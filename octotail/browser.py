"""Browsers!"""

import asyncio as aio
import json
import time
from pathlib import Path

from fake_useragent import UserAgent
from pyppeteer import launch
from pyppeteer.browser import Browser
from pyppeteer.page import Page

from .cli import Opts
from .utils import log

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


async def launch_browser(headless: bool) -> Browser:
    return await launch(
        headless=headless,
        executablePath="/usr/bin/chromium",
        options={"args": CHROME_ARGS},
    )


async def login_flow(page: Page, opts: Opts) -> str:
    await page.goto("https://github.com/login")
    await page.waitForSelector("#login_field", timeout=30000)

    await page.type("#login_field", opts.gh_user)
    await page.keyboard.press("Tab")
    await page.type("#password", opts.gh_pass)
    await page.keyboard.press("Enter")
    await page.waitForSelector("#app_totp", timeout=30000)

    await page.type("#app_totp", opts.gh_token)
    await page.keyboard.press("Enter")
    await page.waitForSelector(f"[data-login='{opts.gh_user}']")

    cookies = await page.cookies()
    return json.dumps(cookies)


async def nom_cookies(page: Page, cookie_jar: Path) -> bool:
    if not cookie_jar.exists():
        return False

    cookies = json.loads(cookie_jar.read_text())

    if any(_is_close_to_expiry(c.get("expires", "-1")) for c in cookies):
        log("found a stale cookie :-(")
        return False

    log("all cookies are fresh, nom nom nom")
    await aio.gather(*[page.setCookie(c) for c in cookies])
    return True


def _is_close_to_expiry(ts: str) -> bool:
    _ts, now = float(ts), time.time()
    return _ts > now and (_ts - now) < 24 * 3600
