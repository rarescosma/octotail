"""GitHub actor."""

import typing as t
from threading import Event

from github import Auth, Github
from github.Repository import Repository
from github.WorkflowJob import WorkflowJob
from github.WorkflowRun import WorkflowRun
from pykka import ActorRef, ThreadingActor
from returns.io import IO, impure_safe
from returns.pipeline import flow, is_successful
from returns.result import Failure, ResultE, Success
from returns.unsafe import unsafe_perform_io

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
            jobs_res = flow(
                _get_workflow_jobs(self.wf_run),
                IO.from_ioresult,
                unsafe_perform_io,
            )
            if not is_successful(jobs_res):
                log(f"fatal error during workflow run update: {jobs_res.failure()}")
                self.mgr.stop()
                break

            for job in jobs_res.unwrap():
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

            self.stop_event.wait(POLL_INTERVAL)
        debug("exiting")

    def _tell(self, what: JobDone | WorkflowDone | WorkflowJob) -> bool:
        if self.mgr.is_alive():
            self.mgr.tell(what)
            return True
        return False


@retries(10, 0.5)
def _get_active_run(repo: Repository, opts: Opts) -> ResultE[WorkflowRun] | Retry:
    runs_res = flow(
        _get_workflow_runs(repo, opts.commit_sha),
        IO.from_ioresult,
        unsafe_perform_io,
    )
    if not is_successful(runs_res):
        return Failure(runs_res.failure())

    runs = runs_res.unwrap()
    if len(runs) == 0:
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

    return Success(_runs[0])


@impure_safe
def _get_workflow_jobs(wf_run: WorkflowRun) -> list[WorkflowJob]:
    wf_run.update()
    return list(wf_run.jobs())


@impure_safe
def _get_workflow_runs(repo: Repository, head_sha: str) -> list[WorkflowRun]:
    return list(repo.get_workflow_runs(head_sha=head_sha))


@impure_safe
def _get_repo(repo_id: str, pat: str) -> Repository:
    gh_client = Github(auth=Auth.Token(pat))
    return gh_client.get_repo(repo_id)


def get_active_run(repo_id: str, opts: Opts) -> ResultE[WorkflowRun]:
    repo = flow(
        _get_repo(repo_id, opts.gh_pat),
        IO.from_ioresult,
        unsafe_perform_io,
    )
    return repo.bind(lambda _repo: _get_active_run(_repo, opts))
