"""GitHub actor."""

import typing as t
from contextlib import suppress
from threading import Event

from github import Auth, Github
from github.Repository import Repository
from github.WorkflowJob import WorkflowJob
from github.WorkflowRun import WorkflowRun
from pykka import ActorRef, ThreadingActor
from returns.result import Failure, ResultE, Success
from urllib3.exceptions import HTTPError

from octotail.cli import Opts
from octotail.utils import Retry, debug, log, retries

VALID_STATI = ["queued", "in_progress", "requested", "waiting", "action_required"]
POLL_INTERVAL = 2


class WorkflowDone(t.NamedTuple):
    """Workflow done message."""

    conclusion: str


class JobDone(t.NamedTuple):
    """Job done message."""

    job_id: int
    job_name: str
    conclusion: str


class RunWatcher(ThreadingActor):
    """Watches for changes in a GitHub Actions run."""

    mgr: ActorRef
    wf_run: WorkflowRun
    stop_event: Event

    _new_jobs: set[int]
    _concluded_jobs: set[int]

    def __init__(self, mgr: ActorRef, wf_run: WorkflowRun):
        super().__init__()
        self.mgr = mgr
        self.wf_run = wf_run
        self.stop_event = mgr.proxy().stop_event.get()

        self._new_jobs = set()
        self._concluded_jobs = set()

    def watch(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.wf_run.update()

                for job in self.wf_run.jobs():
                    if job.conclusion and job.id not in self._concluded_jobs:
                        if not self._tell(JobDone(job.id, job.name, job.conclusion)):
                            break
                        self._concluded_jobs.add(job.id)
                        continue
                    if job.id not in self._new_jobs.union(self._concluded_jobs):
                        if not self._tell(job):
                            break
                        self._new_jobs.add(job.id)

                if self.wf_run.conclusion:
                    self._tell(WorkflowDone(self.wf_run.conclusion))
                    break
            except (OSError, HTTPError) as e:
                log(f"fatal error during workflow update: {e}")
                self.mgr.stop()
                break

            self.stop_event.wait(POLL_INTERVAL)
        debug("exiting")

    def _tell(self, what: JobDone | WorkflowDone | WorkflowJob) -> bool:
        if self.mgr.is_alive():
            self.mgr.tell(what)
            return True
        return False


@retries(10, 0.5)
def _get_active_run(repo: Repository, opts: Opts) -> ResultE[WorkflowRun] | Retry:
    with suppress(Exception):
        runs = repo.get_workflow_runs(head_sha=opts.commit_sha)
        if runs.totalCount == 0:
            return Retry()

        filters: list[t.Callable[[WorkflowRun], bool]] = [lambda wf: wf.status in VALID_STATI]
        if opts.workflow_name:
            filters.append(lambda wf: wf.name == t.cast(str, opts.workflow_name))
        if opts.ref_name:
            filters.append(lambda wf: t.cast(str, opts.ref_name).endswith(wf.head_branch))

        _runs = [r for r in runs if all(f(r) for f in filters)]
        if not _runs:
            return Retry()

        if len(_runs) > 1:
            log(f"found multiple active runs for commit '{opts.commit_sha}'")
            for run in _runs:
                log(f"\n\t{run.html_url}", skip_prefix=True)
            log("", skip_prefix=True)
            log("try narrowing down by workflow name (--workflow) or ref name (--ref-name)")
            return Failure(RuntimeError("cannot disambiguate"))

        return Success(runs[0])

    return Retry()


def get_active_run(repo_id: str, opts: Opts) -> WorkflowRun | None:
    gh_client = Github(auth=Auth.Token(opts.gh_pat))
    wf_run = _get_active_run(gh_client.get_repo(repo_id), opts)
    if not isinstance(wf_run, Success):
        log(f"could not get a matching workflow run: {wf_run}")
        return None
    return wf_run.unwrap()
