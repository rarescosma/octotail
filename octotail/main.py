#!/usr/bin/env -S /bin/sh -c 'exec "$(dirname $(readlink -f "$0"))/../.venv/bin/python3" "$0" "$@"'

"""Having your cake and eating it too."""
import dataclasses
import multiprocessing as mp
import sys
from threading import Event

from pykka import ActorRegistry
from returns.pipeline import is_successful
from returns.result import Failure, Success

from octotail.cli import Opts, entrypoint
from octotail.utils import debug, find_free_port, log


@entrypoint
def _main(opts: Opts) -> int:
    from octotail.browser import BrowseRequest, BrowserWatcher
    from octotail.fmt import Formatter
    from octotail.gh import RunWatcher, get_active_run
    from octotail.git import guess_github_repo
    from octotail.manager import Manager
    from octotail.mitm import ProxyWatcher
    from octotail.streamer import OutputItem, WebsocketClosed

    def _repo_id() -> str | None:
        match opts.repo or guess_github_repo():
            case from_opts if isinstance(from_opts, str):
                return from_opts
            case Success(from_remote):
                return from_remote
            case Failure(e):
                debug(f"failed to process git remotes: {e}")
        return None

    if (repo_id := _repo_id()) is None:
        log("fatal: could not guess repo from remotes and no --repo/-R was passed")
        return 1

    wf_run = get_active_run(repo_id, opts)
    if not is_successful(wf_run):
        log(f"fatal: could not find an active run: {wf_run.failure()}")
        return 1

    # find a free port
    if opts.port is None:
        if (port := find_free_port()) is not None:
            opts = dataclasses.replace(opts, port=port)
        else:
            log("fatal: giving up finding a free port in the 8100 - 8500 range")
            return 1
    debug(f"starting on port {opts.port}")

    _stop = Event()

    # pylint: disable=E1136
    browser_inbox: mp.Queue[BrowseRequest] = mp.Queue()
    output_queue: mp.JoinableQueue[OutputItem | WebsocketClosed | None] = mp.JoinableQueue()
    manager = Manager.start(browser_inbox, output_queue, _stop)

    run_watcher = RunWatcher.start(manager, wf_run.unwrap())
    browser_watcher = BrowserWatcher.start(manager, opts, browser_inbox)
    proxy_watcher = ProxyWatcher.start(manager, opts.port)
    formatter = Formatter.start(manager, output_queue)

    try:
        run_watcher.proxy().watch().join(
            browser_watcher.proxy().watch(),
            proxy_watcher.proxy().watch(),
            formatter.proxy().print_lines(),
        ).get()
    except KeyboardInterrupt:
        _stop.set()

    ActorRegistry.stop_all()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
