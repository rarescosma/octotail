import asyncio
import asyncio as aio
import json
import multiprocessing.dummy
import threading
import time
import typing as t
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import octotail.browser
from octotail.browser import (
    BrowserWatcher,
    CookieJar,
    _controller,
    _launch_browser,
    _login_flow,
    _nom_cookies,
)
from octotail.cli import Opts
from octotail.msg import CloseRequest, ExitRequest, ProxyLive, VisitRequest


@pytest.mark.asyncio
async def test_simple():
    await asyncio.sleep(0.5)


def test_calls_mgr_stop_on_early_exit(monkeypatch):
    monkeypatch.setattr(octotail.browser, "mp", multiprocessing.dummy)
    mgr = MagicMock()

    sut = BrowserWatcher.start(mgr, "mock_opts", "mock_inbox")

    mock_target = MagicMock()

    def _start_watch():
        sut.proxy().watch(target=mock_target).get()

    try:
        thread = threading.Thread(target=_start_watch)
        thread.start()
    finally:
        sut.stop()

    mgr.stop.assert_called_once()
    mock_target.assert_called_once_with("mock_opts", "mock_inbox")


@pytest.mark.asyncio
async def test_launch_gets_a_proxy_argument(monkeypatch):
    mock_launch = AsyncMock()
    monkeypatch.setattr(octotail.browser, "launch", mock_launch)
    await _launch_browser(t.cast(Opts, Namespace(headless="a_bogus_value", port=12345)))
    args = json.dumps(mock_launch.call_args_list[0].kwargs)
    assert "a_bogus_value" in args
    assert "--proxy-server=127.0.0.1:12345" in args


def test_cookie_jar(tmp_path):
    jar_path = tmp_path / "cookies"
    jar1 = CookieJar(path=jar_path, user="foo")
    jar2 = CookieJar(path=jar_path, user="bar")

    assert jar1.read() is None
    assert jar2.read() is None

    jar1.save([{"mmmm": "cookies"}])
    assert jar1.read() == [{"mmmm": "cookies"}]
    assert jar2.read() is None

    jar2.save([{"mmmmmm": "different cookies"}])
    assert jar1.read() == [{"mmmm": "cookies"}]
    assert jar2.read() == [{"mmmmmm": "different cookies"}]


@pytest.mark.parametrize(
    "cookies, expected, set_cookies",
    [
        ([], False, []),
        ([{"oops": "i am close to expiry", "expires": int(time.time()) + 3600}], False, []),
        (
            [{"mmmm": "cooookies", "expires": int(time.time()) + 25 * 3600}],
            True,
            [{"mmmm": "cooookies", "expires": int(time.time()) + 25 * 3600}],
        ),
        (None, False, []),
    ],
)
@pytest.mark.asyncio
async def test_nom_cookies(cookies, expected, set_cookies):
    page = AsyncMock()
    assert await _nom_cookies(cookies, page) == expected
    page.setCookie.assert_has_calls([call(s) for s in set_cookies], any_order=True)


@pytest.mark.parametrize(
    "opts, otp_enabled, expected",
    [
        (
            Namespace(gh_user="foo", gh_pass="bar"),
            False,
            "##success",
        ),
        (
            Namespace(gh_user="foo", gh_pass="bar", gh_otp=None),
            True,
            RuntimeError("GitHub requested OTP authentication, but no OTP token was provided"),
        ),
        (
            Namespace(gh_user="foo", gh_pass="bar", gh_otp="yepp, i have an OTP"),
            True,
            "##success",
        ),
    ],
)
@pytest.mark.asyncio
async def test_login_flow(opts: Opts, otp_enabled: bool, expected):
    page = AsyncMock()
    page.cookies.return_value = "##success"
    if otp_enabled:
        page.evaluate.return_value = "app_totp"

    got = await _login_flow(page, opts)

    if isinstance(expected, Exception):
        assert isinstance(got, type(expected))
        assert str(got).startswith(str(expected))
    else:
        assert got == expected


@pytest.mark.parametrize(
    "opts, jar_cookies, page_cookies, close_called",
    [
        (Namespace(gh_user="foo", gh_pass="bar"), [], "##success", False),
        (Namespace(gh_user="foo", gh_pass="bar"), [], RuntimeError("nope"), True),
        (
            Namespace(gh_user="foo", gh_pass="bar"),
            [{"look": "a cookie"}],
            RuntimeError("nope"),  # doesn't matter, because we have cookie jars
            False,
        ),
    ],
)
@pytest.mark.asyncio
async def test_controller_login_flow(
    monkeypatch, tmp_path, mock_queue, opts: Opts, jar_cookies, page_cookies, close_called
):
    async def _noop(*_, **__):
        pass

    monkeypatch.setattr(octotail.browser, "stealth", _noop)
    browser = AsyncMock()
    start_page = AsyncMock()
    start_page.cookies.return_value = page_cookies
    browser.pages.return_value = [start_page]
    jar_path = tmp_path / "cookies"
    inbox = mock_queue([ProxyLive()])
    cookie_jar = CookieJar(path=jar_path, user="foo")
    cookie_jar.save(jar_cookies)

    sut = _controller(
        browser,
        opts=opts,
        inbox=inbox,
        cookie_jar=cookie_jar,
        sleep_time=0.00000001,
    )

    async def _exit():
        await aio.sleep(0.1)
        if close_called:
            browser.close.assert_called()
        else:
            browser.close.assert_not_called()
        inbox.put_nowait(ExitRequest())

    await aio.gather(sut, _exit())


@pytest.mark.parametrize(
    "inbox_items,",
    [
        ["bogus_message", ProxyLive()],
        [ProxyLive()],
        [
            ProxyLive(),
            VisitRequest(url="foo", job_id=1),
            VisitRequest(url="bar", job_id=2),
            "bogus_item",
            VisitRequest(url="baz", job_id=3),
            CloseRequest(job_id=1),
            VisitRequest(url="heh", job_id=4),
            CloseRequest(job_id=2),
            CloseRequest(job_id=3),
            VisitRequest(url="nop", job_id=5),
            CloseRequest(job_id=4),
            CloseRequest(job_id=5),
        ],
        [
            VisitRequest(url="foo", job_id=1),
            VisitRequest(url="bar", job_id=2),
            "bogus_item",
            ProxyLive(),
            CloseRequest(job_id=1),
            VisitRequest(url="baz", job_id=3),
            CloseRequest(job_id=2),
            CloseRequest(job_id=3),
        ],
    ],
)
@pytest.mark.asyncio
async def test_controller_visits_pages_one_at_a_time(
    monkeypatch, tmp_path, mock_queue, inbox_items
):
    async def _noop(*_, **__):
        pass

    monkeypatch.setattr(octotail.browser, "stealth", _noop)
    browser = AsyncMock()
    browser.pages.return_value = [AsyncMock()]
    inbox = mock_queue([*inbox_items, ExitRequest()])
    cookie_jar = CookieJar(path=(tmp_path / "cookies"), user="foo")
    cookie_jar.save([{"yes": "cookies", "expires": int(time.time()) + 100 * 3600}])

    open_pages = 0

    async def _goto(*_, **__):
        nonlocal open_pages
        assert open_pages == 0
        open_pages += 1

    async def _close():
        nonlocal open_pages
        assert open_pages == 1
        open_pages -= 1

    async def make_page():
        ret = AsyncMock()
        ret.goto = _goto
        ret.close = _close
        return ret

    browser.newPage = make_page

    sut = _controller(
        browser,
        opts=t.cast(Opts, None),
        inbox=inbox,
        cookie_jar=cookie_jar,
        sleep_time=0.00000001,
    )

    await sut
