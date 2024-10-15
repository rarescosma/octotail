"""GitHub actor."""

import re
from contextlib import suppress
from subprocess import CalledProcessError, check_output
from threading import Event
from typing import Callable, List, NamedTuple, Set, cast

from github.Repository import Repository
from github.WorkflowJob import WorkflowJob
from github.WorkflowRun import WorkflowRun
from pykka import ActorRef, ThreadingActor

from octotail.cli import Opts
from octotail.utils import Ok, Result, Retry, debug, log, retries

VALID_STATI = ["queued", "in_progress", "requested", "waiting", "action_required"]
POLL_INTERVAL = 2


class WorkflowDone(NamedTuple):
    """Workflow done message."""

    conclusion: str


class JobDone(NamedTuple):
    """Job done message."""

    job_id: int
    job_name: str
    conclusion: str


class RunWatcher(ThreadingActor):
    """Watches for changes in a GitHub Actions run."""

    mgr: ActorRef
    wf_run: WorkflowRun
    stop_event: Event

    _new_jobs: Set[int] = set()
    _concluded_jobs: Set[int] = set()

    def __init__(self, mgr: ActorRef, wf_run: WorkflowRun):
        super().__init__()
        self.mgr = mgr
        self.wf_run = wf_run
        self.stop_event = mgr.proxy().stop_event.get()

    def watch(self) -> None:
        while not self.stop_event.is_set():
            self.wf_run.update()

            with suppress(Exception):
                for job in self.wf_run.jobs():
                    if job.conclusion and not job.id in self._concluded_jobs:
                        if not self._tell(JobDone(job.id, job.name, job.conclusion)):
                            return
                        self._concluded_jobs.add(job.id)
                        continue
                    if not job.id in self._new_jobs.union(self._concluded_jobs):
                        if not self._tell(job):
                            return
                        self._new_jobs.add(job.id)

            if self.wf_run.conclusion:
                self._tell(WorkflowDone(self.wf_run.conclusion))
                return

            self.stop_event.wait(POLL_INTERVAL)
        debug("exiting")

    def _tell(self, what: JobDone | WorkflowDone | WorkflowJob) -> bool:
        if self.mgr.is_alive():
            self.mgr.tell(what)
            return True
        return False


@retries(10, 0.5)
def get_active_run(repo: Repository, opts: Opts) -> Result[WorkflowRun] | Retry:
    with suppress(Exception):
        runs = repo.get_workflow_runs(head_sha=opts.commit_sha)
        if runs.totalCount == 0:
            return Retry()

        filters: List[Callable[[WorkflowRun], bool]] = [lambda wf: wf.status in VALID_STATI]
        if opts.workflow_name:
            filters.append(lambda wf: wf.name == cast(str, opts.workflow_name))
        if opts.ref_name:
            filters.append(lambda wf: cast(str, opts.ref_name).endswith(wf.head_branch))

        _runs = [r for r in runs if all(f(r) for f in filters)]
        if not _runs:
            return Retry()

        if len(_runs) > 1:
            log(f"found multiple active runs for commit '{opts.commit_sha}'")
            for run in _runs:
                log(f"\n\t{run.html_url}", skip_prefix=True)
            log("", skip_prefix=True)
            log("try narrowing down by workflow name (--workflow) or ref name (--ref-name)")
            return RuntimeError("cannot disambiguate")

        return Ok(runs[0])

    return Retry()


def guess_repo() -> str | None:
    try:
        remotes = check_output(["git", "remote"]).decode().strip().splitlines()
    except CalledProcessError as e:
        log(f"fatal: couldn't list git remotes: {e}")
        return None
    repos = list(filter(None, map(_guess_repo, remotes)))
    if len(repos) > 1:
        log("fatal: found multiple remotes pointing to GitHub")
        return None
    return repos[0] if repos else None


def _guess_repo(remote: str) -> str | None:
    with suppress(Exception):
        origin = check_output(f"git remote get-url {remote}".split()).decode().strip()
        if (match := re.search(r"^git@github.com:([^.]+).git$", origin)) is not None:
            return match.group(1)
    return None
