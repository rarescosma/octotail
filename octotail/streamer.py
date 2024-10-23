"""Streamer actor."""

import asyncio as aio
import json
import multiprocessing as mp
from multiprocessing.queues import Queue

import websockets.client
from returns.result import Success, safe
from websockets.exceptions import ConnectionClosedError

from octotail.msg import OutputItem, StreamerMsg, WebsocketClosed, WsSub
from octotail.utils import RANDOM_UA, log

WS_HEADERS = {
    "User-Agent": RANDOM_UA,
    "Origin": "https://github.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "websocket",
    "Sec-Fetch-Site": "same-site",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}


def run_streamer(ws_sub: WsSub, queue: Queue[StreamerMsg]) -> mp.Process:  # pragma: no cover
    process = mp.Process(target=_streamer, args=(ws_sub, queue))
    process.start()
    return process


def _streamer(ws_sub: WsSub, queue: Queue[StreamerMsg]) -> None:
    loop = aio.new_event_loop()
    aio.set_event_loop(loop)
    try:
        loop.run_until_complete(_stream_it(ws_sub, queue))
    except KeyboardInterrupt:  # pragma: no cover
        loop.close()


async def _stream_it(ws_sub: WsSub, queue: Queue[StreamerMsg]) -> None:
    ws_url = "wss://" + ws_sub.url.removeprefix("https://")
    job_name = ws_sub.job_name or "unknown"

    async for websocket in websockets.client.connect(ws_url, extra_headers=WS_HEADERS):
        try:
            await websocket.send(ws_sub.subs)
            async for msg in websocket:
                _extract_lines(msg).apply(
                    Success(lambda _lines: queue.put(OutputItem(job_name, _lines)))
                )
        except ConnectionClosedError as e:
            log(f"fatal error during websockets connection: {e}")
            queue.put(WebsocketClosed())


@safe
def _extract_lines(msg: str | bytes) -> list[str]:
    _msg = msg.decode() if isinstance(msg, bytes) else msg
    return [line_obj["line"] for line_obj in json.loads(_msg)["data"]["data"]["lines"]]
