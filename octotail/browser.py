"""Browser actor."""

import asyncio as aio
import json
import multiprocessing as mp
import time
from collections import deque
from contextlib import suppress
from pathlib import Path
from queue import Empty
from typing import NamedTuple, Union

from fake_useragent import UserAgent
from pyppeteer import launch
from pyppeteer.browser import Browser
from pyppeteer.page import Page
from pyppeteer_stealth import stealth
from xdg.BaseDirectory import xdg_cache_home

from octotail.utils import Opts, log, debug

COOKIE_JAR = Path(xdg_cache_home) / "octotail" / "gh-cookies.json"

CHROME_ARGS = [
    '--cryptauth-http-host ""',
    "--disable-accelerated-2d-canvas",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-browser-side-navigation",
    "--disable-client-side-phishing-detection",
    "--disable-default-apps",
    "--disable-dev-shm-usage",
    "--disable-device-discovery-notifications",
    "--disable-extensions",
    "--disable-features=site-per-process",
    "--disable-hang-monitor",
    "--disable-java",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-setuid-sandbox",
    "--disable-sync",
    "--disable-translate",
    "--disable-web-security",
    "--disable-webgl",
    "--metrics-recording-only",
    "--no-first-run",
    "--safebrowsing-disable-auto-update",
    "--no-sandbox",
    "--enable-automation",
    "--password-store=basic",
    "--use-mock-keychain",
    '--lang="en-US"',
    f'--user-agent="{UserAgent().random}"',
    "--proxy-server=127.0.0.1:8080",
]


class VisitRequest(NamedTuple):
    url: str


class CloseRequest(NamedTuple):
    job_id: int

class ExitRequest:
    pass

type BrowseRequest = Union[VisitRequest, CloseRequest, ExitRequest]


def run_browser(opts: Opts, q: mp.Queue) -> None:
    loop = aio.new_event_loop()
    aio.set_event_loop(loop)
    try:
        loop.run_until_complete(_browser(opts, q))
    except KeyboardInterrupt:
        loop.close()


async def launch_browser(headless: bool) -> Browser:
    return await launch(
        headless=headless,
        executablePath="/usr/bin/chromium",
        options={
            "args": CHROME_ARGS,
            "autoClose": True,
        },
    )


async def _browser(opts: Opts, queue: mp.Queue) -> None:
    browser = None
    tasks = set()
    open_pages = dict()
    in_progress = aio.Event()
    mini_q = deque()

    async def navigate(_browser: Browser, url: str):
        page = await _browser.newPage()
        job_id = int(url.split("/")[-1])
        open_pages[job_id] = page
        await page.goto(url)

    def _schedule(_url):
        in_progress.set()
        task = aio.create_task(navigate(browser, _url))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    browser = await launch_browser(opts.headless)
    start_page = (await browser.pages())[0]
    await stealth(start_page)

    if not await _nom_cookies(start_page, COOKIE_JAR):
        log("logging in to GitHub")
        cookies = await _login_flow(start_page, opts)
        COOKIE_JAR.parent.mkdir(parents=True, exist_ok=True)
        COOKIE_JAR.write_text(cookies)

    while True:
        with suppress(Empty):
            if not in_progress.is_set() and mini_q:
                _schedule(mini_q.pop().url)
                continue

            browse_request = queue.get_nowait()
            if isinstance(browse_request, ExitRequest):
                return await browser.close()
            if isinstance(browse_request, CloseRequest):
                if browse_request.job_id in open_pages:
                    await open_pages[browse_request.job_id].close()
                in_progress.clear()

            if isinstance(browse_request, VisitRequest):
                if in_progress.is_set():
                    mini_q.appendleft(browse_request)
                else:
                    _schedule(browse_request.url)

        await aio.sleep(0.5)


async def _login_flow(page: Page, opts: Opts) -> str:
    await page.goto("https://github.com/login")
    await page.waitForSelector("#login_field", timeout=30000)

    await page.type("#login_field", opts.gh_user)
    await page.keyboard.press("Tab")
    await page.type("#password", opts.gh_pass)
    await page.keyboard.press("Enter")
    await page.waitForSelector("#app_totp", timeout=30000)

    await page.type("#app_totp", opts.gh_otp)
    await page.keyboard.press("Enter")
    await page.waitForSelector(f"[data-login='{opts.gh_user}']")

    cookies = await page.cookies()
    return json.dumps(cookies)


async def _nom_cookies(page: Page, cookie_jar: Path) -> bool:
    if not cookie_jar.exists():
        return False

    cookies = json.loads(cookie_jar.read_text())

    if any(_is_close_to_expiry(c.get("expires", "-1")) for c in cookies):
        debug("found a stale cookie :-(")
        return False

    debug("all cookies are fresh, nom nom nom")
    await aio.gather(*[page.setCookie(c) for c in cookies])
    return True


def _is_close_to_expiry(ts: str) -> bool:
    _ts, now = float(ts), time.time()
    return _ts > now and (_ts - now) < 24 * 3600
