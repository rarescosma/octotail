"""Asyncio and other nuggets."""

import asyncio as aio
import inspect
from typing import Tuple, cast


async def run_cmd(cmd: str) -> Tuple[bytes, bytes, int]:
    handle = await aio.create_subprocess_shell(
        cmd,
        stdout=aio.subprocess.PIPE,
        stderr=aio.subprocess.PIPE,
    )
    out, err = await handle.communicate()
    return out, err, cast(int, handle.returncode)


def log(msg: str) -> None:
    fn = inspect.stack()[1].function
    print(f"[{fn}]: {msg}")
