#!/usr/bin/env -S /bin/sh -c 'exec "$(dirname $(readlink -f "$0"))/../.venv/bin/python3" "$0" "$@"'
"""
Have the cake and tail it too.
"""
import asyncio as aio
import json
import os
import sys
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Optional

import typer
import websockets.client
from pyppeteer.browser import Browser
from pyppeteer.page import Page
from pyppeteer_stealth import stealth
from xdg.BaseDirectory import xdg_cache_home, xdg_data_home

from .browser import WS_HEADERS, launch_browser, login_flow, nom_cookies
from .cli import Opts, cli
from .mitm import get_websocket, start_proxy
from .utils import Ok, Result, Retry, log, retries, run_cmd

COOKIE_JAR = Path(xdg_cache_home) / "octotail" / "gh-cookies.json"
PROXY_FILE = Path(xdg_data_home) / "octotail" / "proxy.out"
DEBUG = os.getenv("DEBUG") not in ["0", "false", "False", None]


async def browse_to_action(q: aio.Queue, opts: Opts) -> RuntimeError | None:
    browser = page = None

    async def cleanup(_page: Page, _browser: Browser) -> None:
        if _page:
            await _page.close()
        if _browser:
            await _browser.close()

    try:
        browser = await launch_browser(opts.headless)

        page = await browser.newPage()
        await stealth(page)

        if not await nom_cookies(page, COOKIE_JAR):
            log("logging in to GitHub")
            cookies = await login_flow(page, opts)
            COOKIE_JAR.parent.mkdir(parents=True, exist_ok=True)
            COOKIE_JAR.write_text(cookies)

        run_url = await q.get()
        if isinstance(run_url, RuntimeError):
            return run_url

        await page.goto(run_url)
        spinner = await page.waitForSelector(".WorkflowJob-title .anim-rotate")
        href = await page.evaluate(
            """ (element) => { 
                let p = element; 
                while (!p.hasAttribute('href')) { p = p.parentNode; }; 
                return p.href; } """,
            spinner,
        )
        await page.goto(href)
        await page.waitForSelector(".js-socket-channel[data-job-status='in_progress']")
        await aio.sleep(1)
    finally:
        await cleanup(page, browser)

    return None


@retries(10, 0.5)
async def get_run_url(opts: Opts) -> Result[str]:
    valid_statuses = ["queued", "in_progress", "requested", "waiting", "action_required"]
    # farm out to gh CLI the listing of a run that matches the SHA
    list_cmd = f"gh run list -c {opts.commit_sha} -w {opts.workflow} --json url,status,databaseId"

    out, err, ret_code = await run_cmd(list_cmd)
    if ret_code != 0:
        log(f"gh error: {err.decode().strip()}")
        return Retry()

    with suppress(Exception):
        gh_run = json.loads(out.decode().strip())[0]

        if not gh_run.get("status") in valid_statuses:
            log(f"cannot process run in state: '{gh_run.get("status")}'")
            log(f"try:\n\n\tgh run view {gh_run.get("databaseId")} --log\n")
            log(f"or try browsing to:\n\n\t{gh_run.get("url")}\n")
            return RuntimeError("invalid action state")

        log(f"got run url: {gh_run["url"]}")
        return Ok(gh_run["url"])

    if DEBUG:
        log(f"gh out: {out.decode()}; gh err: {err.decode()}")
    return Retry()


async def stream_it(url: str, sub: str) -> None:
    ws_url = "wss://" + url.removeprefix("https://")

    websocket = None
    try:
        async with websockets.client.connect(ws_url, extra_headers=WS_HEADERS) as websocket:
            await websocket.send(sub)
            async for msg in websocket:
                if (conclusion := is_completed(msg)) is not None:
                    print(conclusion)
                    return
                print(extract_line(msg))
    except aio.CancelledError:
        log("cancelled")
        if websocket:
            await websocket.close()


def extract_line(x: str) -> str:
    with suppress(Exception):
        return "\n".join(l["line"] for l in json.loads(x)["data"]["data"]["lines"])
    return ""


def is_completed(x: str) -> Optional[str]:
    with suppress(Exception):
        data = json.loads(x)["data"]
        if data["status"] == "completed" and "conclusion" in data:
            return f'job completed; conclusion: {data["conclusion"]}'
    # noinspection PyUnreachableCode
    return None


@cli
async def main(opts: Opts) -> None:
    log(f"processing commit SHA: '{opts.commit_sha}'")

    # 1. boot up mitmproxy in a separate thread
    _kill_proxy = await start_proxy(PROXY_FILE)

    # 2. use gh CLI to get the action URL
    # 3. log into github via headless browser
    url_q: aio.Queue[str | Exception] = aio.Queue(maxsize=1)
    abort = False
    for ret in await aio.gather(
        partial(get_run_url, url_q)(opts),  # pylint: disable=E1121
        browse_to_action(url_q, opts),
        return_exceptions=True,
    ):
        if isinstance(ret, Exception):
            log(f"got exception: {ret}")
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
        log(f"could not extract websockets URL or subs from {PROXY_FILE}")


def _main() -> None:
    typer.run(main)


if __name__ == "__main__":
    _main()
