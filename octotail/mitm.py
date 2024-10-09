"""Mitmproxy-related routines."""

import asyncio as aio
import json
import multiprocessing
import socket
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple, cast

from mitmproxy.http import HTTPFlow
from mitmproxy.io import FlowReader
from mitmproxy.tools.main import mitmdump

from .utils import log


async def start_proxy(proxy_file: Path) -> Callable:
    proxy_ps = multiprocessing.Process(target=_mitmdump_wrapper, args=(proxy_file,))
    proxy_ps.start()

    def _kill_proxy() -> None:
        proxy_ps.terminate()
        proxy_ps.join()

    if not await _check_proxy(proxy_ps):
        log("proxy didn't go live; bailing...")
        _kill_proxy()
        sys.exit(1)

    return _kill_proxy


def _mitmdump_wrapper(proxy_file: Path) -> None:
    proxy_file.parent.mkdir(parents=True, exist_ok=True)
    sys.argv = f"mitmdump -q -w {proxy_file}".split()
    mitmdump()


async def _check_proxy(proxy_ps: multiprocessing.Process) -> bool:
    for _ in range(0, 50):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        res = sock.connect_ex(("127.0.0.1", 8080))
        sock.close()
        if res == 0:
            return True
        await aio.sleep(0.2)
        if not proxy_ps.is_alive():
            return False
    return False


def get_websocket(proxy_file: Path) -> Optional[Tuple[str, str]]:
    if not proxy_file.exists() or not proxy_file.is_file():
        return None

    with open(proxy_file, "rb") as f:
        reader = FlowReader(f)
        wss = [
            flow
            for flow in cast(Iterable[HTTPFlow], reader.stream())
            if flow.websocket is not None and flow.request.host.startswith("alive.github.com")
        ]
        if not wss:
            return None
        flow = wss[-1]
        msgs = [m.text for m in flow.websocket.messages if "subscribe" in json.loads(m.text)]
        if not msgs:
            return None
        log(f"success: '{flow.request.url}'")
        return flow.request.url, msgs[0]
