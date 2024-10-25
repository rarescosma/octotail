"""Bits and pieces."""

import inspect
import os
import random
import socket
import sys
import time
import typing as t
from pathlib import Path

from fake_useragent import UserAgent
from returns.io import IO, IOResultE
from returns.result import Failure, ResultE, Success
from returns.unsafe import unsafe_perform_io

RANDOM_UA: str = UserAgent().random
DEBUG = os.getenv("DEBUG") not in ["0", "false", "False", None]
FIND_FREE_PORT_TRIES = 100

A = t.TypeVar("A")
B = t.TypeVar("B")
T = t.TypeVar("T")


class Retry:
    """Retry variant."""

    def __eq__(self, other: t.Any) -> bool:
        return isinstance(other, Retry)


def flatmap(f: t.Callable[[A], t.Iterable[B]], xs: t.Iterable[A]) -> t.Iterable[B]:
    """Map f over an iterable and flatten the result set."""
    return (y for x in xs for y in f(x))


def log(
    msg: str, *, stack_offset: int = 1, file: t.Any = sys.stdout, skip_prefix: bool = False
) -> None:
    if skip_prefix:
        prefix = ""
    else:
        stack = inspect.stack()
        frame = stack[min(stack_offset, len(stack) - 1)]
        module = inspect.getmodule(frame[0])
        if module is not None and module.__file__ is not None:
            module_name = Path(module.__file__).with_suffix("").name
        else:
            module_name = "?"
        prefix = f"[{module_name}:{frame.function}]: "
    print(f"{prefix}{msg}", file=file)


def debug(msg: str, *, file: t.Any = sys.stderr) -> None:
    if DEBUG:
        log(msg, stack_offset=2, file=file)


def perform_io[**P, R](fn: t.Callable[P, IOResultE[R]]) -> t.Callable[P, ResultE[R]]:
    def inner(*args: P.args, **kwargs: P.kwargs) -> ResultE[R]:
        return unsafe_perform_io(IO.from_ioresult(fn(*args, **kwargs)))

    return inner


def retries[
    **P, R
](num_retries: int, retry_delay: float) -> t.Callable[
    [t.Callable[P, ResultE[R] | Retry]], t.Callable[P, ResultE[R]]
]:
    """ "just put a retry loop around it" """

    def wrapper(fn: t.Callable[P, ResultE[R] | Retry]) -> t.Callable[P, ResultE[R]]:
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> ResultE[R]:
            for _ in range(num_retries):
                match fn(*args, **kwargs):
                    case Success() as s:
                        return s
                    case Failure() as f:
                        return f
                    case _ as obj if obj == Retry():
                        time.sleep(retry_delay)
                        continue
            return Failure(RuntimeError(f"retries exceeded in '{fn.__name__}'"))

        return wrapped

    return wrapper


def find_free_port(min_port: int = 8100, max_port: int = 8500) -> int | None:
    num_tries = 0
    random_port = random.randint(min_port, max_port)
    while not is_port_open(random_port):
        if num_tries > FIND_FREE_PORT_TRIES:
            return None
        random_port = random.randint(min_port, max_port)
        num_tries += 1
    return random_port


def is_port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    res = sock.connect_ex(("127.0.0.1", port))
    sock.close()
    return res != 0


def remove_consecutive_falsy(items: t.Iterable[T]) -> t.Generator[T, None, None]:
    yielded_empty = False
    for item in items:
        if item or not yielded_empty:
            yield item
        yielded_empty = not item
