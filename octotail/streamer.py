"""Streamer actor."""

import asyncio as aio
import json
import multiprocessing as mp
from contextlib import suppress
from multiprocessing.synchronize import Lock as LockBase

import websockets.client

from octotail.browser import RANDOM_UA
from octotail.mitm import WsSub
from octotail.utils import log

WS_HEADERS = {
    "User-Agent": RANDOM_UA,
    "Origin": "https://github.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "websocket",
    "Sec-Fetch-Site": "same-site",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}


def run_streamer(ws_sub: WsSub, lock: LockBase) -> mp.Process:
    p = mp.Process(target=_streamer, args=(ws_sub, lock))
    p.start()
    return p


def _streamer(ws_sub: WsSub, lock: LockBase) -> None:
    loop = aio.new_event_loop()
    aio.set_event_loop(loop)
    try:
        loop.run_until_complete(_stream_it(ws_sub, lock))
    except KeyboardInterrupt:
        loop.close()


async def _stream_it(ws_sub: WsSub, lock: LockBase) -> None:
    ws_url = "wss://" + ws_sub.url.removeprefix("https://")
    job_name = ws_sub.job_name or "unknown"

    websocket = None
    try:
        async with websockets.client.connect(ws_url, extra_headers=WS_HEADERS) as websocket:
            await websocket.send(ws_sub.subs)
            async for msg in websocket:
                lines = _extract_lines(job_name, msg)
                if lines:
                    lock.acquire()
                    print(lines)
                    lock.release()
    except aio.CancelledError:
        log("cancelled")
        if websocket:
            await websocket.close()


def _extract_lines(job_name: str, x: str) -> str:
    with suppress(Exception):
        return "\n".join(
            f"[{job_name}]: {l['line']}" for l in json.loads(x)["data"]["data"]["lines"]
        )
    return ""
