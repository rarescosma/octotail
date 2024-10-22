"""GitHub actor."""

import typing as t
from threading import Event

from github import Auth, Github
from github.Repository import Repository
from github.WorkflowJob import WorkflowJob
from github.WorkflowRun import WorkflowRun
from pykka import ActorRef, ThreadingActor
from returns.io import IOResultE, impure_safe
from returns.pipeline import is_successful
from returns.result import Failure, ResultE, Success

from octotail.cli import Opts
from octotail.manager import Manager
from octotail.msg import JobDone, WorkflowDone
from octotail.utils import Retry, debug, log, perform_io, retries

VALID_STATI = ["queued", "in_progress", "requested", "waiting", "action_required"]
DEFAULT_POLL_INTERVAL = 2


class Client:  # pragma: no cover
    """Side-effects galore."""

    @staticmethod
    @impure_safe
    def get_workflow_jobs(wf_run: WorkflowRun) -> list[WorkflowJob]:
        wf_run.update()
        return list(wf_run.jobs())

    @staticmethod
    @impure_safe
    def get_workflow_runs(repo: Repository, head_sha: str) -> list[WorkflowRun]:
        return list(repo.get_workflow_runs(head_sha=head_sha))

    @staticmethod
    @impure_safe
    def get_repo(repo_id: str, pat: str) -> Repository:
        gh_client = Github(auth=Auth.Token(pat))
        return gh_client.get_repo(repo_id)

    @staticmethod
    def poll_interval() -> float:
        return DEFAULT_POLL_INTERVAL


DEFAULT_CLIENT: Client = Client()


class JobState(t.NamedTuple):
    """Holds job state (seen or concluded)."""

    seen_jobs: set[int]
    concluded_jobs: set[int]

    @classmethod
    def default(cls) -> "JobState":
        return cls(seen_jobs=set(), concluded_jobs=set())

    def diff(self, new_jobs: list[WorkflowJob]) -> list[JobDone | WorkflowJob]:
        _diff: list[JobDone | WorkflowJob] = []
        for job in new_jobs:
            if job.conclusion and job.id not in self.concluded_jobs:
                _diff.append(JobDone(job.id, job.name, job.conclusion))
                self.concluded_jobs.add(job.id)
                continue
            if job.id not in self.seen_jobs.union(self.concluded_jobs):
                _diff.append(job)
                self.seen_jobs.add(job.id)
        return _diff


class RunWatcher(ThreadingActor):
    """Watches for changes in a GitHub Actions run."""

    mgr: ActorRef[Manager]
    wf_run: WorkflowRun
    client: Client
    stop_event: Event

    _state: JobState

    def __init__(
        self,
        mgr: ActorRef[Manager],
        wf_run: WorkflowRun,
        client: Client = DEFAULT_CLIENT,
    ):
        super().__init__()
        self.mgr = mgr
        self.wf_run = wf_run
        self.client = client
        self.stop_event = mgr.proxy().stop_event.get()

        self._state = JobState.default()

    def watch(self) -> None:
        while not self.stop_event.is_set():
            jobs_res = perform_io(self.client.get_workflow_jobs)(self.wf_run)
            if not is_successful(jobs_res):
                log(f"fatal error during workflow run update: {jobs_res.failure()}")
                self.mgr.stop()
                break

            for job in self._state.diff(jobs_res.unwrap()):
                if not self._tell(job):
                    break

            if wf_conclusion := self.wf_run.conclusion:
                self._tell(WorkflowDone(wf_conclusion))
                break

            self.stop_event.wait(self.client.poll_interval())
        debug("exiting")

    def _tell(self, what: JobDone | WorkflowDone | WorkflowJob) -> bool:
        if self.mgr.is_alive():
            self.mgr.tell(what)
            return True
        return False


def _get_active_run(
    opts: Opts, run_lister: t.Callable[..., ResultE[list[WorkflowRun]]]
) -> ResultE[WorkflowRun] | Retry:
    runs_res = run_lister()
    if not is_successful(runs_res):
        return Failure(runs_res.failure())

    if not (filtered := _filter_runs(opts, runs_res.unwrap())):
        return Retry()

    if len(filtered) > 1:
        log(f"found multiple active runs for commit '{opts.commit_sha}'")
        for run in filtered:
            log(f"\n\t{run.html_url}", skip_prefix=True)
        log("", skip_prefix=True)
        log("try narrowing down by workflow name (--workflow) or ref name (--ref-name)")
        return Failure(RuntimeError("cannot disambiguate"))

    return Success(filtered[0])


def _filter_runs(opts: Opts, runs: list[WorkflowRun]) -> list[WorkflowRun]:
    predicates: list[t.Callable[[WorkflowRun], bool]] = [lambda run: run.status in VALID_STATI]
    if opts.workflow_name:
        predicates.append(lambda run: run.name == t.cast(str, opts.workflow_name))
    if opts.ref_name:
        predicates.append(lambda run: t.cast(str, opts.ref_name).endswith(run.head_branch))
    return [run for run in runs if all(predicate(run) for predicate in predicates)]


def get_active_run(
    repo_id: str,
    opts: Opts,
    *,
    client: Client = DEFAULT_CLIENT,
    retry_delay: float = 0.5,
) -> ResultE[WorkflowRun]:
    repo = client.get_repo(repo_id, opts.gh_pat)

    def _curried_workflow_runs(_repo: IOResultE[Repository]) -> IOResultE[list[WorkflowRun]]:
        return _repo.bind(lambda __repo: client.get_workflow_runs(__repo, opts.commit_sha))

    return retries(10, retry_delay)(_get_active_run)(
        opts,
        lambda: perform_io(_curried_workflow_runs)(repo),
    )
