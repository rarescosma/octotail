#!/usr/bin/env -S /bin/sh -c 'exec "$(dirname $(readlink -f "$0"))/.venv/bin/python3" "$0" "$@"'
"""
Basically a curse.
"""
import asyncio as aio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from typing import Any, Iterable, Tuple, cast

from fake_useragent import UserAgent
from mitmproxy.http import HTTPFlow
from mitmproxy.io import FlowReader
from pyppeteer import launch
from pyppeteer_stealth import stealth
from websockets.client import connect

USER = os.getenv("_GH_USER")
PASS = os.getenv("_GH_PASS")
TOKEN = os.getenv("_GH_TOKEN")
PROXY_FILE = "/tmp/proxy.tmp"
proxy_handle = None

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


def run_mitmproxy() -> None:
    # Create a subprocess
    proxy = subprocess.Popen(
        ["mitmdump", "-w", PROXY_FILE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    global proxy_handle
    proxy_handle = proxy
    proxy.wait()


async def browse_to_action(job_name: str, q: aio.Queue) -> None:
    browser = page = None
    try:
        browser = await launch(
            # headless=False,
            # executablePath="/usr/bin/chromium",
            headless=True,
            options={"args": CHROME_ARGS},
        )

        page = await browser.newPage()
        await stealth(page)

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

        action_url = await q.get()
        await page.goto(action_url)
        link_handle = await page.waitForSelector(f"#workflow-job-name-{job_name}")
        href = await page.evaluate("(element) => element.href", link_handle)
        await page.goto(href)
        await aio.sleep(1)
    finally:
        if page:
            await page.close()
        if browser:
            await browser.close()


async def get_action_url(commit_sha: str, q: aio.Queue) -> None:
    # farm out to gh CLI the listing of a job that matches the SHA
    cmd = f"gh run list -c {commit_sha} --json url,status"

    # "just put a retry loop around it"
    res = None
    for _ in range(0, 10):
        with suppress(Exception):
            res = json.loads(subprocess.check_output(cmd.split(" ")).decode().strip())
            if res is not None and res:
                break
        time.sleep(1)

    if not res:
        sys.exit(0)

    url = res[0]["url"]
    print(f"got action URL: '{url}'")
    await q.put(url)


def url_and_sub(path: str) -> Tuple[str, str]:
    with open(path, "rb") as f:
        reader = FlowReader(f)
        wss = [
            flow
            for flow in cast(Iterable[HTTPFlow], reader.stream())
            if flow.websocket is not None and flow.request.host.startswith("alive.github.com")
        ]
        flow = wss[-1]
        msgs = [m.text for m in flow.websocket.messages if "subscribe" in json.loads(m.text)]
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
        async with connect(ws_url, extra_headers=headers) as websocket:
            await websocket.send(sub)
            async for msg in websocket:
                if is_completed(msg):
                    sys.exit(0)
                print(extract_line(msg))
    except aio.CancelledError:
        print("cancelled")
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
    return False


async def main(commit_sha: str, job_name: str, *_: Any) -> None:
    if commit_sha == len(commit_sha) * "0":
        return

    loop = aio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda *_: sys.exit(0), tuple())

    print(f"processing commit SHA: '{commit_sha}'")

    # 1. boot up mitmproxy in a separate thread
    proxy_thread = threading.Thread(target=run_mitmproxy)
    print("started mitmproxy")
    proxy_thread.start()

    q: aio.Queue[str] = aio.Queue(maxsize=1)

    # 2. use gh CLI to get the action URL
    # 3. log into github via headless browser
    await aio.gather(get_action_url(commit_sha, q), browse_to_action(job_name, q))

    # 4. murder the proxy
    if proxy_handle is not None:
        cast(subprocess.Popen, proxy_handle).send_signal(signal.SIGINT)

    proxy_thread.join()

    # 5. start streaming
    ws_url, ws_sub = url_and_sub(PROXY_FILE)
    print(f"got websocket URL: '{ws_url}'")
    await stream_it(ws_url, ws_sub)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(0)
    aio.run(main(*sys.argv[1:]))
