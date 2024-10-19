#!/usr/bin/env python3
""" Git pipelines. """
import re
import typing as t
from pathlib import Path
from subprocess import DEVNULL, check_output

from returns.converters import flatten, maybe_to_result
from returns.functions import identity
from returns.io import IO, impure_safe
from returns.maybe import Maybe, Nothing, Some
from returns.pipeline import flow
from returns.pointfree import bind_result, map_
from returns.result import ResultE, safe
from returns.unsafe import unsafe_perform_io

GH_REMOTE_MARKER = "git@github.com"


class GitRemote(t.NamedTuple):
    """A git remote."""

    name: str
    url: str


@impure_safe
def _check_git(cmd: str, cwd: Path = Path()) -> str:
    return check_output(["git", *cmd.split()], stderr=DEVNULL, cwd=cwd).decode().strip()


def git(cmd: str, *, cwd: Path = Path()) -> ResultE[str]:
    return flow(
        _check_git(cmd, cwd),
        IO.from_ioresult,
        unsafe_perform_io,
    )


@safe
def _parse_remotes(remotes: str) -> list[GitRemote]:
    return sorted({GitRemote(*tuple(filter(None, r.split()))[:2]) for r in remotes.splitlines()})


@safe
def _limit_remotes(remotes: list[GitRemote]) -> GitRemote:
    if not remotes:
        raise RuntimeError("no remotes")
    if len(remotes) > 1:
        raise RuntimeError(f"too many remotes: {remotes!r}")
    return remotes[0]


def _extract_github_repo(remote: GitRemote) -> ResultE[str]:
    match = Maybe.from_optional(re.search(rf"^{GH_REMOTE_MARKER}:([^.]+).git$", remote.url)).map(
        lambda m: m.group(1)
    )
    return maybe_to_result(match).alt(
        lambda _: RuntimeError(f"failed to extract github repo from remote '{remote}'")
    )


def get_remotes(
    filter_fn: Maybe[t.Callable[[GitRemote], bool]] = Nothing,
) -> ResultE[list[GitRemote]]:
    _filter_fn = filter_fn.map(lambda f: lambda xs: [x for x in xs if f(x)])
    return flow(
        git("remote --verbose"),
        bind_result(_parse_remotes),
        map_(_filter_fn.value_or(identity)),
    )


def guess_github_repo() -> ResultE[str]:
    remote = flow(
        get_remotes(Some(lambda r: GH_REMOTE_MARKER in r.url)),
        bind_result(_limit_remotes),
    )
    return flatten(remote.map(_extract_github_repo))


def get_repo_dir() -> ResultE[Path]:
    return git("rev-parse --sq --show-toplevel").map(Path)
