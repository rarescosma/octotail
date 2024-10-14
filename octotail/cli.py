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
    """Holds common options."""

    commit_sha: Annotated[str, typer.Argument(callback=_sha_callback)]
    workflow: str
    gh_pat: Annotated[str, typer.Option(envvar="_GH_PAT")]
    gh_user: Annotated[str, typer.Option(envvar="_GH_USER")]
    gh_pass: Annotated[str, typer.Option(envvar="_GH_PASS")]
    gh_otp: Annotated[str | None, typer.Option(envvar="_GH_OTP")] = None
    headless: Annotated[bool, typer.Option(envvar="_HEADLESS")] = True
    port: Annotated[int | None, typer.Option(envvar="_PORT")] = None

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
