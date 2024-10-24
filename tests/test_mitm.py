import threading
import time
from unittest.mock import MagicMock, call

import pytest
from returns.maybe import Nothing
from returns.result import Success

import octotail.mitm
import octotail.utils
from octotail.msg import ProxyLive, WsSub


@pytest.mark.parametrize(
    "lines, subs",
    [
        ([], []),
        (["", " "], []),
        (
            [
                "127.0.0.1:55350 -> WebSocket text message -> alive.github.com:443/foobar",
                "",
                "",
                '{"subscribe":{"eyJjIjoiY2hlY2tfcnVuczozMTczNzQ5NDIwMyIsInQiOjE3MjkyNjIyMDV9":""}}',
                "",
                "",
            ],
            [
                WsSub(
                    url="alive.github.com:443/foobar",
                    subs='{"subscribe":{"eyJjIjoiY2hlY2tfcnVuczozMTczNzQ5NDIwMyIsInQiOjE3MjkyNjIyMDV9":""}}',
                    job_id=31737494203,
                )
            ],
        ),
    ],
)
def test_buffer_state(monkeypatch, lines, subs):
    sut = octotail.mitm.BufferState()
    not_nothings = [
        res.unwrap() for line in lines if (res := sut.process_line(line)) is not Nothing
    ]
    assert not_nothings == subs


@pytest.mark.parametrize(
    "lines, check_liveness_return, tell_calls",
    [
        ([""], Success(True), [call(ProxyLive())]),
        ([""], Success(False), [call("fatal: proxy didn't go live")]),
        (
            [
                "127.0.0.1:55350 -> WebSocket text message -> alive.github.com:443/foobar",
                "",
                "",
                '{"subscribe":{"eyJjIjoiY2hlY2tfcnVuczozMTczNzQ5NDIwMyIsInQiOjE3MjkyNjIyMDV9":""}}',
                "",
                "",
            ],
            Success(True),
            [
                call(ProxyLive()),
                call(
                    WsSub(
                        url="alive.github.com:443/foobar",
                        subs='{"subscribe":{"eyJjIjoiY2hlY2tfcnVuczozMTczNzQ5NDIwMyIsInQiOjE3MjkyNjIyMDV9":""}}',
                        job_id=31737494203,
                        job_name=None,
                    )
                ),
            ],
        ),
    ],
)
def test_proxy_watcher(monkeypatch, lines, check_liveness_return, tell_calls):
    mgr = MagicMock()
    mgr.is_alive.return_value = True
    stop_mock = MagicMock()
    stop_event = threading.Event()
    stop_mock.stop_event.get.return_value = stop_event
    mgr.proxy.return_value = stop_mock

    monkeypatch.setattr(octotail.mitm, "_check_liveness", lambda *_, **__: check_liveness_return)
    monkeypatch.setattr(octotail.mitm, "run_mitmdump", MagicMock())

    sut = octotail.mitm.ProxyWatcher.start(mgr=mgr, port=9182)
    queue = sut.proxy().queue
    for line in lines:
        queue.put(line)

    try:
        thread = threading.Thread(target=lambda: sut.proxy().watch().get())
        thread.start()
        while len(mgr.tell.call_args_list) < len(tell_calls):
            time.sleep(0.001)
        stop_event.set()
    finally:
        sut.stop()

    assert mgr.tell.call_args_list == tell_calls


def test_proxy_watcher_no_manager():
    sut = octotail.mitm.ProxyWatcher(mgr=None, port=9182)
    assert sut.port == 9182
    assert not hasattr(sut, "mgr")
