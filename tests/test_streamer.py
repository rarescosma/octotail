import asyncio as aio
import json
from copy import deepcopy
from unittest.mock import MagicMock

import pytest
import websockets.client
from websockets.exceptions import ConnectionClosedError

import octotail.streamer
from octotail.msg import OutputItem, WebsocketClosed, WsSub


class MockAiter:
    out_vals: list
    in_vals: list

    def __init__(self, values):
        self.out_vals = values
        self.in_vals = []

    async def __aiter__(self):
        for val in self.out_vals:
            if isinstance(val, Exception):
                raise val
            else:
                yield val

    async def send(self, what):
        self.in_vals.append(what)

    def report(self):
        if not self.in_vals:
            rep = []
            for child in self.out_vals:
                if isinstance(child, type(self)):
                    rep.append(child.report())
            return rep

        return deepcopy(self.in_vals)


def _pack_lines(lines: list[str]) -> str:
    return json.dumps({"data": {"data": {"lines": [{"line": _} for _ in lines]}}})


@pytest.mark.parametrize(
    "ws, a_iter, expected_queue, expected_url, expected_a_iter_report",
    [
        (
            WsSub(url="", subs="sub_message", job_id=123),
            MockAiter(
                [
                    MockAiter([ConnectionClosedError(None, None)]),
                    MockAiter([_pack_lines(["foo"])]),
                    MockAiter([aio.CancelledError()]),
                ]
            ),
            [
                WebsocketClosed(),
                OutputItem(job_name="unknown", lines=["foo"]),
            ],
            "wss://",
            [
                ["sub_message"],
                ["sub_message"],
                ["sub_message"],
            ],
        ),
        (
            WsSub(url="https://foo.bar", subs="sub_message", job_id=123, job_name="silly-job"),
            MockAiter(
                [
                    MockAiter(
                        [
                            _pack_lines(["foo", "bar"]),
                            _pack_lines(["baz"]),
                        ]
                    ),
                ]
            ),
            [
                OutputItem(job_name="silly-job", lines=["foo", "bar"]),
                OutputItem(job_name="silly-job", lines=["baz"]),
            ],
            "wss://foo.bar",
            [
                ["sub_message"],
            ],
        ),
    ],
)
def test_streamer(
    monkeypatch, mock_queue, ws, a_iter, expected_queue, expected_url, expected_a_iter_report
):
    a_iter_factory = MagicMock()
    a_iter_factory.return_value = a_iter
    monkeypatch.setattr(websockets.client, "connect", a_iter_factory)

    q = mock_queue()

    octotail.streamer._streamer(ws, q)

    assert q.report() == expected_queue
    assert a_iter_factory.call_args_list[0].args == (expected_url,)
    assert a_iter.report() == expected_a_iter_report
