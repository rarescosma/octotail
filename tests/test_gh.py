import threading
import typing as t
from collections import deque
from unittest.mock import MagicMock, PropertyMock, call

import pytest
from returns.io import IOFailure, IOResult, IOResultE, IOSuccess
from returns.result import Failure, Success

from octotail import gh
from octotail.cli import Opts
from octotail.msg import JobDone, WorkflowDone


class MockOpts(t.NamedTuple):
    workflow_name: str | None = None
    ref_name: str | None = None
    gh_pat: str = ""
    commit_sha: str = ""


class MockRun(t.NamedTuple):
    status: str = ""
    name: str = ""
    head_branch: str = ""
    html_url: str = ""


class MockJob(t.NamedTuple):
    id: int = -1
    conclusion: str = ""

    @property
    def name(self) -> str:
        return str(self.id)


@pytest.mark.parametrize(
    ("opts", "input_runs", "output_runs"),
    [
        (MockOpts(), [], []),
        (MockOpts(), [MockRun(status="queued")], [MockRun(status="queued")]),
        (MockOpts(), [MockRun(status="fried")], []),
        (MockOpts(workflow_name="foobar"), [MockRun(status="queued")], []),
        (
            MockOpts(workflow_name="foobar"),
            [
                MockRun(status="queued", name="foobar"),
                MockRun(status="in_progress", name="not-foobar"),
            ],
            [MockRun(status="queued", name="foobar")],
        ),
        (
            MockOpts(ref_name="refs/tags/v1.0.0"),
            [
                MockRun(status="queued", head_branch="v1.0.0"),
                MockRun(status="in_progress", head_branch="tags/v1.0.0"),
                MockRun(status="in_progress", head_branch="foobar"),
            ],
            [
                MockRun(status="queued", head_branch="v1.0.0"),
                MockRun(status="in_progress", head_branch="tags/v1.0.0"),
            ],
        ),
    ],
)
def test_filter_runs(opts: Opts, input_runs, output_runs):
    assert gh._filter_runs(opts, input_runs) == output_runs


@pytest.mark.parametrize(
    ("list_results", "expected"),
    [
        ([IOFailure("foo")], Failure("foo")),
        ([IOSuccess([])] * 20, Failure(RuntimeError("retries exceeded"))),
        (
            [IOSuccess([MockRun(status="fried")])] * 20,
            Failure(RuntimeError("retries exceeded")),
        ),
        (
            [IOSuccess([MockRun(status="fried")])] * 5
            + [IOSuccess([MockRun(status="in_progress")])],
            Success(MockRun(status="in_progress")),
        ),
        (
            [
                IOSuccess(
                    [
                        MockRun(status="in_progress", name="foo"),
                        MockRun(status="in_progress", name="bar"),
                    ]
                )
            ],
            Failure(RuntimeError("cannot disambiguate")),
        ),
    ],
)
def test_get_active_run(list_results, expected):
    deq = deque(list_results)

    def _run_lister(*_, **__):
        return deq.popleft()

    client = MagicMock()
    client.get_repo.return_value = IOSuccess("repo-id")
    client.get_workflow_runs = _run_lister

    got = gh.get_active_run("repo-id", t.cast(Opts, MockOpts()), client=client, retry_delay=0)
    if isinstance(expected, Failure):
        assert isinstance(got.failure(), type(expected.failure()))
        assert str(got.failure()).startswith(str(expected.failure()))
    else:
        assert got == expected


@pytest.mark.parametrize(
    ("batches", "result_batches"),
    [
        ([], []),
        (
            [[MockJob(conclusion="yes", id=123)]],
            [[JobDone(job_id=123, job_name="123", conclusion="yes")]],
        ),
        (
            [
                [MockJob(conclusion="yes", id=123)],
                [MockJob(conclusion="yes", id=123)],
            ],
            [
                [JobDone(job_id=123, job_name="123", conclusion="yes")],
                [],
            ],
        ),
        (
            [
                [MockJob(conclusion="yes", id=123), MockJob(id=456)],
                [MockJob(conclusion="yes", id=123), MockJob(id=456)],
                [MockJob(conclusion="yes", id=123), MockJob(id=456, conclusion="yes")],
            ],
            [
                [JobDone(job_id=123, job_name="123", conclusion="yes"), MockJob(id=456)],
                [],
                [JobDone(job_id=456, job_name="456", conclusion="yes")],
            ],
        ),
    ],
)
def test_job_state(batches, result_batches):
    assert len(batches) == len(result_batches)
    sut = gh.JobState.default()
    for batch, result in zip(batches, result_batches, strict=False):
        assert sut.diff(batch) == result


@pytest.mark.parametrize(
    ("stop_is_set", "jobs_res", "wf_conclusions", "mgr_is_alive", "tell_calls"),
    [
        (False, IOResult.from_failure(RuntimeError("nope!")), [], True, []),
        (
            False,
            IOSuccess([MockJob(conclusion="yes", id=123)]),
            ["", "wf-success!"],
            True,
            [
                call(JobDone(job_id=123, job_name="123", conclusion="yes")),
                call(WorkflowDone(conclusion="wf-success!")),
            ],
        ),
        (
            False,
            IOSuccess([MockJob(conclusion="yes", id=123)]),
            ["", "wf-success!"],
            False,
            [],
        ),
        (True, None, None, True, []),
    ],
)
def test_run_watcher(
    stop_is_set: bool,
    jobs_res: IOResultE,
    wf_conclusions: list[str],
    mgr_is_alive: bool,
    tell_calls: list[call],
):
    mgr = MagicMock()
    mgr.is_alive.return_value = mgr_is_alive
    stop_mock = MagicMock()
    stop_event = threading.Event()
    if stop_is_set:
        stop_event.set()
    stop_mock.stop_event.get.return_value = stop_event
    mgr.proxy.return_value = stop_mock

    client = MagicMock()
    client.get_workflow_jobs.return_value = jobs_res
    client.poll_interval.return_value = 0

    wf_run = MagicMock()
    type(wf_run).conclusion = PropertyMock(side_effect=wf_conclusions)
    sut = gh.RunWatcher.start(mgr=mgr, wf_run=wf_run, client=client)

    try:
        thread = threading.Thread(target=lambda: sut.proxy().watch().get())
        thread.start()
        thread.join()
    finally:
        sut.stop()

    assert mgr.tell.call_args_list == tell_calls
