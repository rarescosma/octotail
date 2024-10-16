""" Options class + some magic so we can define our args in one place only. """

import os
import re
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Annotated, Callable
from unittest.mock import patch

from rich.box import Box
from rich.panel import Panel
from typer import Argument, BadParameter, Option, Typer

NO_RICH = os.getenv("NO_RICH") not in ["0", "false", "False", None]
_REPO_HELP = "\n".join(
    [
        "Use this GitHub repo to look for workflow runs.",
        "If unspecified, will look for a remote matching 'git@github.com:<user>/<repo>.git'",
        "in the current directory.",
        "\nExamples: 'user/repo' OR 'org_name/repo'",
    ]
)

NO_FRILLS: Box = Box(" -- \n" + "    \n" * 7)


def _sha_callback(value: str) -> str:
    if len(value) != 40:
        raise BadParameter("need a full 40 character long commit sha")
    if value == 40 * "0":
        raise BadParameter("refusing to work with the all-zero commit sha")
    return value


def _repo_callback(value: str | None) -> str | None:
    pattern = r"^[a-zA-Z0-9_-]{1,100}/[a-zA-Z0-9_-]{1,100}$"
    if value is not None and re.match(pattern, value) is None:
        raise BadParameter(f"invalid format for repo: {value}")
    return value


@dataclass(frozen=True)
class Opts:
    """
    Find an active workflow run for the given <COMMIT_SHA> (and optionally
    --workflow and/or --ref-name) and attempt to tail its logs.

    NOTE: the <COMMIT_SHA> has to be of the full 40 characters length.
    """

    commit_sha: Annotated[
        str,
        Argument(callback=_sha_callback, help="Full commit SHA that triggered the workflow."),
    ]
    gh_pat: Annotated[
        str,
        Option(
            envvar="_GH_PAT",
            help="GitHub personal access token. (for API auth)",
            show_default=False,
        ),
    ]
    gh_user: Annotated[
        str,
        Option(envvar="_GH_USER", help="GitHub username. (for web auth)", show_default=False),
    ]
    gh_pass: Annotated[
        str,
        Option(envvar="_GH_PASS", help="GitHub password. (for web auth)", show_default=False),
    ]
    gh_otp: Annotated[str | None, Option(envvar="_GH_OTP", help="GitHub OTP. (for web auth)")] = (
        None
    )
    workflow_name: Annotated[
        str | None,
        Option(
            "-w",
            "--workflow",
            help="Only consider workflows with this name.",
            show_default=False,
        ),
    ] = None
    ref_name: Annotated[
        str | None,
        Option(
            "-r",
            "--ref-name",
            help="Only consider workflows triggered by this ref.\n\nExample: 'refs/heads/main'",
            show_default=False,
        ),
    ] = None
    repo: Annotated[
        str | None,
        Option(
            "-R",
            "--repo",
            help=_REPO_HELP,
            show_default=False,
            callback=_repo_callback,
        ),
    ] = None
    headless: Annotated[bool, Option(envvar="_HEADLESS", help="Run browser in headless mode.")] = (
        True
    )
    port: Annotated[
        int | None,
        Option(
            envvar="_PORT",
            help="Port the proxy will listen on.",
            show_default="random in range 8100-8500",
        ),
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
        app = Typer(
            add_completion=False,
            rich_markup_mode=(None if NO_RICH else "rich"),
            pretty_exceptions_show_locals=False,
        )
        app.command(no_args_is_help=True)(Opts)
        _post_init.set(wrapped)
        with patch(
            "typer.rich_utils.Panel",
            lambda *args, **kwargs: Panel(*args, **{**kwargs, "box": NO_FRILLS}),
        ) as _:
            app()

    return wrapper
