"""Bits and pieces."""

import inspect
import os
import random
import socket
import time
from dataclasses import dataclass
from typing import Annotated, Callable, Generic, NamedTuple, TypeVar

import typer

DEBUG = os.getenv("DEBUG") not in ["0", "false", "False", None]

T = TypeVar("T")


class Retry:
    """Retry variant."""


class Ok(NamedTuple, Generic[T]):
    """Success variant."""

    result: T


type Result[T] = Ok[T] | RuntimeError | Retry


def log(msg: str, stack_offset: int = 1) -> None:
    fn = inspect.stack()[stack_offset].function
    print(f"[{fn}]: {msg}")


def debug(msg: str) -> None:
    if DEBUG:
        log(msg, 2)


def retries[
    **P
](num_retries: int, sleep_time: float) -> Callable[
    [Callable[P, Result | Retry]], Callable[P, Result]
]:
    """ "just put a retry loop around it" """

    def wrapper(fn: Callable[P, Result | Retry]) -> Callable[P, Result]:
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> Result:
            for _ in range(0, num_retries):
                match fn(*args, **kwargs):
                    case Ok(x):
                        return x
                    case Retry():
                        time.sleep(sleep_time)
                        continue
                    case RuntimeError() as e:
                        return e
            return RuntimeError(f"retries exceeded in '{fn.__name__}'")

        return wrapped

    return wrapper


@dataclass(frozen=True)
class Opts:
    """Holds common options."""

    commit_sha: str
    workflow: str
    gh_user: str
    gh_pass: str
    gh_otp: str
    gh_pat: str
    headless: bool
    port: int | None


def cli(main: Callable[[Opts], None]) -> Callable:
    # pylint: disable=R0913,R0917
    def _inner(
        commit_sha: Annotated[str, typer.Argument(callback=_sha_callback)],
        workflow: str,
        gh_user: Annotated[str, typer.Option(envvar="_GH_USER")],
        gh_pass: Annotated[str, typer.Option(envvar="_GH_PASS")],
        gh_otp: Annotated[str, typer.Option(envvar="_GH_OTP")],
        gh_pat: Annotated[str, typer.Option(envvar="_GH_PAT")],
        headless: Annotated[bool, typer.Option(envvar="_HEADLESS")] = True,
        port: Annotated[int | None, typer.Option(envvar="_PORT")] = None,
    ) -> None:
        opts = Opts(commit_sha, workflow, gh_user, gh_pass, gh_otp, gh_pat, headless, port)
        main(opts)

    return _inner


def find_free_port(min_port: int = 8100, max_port: int = 8500) -> int | None:
    num_tries = 0
    random_port = random.randint(min_port, max_port)
    while not is_port_open(random_port):
        if num_tries > 100:
            return None
        random_port = random.randint(min_port, max_port)
        num_tries += 1
    return random_port


def is_port_open(port: int) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    res = sock.connect_ex(("127.0.0.1", port))
    sock.close()
    return res != 0


def _sha_callback(value: str) -> str:
    if len(value) != 40:
        raise typer.BadParameter("need a full 40 character long commit sha")
    return value
