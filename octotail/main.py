#!/usr/bin/env -S /bin/sh -c 'exec "$(dirname $(readlink -f "$0"))/../.venv/bin/python3" "$0" "$@"'

"""Having your cake and eating it too."""
import dataclasses
import multiprocessing as mp
import sys
from pathlib import Path
from threading import Event
from typing import Dict, Union

import typer
from github import Auth, Github
from github.WorkflowJob import WorkflowJob
from github.WorkflowRun import WorkflowRun
from pykka import ActorRegistry, ThreadingActor
from xdg.BaseDirectory import xdg_cache_home

from octotail.browser import (
    BrowseRequest,
    BrowserWatcher,
    CloseRequest,
    ExitRequest,
    VisitRequest,
)
from octotail.fmt import Formatter
from octotail.gh import JobDone, RunWatcher, WorkflowDone, get_active_run, guess_repo
from octotail.mitm import ProxyWatcher, WsSub
from octotail.streamer import OutputItem, run_streamer
from octotail.utils import Opts, cli, debug, find_free_port, log

COOKIE_JAR = Path(xdg_cache_home) / "octotail" / "gh-cookies.json"

type MgrMessage = Union[WorkflowJob, WsSub, JobDone, WorkflowDone]


class Manager(ThreadingActor):
    """I'm the Baahwss."""

    browse_queue: mp.Queue
    output_queue: mp.JoinableQueue
    stop_event: Event

    background_tasks: Dict[int, mp.Process]
    job_map: Dict[int, str]

    def __init__(self, browse_queue: mp.Queue, output_queue: mp.JoinableQueue, stop: Event):
        super().__init__()
        self.browse_queue = browse_queue
        self.output_queue = output_queue
        self.stop_event = stop

        self.background_tasks = {}
        self.job_map = {}

    def on_receive(self, msg: MgrMessage) -> None:
        debug(f"manager got message: {msg!r}")

        match msg:
            case WorkflowJob() as job:
                self.browse_queue.put_nowait(VisitRequest(job.html_url, job.id))
                self.job_map[job.id] = job.name

            case WsSub() as ws_sub:
                self.browse_queue.put_nowait(CloseRequest(ws_sub.job_id))
                if ws_sub.job_id in self.job_map:
                    ws_sub = dataclasses.replace(ws_sub, job_name=self.job_map[ws_sub.job_id])
                self._replace_streamer(ws_sub.job_id, run_streamer(ws_sub, self.output_queue))

            case JobDone() as job:
                self.output_queue.put(OutputItem(job.job_name, [f"##[conclusion]{job.conclusion}"]))
                self._terminate_streamer(job.job_id)

            case WorkflowDone() as wf_done:
                self.browse_queue.put_nowait(ExitRequest())
                self.output_queue.put(
                    OutputItem("workflow", [f"##[conclusion]{wf_done.conclusion}"])
                )
                self.output_queue.put_nowait(None)
                self.output_queue.join()
                self.stop_event.set()

    def on_stop(self) -> None:
        debug("manager stopping")
        self.stop_event.set()
        self.output_queue.close()
        self.browse_queue.put_nowait(ExitRequest())
        for p in self.background_tasks.values():
            p.terminate()

    def _terminate_streamer(self, job_id: int) -> None:
        if job_id in self.background_tasks:
            self.background_tasks[job_id].terminate()
            del self.background_tasks[job_id]

    def _replace_streamer(self, job_id: int, streamer: mp.Process) -> None:
        self._terminate_streamer(job_id)
        self.background_tasks[job_id] = streamer


@cli
def main(opts: Opts) -> None:
    _stop = Event()

    if (repo_id := guess_repo()) is None:
        sys.exit(1)

    gh_client = Github(auth=Auth.Token(opts.gh_pat))
    wf_run = get_active_run(gh_client.get_repo(repo_id), opts.commit_sha, opts.workflow)
    if not isinstance(wf_run, WorkflowRun):
        log(f"could not get a matching workflow run: {wf_run}")
        sys.exit(1)

    # find a free port
    if opts.port is None:
        if (port := find_free_port()) is not None:
            opts = dataclasses.replace(opts, port=port)
        else:
            log("giving up finding a free port in the 8100 - 8500 range")
            sys.exit(1)

    debug(f"starting on port {opts.port}")

    # pylint: disable=E1136
    browser_inbox: mp.Queue[BrowseRequest] = mp.Queue()
    output_queue: mp.JoinableQueue[OutputItem | None] = mp.JoinableQueue()
    manager = Manager.start(browser_inbox, output_queue, _stop)

    browser_watcher = BrowserWatcher.start(manager, opts, browser_inbox)
    run_watcher = RunWatcher.start(manager, wf_run)
    proxy_watcher = ProxyWatcher.start(manager, opts.port)
    formatter = Formatter.start(output_queue)

    browser_watcher_future = browser_watcher.proxy().watch()
    run_watcher_future = run_watcher.proxy().watch()
    proxy_watcher_future = proxy_watcher.proxy().watch()
    formatter_future = formatter.proxy().print_lines()
    try:
        browser_watcher_future.join(
            run_watcher_future,
            proxy_watcher_future,
            formatter_future,
        ).get()
    except KeyboardInterrupt:
        _stop.set()

    ActorRegistry.stop_all()


def _main() -> None:
    typer.run(main)


if __name__ == "__main__":
    _main()
