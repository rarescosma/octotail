"""Browser actor."""

import asyncio as aio
import json
import multiprocessing as mp
import time
import typing as t
from collections import deque
from contextlib import suppress
from multiprocessing.queues import Queue
from pathlib import Path
from queue import Empty

from pykka import ActorRef, ThreadingActor
from pyppeteer import launch
from pyppeteer.browser import Browser
from pyppeteer.page import Page
from pyppeteer_stealth import stealth
from xdg.BaseDirectory import xdg_cache_home

from octotail.cli import Opts
from octotail.manager import Manager
from octotail.msg import BrowseRequest, CloseRequest, ExitRequest, ProxyLive, VisitRequest
from octotail.utils import RANDOM_UA, debug, log

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
    f'--user-agent="{RANDOM_UA}"',
]


type Cookies = list[dict[str, t.Any]]


class BrowserWatcher(ThreadingActor):
    """Runs the pyppeteer browser in a separate process."""

    opts: Opts
    inbox: Queue[BrowseRequest]
    mgr: ActorRef[Manager]

    def __init__(self, mgr: ActorRef[Manager], opts: Opts, inbox: Queue[BrowseRequest]):
        super().__init__()
        self.opts = opts
        self.inbox = inbox
        self.mgr = mgr

    def watch(self, target: t.Callable[[Opts, Queue[BrowseRequest]], None]) -> None:
        browser = mp.Process(target=target, args=(self.opts, self.inbox))
        browser.start()
        browser.join()
        self.mgr.stop()
        debug("exiting")


def start_controller(opts: Opts, inbox: Queue[BrowseRequest]) -> None:  # pragma: no cover
    loop = aio.new_event_loop()
    aio.set_event_loop(loop)
    try:
        browser = loop.run_until_complete(_launch_browser(opts))
        loop.run_until_complete(
            _controller(
                browser,
                opts=opts,
                inbox=inbox,
                cookie_jar=CookieJar(opts.gh_user),
            )
        )
    except KeyboardInterrupt:
        loop.close()


async def _launch_browser(opts: Opts) -> Browser:
    return await launch(
        headless=opts.headless,
        executablePath="/usr/bin/chromium",
        options={
            "args": [*CHROME_ARGS, f"--proxy-server=127.0.0.1:{opts.port}"],
            "autoClose": False,
            "handleSIGINT": False,
        },
    )


class CookieJar(t.NamedTuple):
    """Provides read/write access to user-scoped cookies."""

    user: str
    path: Path = COOKIE_JAR

    def save(self, cookies: Cookies) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.path.write_text(json.dumps({**existing, self.user: cookies}))

    def read(self) -> Cookies | None:
        if not self.path.exists():
            return None
        return t.cast(Cookies, json.loads(self.path.read_text()).get(self.user))


async def _controller(
    browser: Browser,
    opts: Opts,
    inbox: Queue[BrowseRequest],
    cookie_jar: CookieJar,
    sleep_time: float = 0.5,
) -> None:
    tasks = set()
    open_pages: dict[int, Page] = {}
    ready = aio.Event()
    visit_queue: deque[VisitRequest] = deque()

    def _schedule_visit(_visit_req: VisitRequest) -> None:
        ready.clear()
        _task = aio.create_task(_visit(_visit_req))
        tasks.add(_task)
        _task.add_done_callback(tasks.discard)

    async def _visit(_visit_req: VisitRequest) -> None:
        _page = await browser.newPage()
        open_pages[_visit_req.job_id] = _page
        await _page.goto(_visit_req.url, timeout=0)

    start_page = (await browser.pages())[0]
    await stealth(start_page)

    # buffer visit requests until the proxy goes live
    while True:
        with suppress(Empty):
            match inbox.get_nowait():
                case VisitRequest() as visit_req:
                    visit_queue.appendleft(visit_req)
                case ProxyLive():
                    ready.set()
                    break
        await aio.sleep(sleep_time)

    if not await _nom_cookies(cookie_jar.read(), start_page):
        log("logging in to GitHub")
        cookies = await _login_flow(start_page, opts)
        if isinstance(cookies, RuntimeError):
            log(f"fatal: {cookies}")
            await browser.close()
            return
        cookie_jar.save(cookies)

    while True:
        with suppress(Empty):
            if ready.is_set() and visit_queue:
                _schedule_visit(visit_queue.pop())
                continue

            match inbox.get_nowait():
                case ExitRequest():
                    await browser.close()
                    return

                case CloseRequest() as close_req:
                    if close_req.job_id in open_pages:
                        await open_pages[close_req.job_id].close()
                    ready.set()

                case VisitRequest() as visit_req:
                    if ready.is_set():
                        _schedule_visit(visit_req)
                    else:
                        visit_queue.appendleft(visit_req)
        await aio.sleep(sleep_time)


async def _login_flow(page: Page, opts: Opts) -> Cookies | RuntimeError:
    await page.goto("https://github.com/login")

    await page.waitForSelector("#login_field")
    await page.type("#login_field", opts.gh_user)
    await page.keyboard.press("Tab")

    await page.type("#password", opts.gh_pass)
    await page.keyboard.press("Enter")

    el = await page.waitForSelector(f"#app_totp, [data-login='{opts.gh_user}']")
    el_id = await page.evaluate("(element) => element.id", el)
    if el_id == "app_totp":
        if opts.gh_otp is None:
            return RuntimeError(
                "GitHub requested OTP authentication, but no OTP token was provided"
            )
        await el.type(opts.gh_otp)
        await page.keyboard.press("Enter")
        await page.waitForSelector(f"[data-login='{opts.gh_user}']")

    cookies = await page.cookies()
    await page.goto("about:blank")
    return t.cast(Cookies, cookies)


async def _nom_cookies(cookies: Cookies | None, page: Page) -> bool:
    if cookies is None or not cookies:
        return False

    if any(_is_close_to_expiry(c.get("expires", "-1")) for c in cookies):
        debug("found a stale cookie :-(")
        return False

    debug("all cookies are fresh, nom nom nom")
    await aio.gather(*[page.setCookie(c) for c in cookies])
    return True


def _is_close_to_expiry(ts: str) -> bool:
    _ts, now = float(ts), time.time()
    return _ts > now and (_ts - now) < 24 * 3600
