"""Bits and pieces."""

import inspect
import os
import random
import socket
import sys
import time
from pathlib import Path
from typing import Any, Callable, Generator, Generic, Iterable, NamedTuple, TypeVar

DEBUG = os.getenv("DEBUG") not in ["0", "false", "False", None]

A = TypeVar("A")
B = TypeVar("B")
T = TypeVar("T")


class Retry:
    """Retry variant."""


class Ok(NamedTuple, Generic[T]):
    """Success variant."""

    result: T


type Result[T] = Ok[T] | RuntimeError | Retry


def flatmap(f: Callable[[A], Iterable[B]], xs: Iterable[A]) -> Iterable[B]:
    """Map f over an iterable and flatten the result set."""
    return (y for x in xs for y in f(x))


def log(
    msg: str, /, stack_offset: int = 1, file: Any = sys.stdout, skip_prefix: bool = False
) -> None:
    if skip_prefix:
        prefix = ""
    else:
        frame = inspect.stack()[stack_offset]
        module = inspect.getmodule(frame[0])
        if module is not None and getattr(module, "__file__") is not None:
            module_name = Path(getattr(module, "__file__")).with_suffix("").name
        else:
            module_name = "?"
        prefix = f"[{module_name}:{frame.function}]: "
    print(f"{prefix}{msg}", file=file)


def debug(msg: str) -> None:
    if DEBUG:
        log(msg, stack_offset=2, file=sys.stderr)


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


def remove_consecutive_falsy(items: Iterable[T]) -> Generator[T, None, None]:
    yielded_empty = False
    for item in items:
        if item:
            yielded_empty = False
            yield item
        elif not yielded_empty:
            yield item
            yielded_empty = True
