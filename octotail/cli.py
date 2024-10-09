"""CLI-related routines."""

import asyncio as aio
import signal
import sys
from typing import Annotated, Callable, Coroutine, NamedTuple

import typer


class Opts(NamedTuple):
    """Holds common options."""

    commit_sha: str
    workflow: str
    gh_user: str
    gh_pass: str
    gh_token: str
    headless: bool


def cli(async_main: Callable[..., Coroutine]) -> Callable:
    # pylint: disable=R0913,R0917
    def _inner(
        commit_sha: Annotated[str, typer.Argument(callback=_sha_callback)],
        workflow: str,
        gh_user: Annotated[str, typer.Option(envvar="_GH_USER")],
        gh_pass: Annotated[str, typer.Option(envvar="_GH_PASS")],
        gh_token: Annotated[str, typer.Option(envvar="_GH_TOKEN")],
        headless: Annotated[bool, typer.Option(envvar="_HEADLESS")] = True,
    ) -> None:
        opts = Opts(commit_sha, workflow, gh_user, gh_pass, gh_token, headless)

        async def _inner2() -> None:
            loop = aio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, lambda *_: sys.exit(0), tuple())
            loop.add_signal_handler(signal.SIGTERM, lambda *_: sys.exit(0), tuple())
            await async_main(opts)

        aio.run(_inner2())

    return _inner


def _sha_callback(value: str) -> str:
    if len(value) != 40:
        raise typer.BadParameter("need a full 40 character long commit sha")
    return value
