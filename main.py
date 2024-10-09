#!/usr/bin/env -S /bin/sh -c 'exec "$(dirname $(readlink -f "$0"))/.venv/bin/python3" "$0" "$@"'
"""
Basically a curse.
"""
import asyncio as aio
import inspect
import json
import multiprocessing
import os
import signal
import socket
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Iterable, Optional, Tuple, cast

import websockets.client
from fake_useragent import UserAgent
from mitmproxy.http import HTTPFlow
from mitmproxy.io import FlowReader
from mitmproxy.tools.main import mitmdump
from pyppeteer import launch
from pyppeteer.browser import Browser
from pyppeteer.page import Page
from pyppeteer_stealth import stealth
from xdg.BaseDirectory import xdg_cache_home, xdg_data_home

USER = os.getenv("_GH_USER")
PASS = os.getenv("_GH_PASS")
TOKEN = os.getenv("_GH_TOKEN")
PROXY_FILE = Path(xdg_data_home) / "action-cat" / "proxy.out"
COOKIE_JAR = Path(xdg_cache_home) / "action-cat" / "gh-cookies.json"
DEBUG = bool(os.getenv("DEBUG"))
HEADLESS = os.getenv("_HEADLESS") != "0"

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


def _log(msg: str) -> None:
    fn = inspect.stack()[1].function
    print(f"[{fn}]: {msg}")


def run_mitmproxy() -> None:
    PROXY_FILE.parent.mkdir(parents=True, exist_ok=True)
    sys.argv = f"mitmdump -q -w {PROXY_FILE}".split()
    mitmdump()


async def check_proxy(proxy_ps: multiprocessing.Process) -> bool:
    for _ in range(0, 50):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        res = sock.connect_ex(("127.0.0.1", 8080))
        sock.close()
        if res == 0:
            return True
        await aio.sleep(0.2)
        if not proxy_ps.is_alive():
            return False
    return False


async def browse_to_action(job_name: str, q: aio.Queue) -> RuntimeError | None:
    browser = page = None

    async def cleanup(_page: Page, _browser: Browser) -> None:
        if _page:
            await _page.close()
        if _browser:
            await _browser.close()

    try:
        browser = await launch(
            headless=HEADLESS,
            executablePath="/usr/bin/chromium",
            options={"args": CHROME_ARGS},
        )

        page = await browser.newPage()
        await stealth(page)

        if not await nom_cookies(page):
            _log("logging in to GitHub")
            await page.goto("https://github.com/login")
            await page.waitForSelector("#login_field", timeout=30000)

            await page.type("#login_field", USER)
            await page.keyboard.press("Tab")
            await page.type("#password", PASS)
            await page.keyboard.press("Enter")
            await page.waitForSelector("#app_totp", timeout=30000)

            await page.type("#app_totp", TOKEN)
            await page.keyboard.press("Enter")
            await page.waitForSelector(f"[data-login='{USER}']")

            cookies = await page.cookies()
            COOKIE_JAR.parent.mkdir(parents=True, exist_ok=True)
            COOKIE_JAR.write_text(json.dumps(cookies))

        action_url = await q.get()
        if isinstance(action_url, RuntimeError):
            return action_url

        await page.goto(action_url)
        link_handle = await page.waitForSelector(f"#workflow-job-name-{job_name}")
        href = await page.evaluate("(element) => element.href", link_handle)
        await page.goto(href)
        await aio.sleep(1)
    finally:
        await cleanup(page, browser)

    return None


async def nom_cookies(page: Page) -> bool:
    if not COOKIE_JAR.exists():
        return False

    cookies = json.loads(COOKIE_JAR.read_text())

    if any(is_close_to_expiry(c.get("expires", "-1")) for c in cookies):
        _log("found a stale cookie :-(")
        return False

    _log("all cookies are fresh, nom nom nom")
    await aio.gather(*[page.setCookie(c) for c in cookies])
    return True


def is_close_to_expiry(ts: str) -> bool:
    _ts, now = float(ts), time.time()
    return _ts > now and (_ts - now) < 24 * 3600


async def get_action_url(commit_sha: str, q: aio.Queue) -> None:
    # farm out to gh CLI the listing of a job that matches the SHA
    cmd = f"gh run list -c {commit_sha} --json url,status,databaseId"

    # "just put a retry loop around it"
    for _ in range(0, 10):
        gh = await aio.create_subprocess_shell(
            cmd,
            stdout=aio.subprocess.PIPE,
            stderr=aio.subprocess.PIPE,
        )
        out, err = await gh.communicate()
        if gh.returncode != 0:
            _log(f"gh error: {err.decode().strip()}")
            await aio.sleep(1)
            continue

        with suppress(Exception):
            decoded = json.loads(out.decode().strip())[0]
            if not decoded.get("status") in [
                "queued",
                "in_progress",
                "requested",
                "waiting",
                "action_required",
            ]:
                _log(f"cannot process action in state: '{decoded.get("status")}'")
                _log(f"try:\n\n\tgh run view {decoded.get("databaseId")} --log\n")
                _log(f"or try browsing to:\n\n\t{decoded.get("url")}\n")
                return await q.put(RuntimeError("invalid action state"))

            url = decoded["url"]
            _log(f"success: {url}")
            return await q.put(url)
        if DEBUG:
            _log(f"gh out: {out.decode()}; gh err: {err.decode()}")

        await aio.sleep(1)

    _log("giving up")
    await q.put(RuntimeError("exceeded gh retries"))


def get_websocket(path: Path) -> Optional[Tuple[str, str]]:
    if not path.exists() or not path.is_file():
        return None

    with open(path, "rb") as f:
        reader = FlowReader(f)
        wss = [
            flow
            for flow in cast(Iterable[HTTPFlow], reader.stream())
            if flow.websocket is not None and flow.request.host.startswith("alive.github.com")
        ]
        if not wss:
            return None
        flow = wss[-1]
        msgs = [m.text for m in flow.websocket.messages if "subscribe" in json.loads(m.text)]
        if not msgs:
            return None
        _log(f"success: '{flow.request.url}'")
        return flow.request.url, msgs[0]


async def stream_it(url: str, sub: str) -> None:
    ws_url = "wss://" + url.removeprefix("https://")

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Origin": "https://github.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "websocket",
        "Sec-Fetch-Site": "same-site",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }

    websocket = None
    try:
        async with websockets.client.connect(ws_url, extra_headers=headers) as websocket:
            await websocket.send(sub)
            async for msg in websocket:
                if is_completed(msg):
                    return
                print(extract_line(msg))
    except aio.CancelledError:
        _log("cancelled")
        if websocket:
            await websocket.close()


def extract_line(x: str) -> str:
    with suppress(Exception):
        return "\n".join(l["line"] for l in json.loads(x)["data"]["data"]["lines"])
    return ""


def is_completed(x: str) -> bool:
    with suppress(Exception):
        dec = json.loads(x)
        return dec["data"]["status"] == "completed" and "conclusion" in dec["data"]
    # noinspection PyUnreachableCode
    return False


async def main() -> None:
    # FIXME - better env and arg error handling
    if len(sys.argv) < 3:
        sys.exit(0)

    (commit_sha, job_name) = sys.argv[1:3]
    if commit_sha == len(commit_sha) * "0":
        return

    loop = aio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda *_: sys.exit(0), tuple())
    loop.add_signal_handler(signal.SIGTERM, lambda *_: sys.exit(0), tuple())

    _log(f"processing commit SHA: '{commit_sha}'")

    # 1. boot up mitmproxy in a separate thread
    _log("starting mitmproxy")
    proxy_ps = multiprocessing.Process(target=run_mitmproxy)
    proxy_ps.start()

    def _kill_proxy() -> None:
        proxy_ps.terminate()
        proxy_ps.join()

    if not await check_proxy(proxy_ps):
        _log("proxy didn't go live; bailing...")
        _kill_proxy()
        sys.exit(1)

    # 2. use gh CLI to get the action URL
    # 3. log into github via headless browser
    url_q: aio.Queue[str | Exception] = aio.Queue(maxsize=1)
    abort = False
    for ret in await aio.gather(
        get_action_url(commit_sha, url_q), browse_to_action(job_name, url_q), return_exceptions=True
    ):
        if isinstance(ret, Exception):
            _log(f"got exception: {ret}")
            abort = True

    # 4. murder the proxy
    _kill_proxy()
    if abort:
        sys.exit(1)

    # 5. start streaming
    if (url_sub := get_websocket(PROXY_FILE)) is not None:
        ws_url, ws_sub = url_sub
        await stream_it(ws_url, ws_sub)
    else:
        _log(f"could not extract websockets URL or subs from {PROXY_FILE}")


if __name__ == "__main__":
    aio.run(main())
