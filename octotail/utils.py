"""Asyncio and other nuggets."""

import asyncio as aio
import inspect
from typing import (
    Awaitable,
    Callable,
    Concatenate,
    Coroutine,
    Generic,
    NamedTuple,
    Tuple,
    TypeVar,
    cast,
)


async def run_cmd(cmd: str) -> Tuple[bytes, bytes, int]:
    handle = await aio.create_subprocess_shell(
        cmd,
        stdout=aio.subprocess.PIPE,
        stderr=aio.subprocess.PIPE,
    )
    out, err = await handle.communicate()
    return out, err, cast(int, handle.returncode)


T = TypeVar("T")


class Retry:
    """Retry variant."""


class Ok(NamedTuple, Generic[T]):
    """Success variant."""

    result: T


type Result[T] = Ok[T] | RuntimeError | Retry


def retries[
    **P
](num_retries: int, sleep_time: float) -> Callable[
    [Callable[P, Awaitable[Result]]], Callable[Concatenate[aio.Queue, P], Coroutine]
]:
    """ "just put a retry loop around it" """

    def wrapper(
        fn: Callable[P, Awaitable[Result]]
    ) -> Callable[Concatenate[aio.Queue, P], Coroutine]:
        async def wrapped(q: aio.Queue, *args: P.args, **kwargs: P.kwargs) -> None:
            for _ in range(0, num_retries):
                match await fn(*args, **kwargs):
                    case Ok(x):
                        return await q.put(x)
                    case Retry():
                        await aio.sleep(sleep_time)
                        continue
                    case RuntimeError() as e:
                        return await q.put(e)
            await q.put(RuntimeError(f"retries exceeded in '{fn.__name__}'"))

        return wrapped

    return wrapper


def log(msg: str) -> None:
    fn = inspect.stack()[1].function
    print(f"[{fn}]: {msg}")
