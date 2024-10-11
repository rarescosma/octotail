#!/usr/bin/env -S /bin/sh -c 'exec "$(dirname $(readlink -f "$0"))/../.venv/bin/python3" "$0" "$@"'

"""Having your cake and eating it too."""
import dataclasses
import multiprocessing as mp
import sys
from multiprocessing.synchronize import Lock as LockBase
from pathlib import Path
from threading import Event
from typing import Dict, Union

import typer
from github import Auth, Github
from github.WorkflowJob import WorkflowJob
from github.WorkflowRun import WorkflowRun
from pykka import ActorRegistry, ThreadingActor
from xdg.BaseDirectory import xdg_cache_home

from octotail.browser import CloseRequest, ExitRequest, VisitRequest, run_browser
from octotail.gh import JobDone, RunWatcher, WorkflowDone, get_active_run, guess_repo
from octotail.mitm import ProxyWatcher, WsSub
from octotail.streamer import run_streamer
from octotail.utils import Opts, cli, debug, log

COOKIE_JAR = Path(xdg_cache_home) / "octotail" / "gh-cookies.json"

type MgrMessage = Union[WorkflowJob, WsSub, JobDone, WorkflowDone]


class Manager(ThreadingActor):
    """I'm the Baahwss."""

    browse_queue: mp.Queue
    stop: Event
    background_tasks: Dict[int, mp.Process]
    output_lock: LockBase
    job_map: Dict[int, str]

    def __init__(self, browse_queue: mp.Queue, stop: Event):
        super().__init__()
        self.browse_queue = browse_queue
        self.stop = stop
        self.background_tasks = {}
        self.output_lock = mp.Lock()
        self.job_map = {}

    def on_receive(self, msg: MgrMessage) -> None:
        debug(f"manager got message: {msg!r}")
        match msg:
            case WorkflowJob() as job:
                self.browse_queue.put_nowait(VisitRequest(job.html_url))
                self.job_map[job.id] = job.name

            case WsSub() as ws_sub:
                if ws_sub.job_id in self.job_map:
                    ws_sub = dataclasses.replace(ws_sub, job_name=self.job_map[ws_sub.job_id])
                self.browse_queue.put_nowait(CloseRequest(ws_sub.job_id))
                self.background_tasks[ws_sub.job_id] = run_streamer(ws_sub, self.output_lock)

            case JobDone() as job_done:
                print(f"[{job_done.job_name}] conclusion: {job_done.conclusion}")
                if (streamer := self.background_tasks.get(job_done.job_id)) is not None:
                    streamer.terminate()
                    del self.background_tasks[job_done.job_id]

            case WorkflowDone() as wf_done:
                print(f"workflow conclusion: {wf_done.conclusion}")
                self.browse_queue.put_nowait(ExitRequest())
                self.stop.set()

    def on_stop(self) -> None:
        log("manager stopping")
        self.browse_queue.put_nowait(ExitRequest())
        for p in self.background_tasks.values():
            p.terminate()


@cli
def main(opts: Opts) -> None:
    _stop = Event()

    if (repo_id := guess_repo()) is None:
        sys.exit(1)

    g = Github(auth=Auth.Token(opts.gh_pat))
    gh_repo = g.get_repo(repo_id)
    gha_run = get_active_run(gh_repo, opts.commit_sha, opts.workflow)
    if not isinstance(gha_run, WorkflowRun):
        sys.exit(1)

    # pylint: disable=E1136
    url_queue: mp.Queue[str] = mp.Queue()

    mp.Process(target=run_browser, args=(opts, url_queue)).start()
    manager = Manager.start(url_queue, _stop)
    run_watcher = RunWatcher.start(gha_run, manager, _stop)
    proxy_watcher = ProxyWatcher.start(manager, _stop)

    h1 = run_watcher.proxy().watch()
    h2 = proxy_watcher.proxy().watch()
    try:
        h1.join(h2).get()
    except KeyboardInterrupt:
        _stop.set()

    ActorRegistry.stop_all()


def _main() -> None:
    typer.run(main)


if __name__ == "__main__":
    _main()
