""" Options class + some magic so we can define our args in one place only. """

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Annotated, Callable

import typer


def _sha_callback(value: str) -> str:
    if len(value) != 40:
        raise typer.BadParameter("need a full 40 character long commit sha")
    if value == 40 * "0":
        raise typer.BadParameter("refusing to work with the all-zero commit sha")
    return value


@dataclass(frozen=True)
class Opts:
    """
    Look for an active workflow run for the given <COMMIT_SHA> (and optionally
    --workflow-name and/or --ref-name) and attempt to tail its logs.

    NOTE: the <COMMIT_SHA> has to be of the full 40 characters length.
    """

    commit_sha: Annotated[
        str,
        typer.Argument(callback=_sha_callback, help="Full commit SHA that triggered the workflow."),
    ]
    gh_pat: Annotated[
        str,
        typer.Option(envvar="_GH_PAT", help="GitHub personal access token.", show_default=False),
    ]
    gh_user: Annotated[
        str,
        typer.Option(envvar="_GH_USER", help="GitHub username. (for web auth)", show_default=False),
    ]
    gh_pass: Annotated[
        str,
        typer.Option(envvar="_GH_PASS", help="GitHub password. (for web auth)", show_default=False),
    ]
    gh_otp: Annotated[
        str | None, typer.Option(envvar="_GH_OTP", help="GitHub OTP. (for web auth)")
    ] = None
    workflow_name: Annotated[
        str | None,
        typer.Option("-w", "--workflow", help="Look for workflows with this particular name."),
    ] = None
    ref_name: Annotated[
        str | None,
        typer.Option(
            "-r",
            "--ref-name",
            help="Look for workflows triggered by this ref.\n\nExample: 'refs/heads/main'",
        ),
    ] = None
    headless: Annotated[bool, typer.Option(envvar="_HEADLESS")] = True
    port: Annotated[
        int | None, typer.Option(envvar="_PORT", show_default="random in range 8100-8500")
    ] = None

    def __post_init__(self) -> None:
        _post_init.get()(self)


def _noop(_: Opts) -> None:
    pass


_post_init: ContextVar[Callable[[Opts], None]] = ContextVar("post_init", default=_noop)


def entrypoint(main_fn: Callable[[Opts], None]) -> Callable:
    def wrapped(opts: Opts) -> None:
        _post_init.set(_noop)
        main_fn(opts)

    @wraps(main_fn)
    def wrapper() -> None:
        app = typer.Typer(add_completion=False, rich_markup_mode="rich")
        app.command(no_args_is_help=True)(Opts)
        _post_init.set(wrapped)
        app()

    return wrapper
