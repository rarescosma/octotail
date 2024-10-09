"""Websockets-related routines."""

import asyncio as aio
import json
from contextlib import suppress
from typing import Optional

import websockets.client

from .utils import log


async def stream_it(url: str, sub: str) -> None:
    ws_url = "wss://" + url.removeprefix("https://")

    websocket = None
    try:
        async with websockets.client.connect(ws_url, extra_headers=WS_HEADERS) as websocket:
            await websocket.send(sub)
            async for msg in websocket:
                if (conclusion := _is_completed(msg)) is not None:
                    print(conclusion)
                    return
                print(_extract_line(msg))
    except aio.CancelledError:
        log("cancelled")
        if websocket:
            await websocket.close()


def _extract_line(x: str) -> str:
    with suppress(Exception):
        return "\n".join(l["line"] for l in json.loads(x)["data"]["data"]["lines"])
    return ""


def _is_completed(x: str) -> Optional[str]:
    with suppress(Exception):
        data = json.loads(x)["data"]
        if data["status"] == "completed" and "conclusion" in data:
            return f'job completed; conclusion: {data["conclusion"]}'
    return None


WS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Origin": "https://github.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "websocket",
    "Sec-Fetch-Site": "same-site",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}
