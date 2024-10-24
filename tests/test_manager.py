import importlib
import threading
import typing as t
from unittest.mock import MagicMock

import github
import pytest

import octotail.manager
import octotail.streamer
from octotail.msg import (
    CloseRequest,
    ExitRequest,
    JobDone,
    OutputItem,
    ProxyLive,
    VisitRequest,
    WorkflowDone,
    WsSub,
)


class WorkflowJob(t.NamedTuple):
    html_url: str
    id: int

    @property
    def name(self) -> str:
        return str(self.id)


@pytest.mark.parametrize(
    "messages, expected_browse_queue, expected_output_queue",
    [
        ([], [], []),
        (["bogus"], [], []),
        ([ProxyLive()], [ProxyLive()], []),
        (
            [
                WorkflowJob(html_url="https://foo.bar", id=123),
                WsSub(url="https://ws.bar", subs="", job_id=123),
                WsSub(url="https://ws.bar", subs="", job_id=345),
            ],
            [
                VisitRequest(url="https://foo.bar", job_id=123),
                CloseRequest(job_id=123),
                CloseRequest(job_id=345),
            ],
            [],
        ),
        (
            [
                WorkflowJob(html_url="https://foo.bar", id=123),
                WsSub(url="https://ws.bar", subs="", job_id=123),
                JobDone(job_id=123, conclusion="yes", job_name="foo"),
                JobDone(job_id=123, conclusion="yes", job_name="foo"),
                WorkflowDone(conclusion="very good"),
            ],
            [
                VisitRequest(url="https://foo.bar", job_id=123),
                CloseRequest(job_id=123),
                ExitRequest(),
            ],
            [
                OutputItem("foo", ["##[conclusion]yes"]),
                OutputItem("foo", ["##[conclusion]yes"]),
                OutputItem("workflow", ["##[conclusion]very good"]),
                None,
            ],
        ),
    ],
)
def test_message_to_queues(
    monkeypatch, mock_queue, messages, expected_browse_queue, expected_output_queue
):
    monkeypatch.setattr(github.WorkflowJob, "WorkflowJob", WorkflowJob)
    monkeypatch.setattr(octotail.streamer, "run_streamer", MagicMock())
    importlib.reload(octotail.manager)

    browse_queue = mock_queue()
    output_queue = mock_queue()
    manager = octotail.manager.Manager.start(browse_queue, output_queue, threading.Event())

    try:
        for msg in messages:
            manager.proxy().on_receive(msg).get()

        assert browse_queue.report() == expected_browse_queue
        assert output_queue.report() == expected_output_queue
    finally:
        manager.stop()
