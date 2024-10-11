"""Mitmproxy actor."""

import base64
import copy
import json
import multiprocessing
import sys
from argparse import Namespace
from contextlib import suppress
from dataclasses import dataclass
from queue import Empty
from threading import Event
from typing import List

from mitmproxy.tools.main import mitmdump
from pykka import ActorRef, ThreadingActor

from octotail.utils import Ok, Result, Retry, is_port_open, retries

MARKERS = Namespace(
    ws_header="WebSocket text message",
    ws_host="alive.github.com",
    ws_action='"subscribe":',
)


@dataclass(frozen=True)
class WsSub:
    """Represents a websocket subscription."""

    url: str
    subs: str
    job_id: int
    job_name: str | None = None


class ProxyWatcher(ThreadingActor):
    """Watches for websocket subscriptions done through the mitmproxy."""

    mgr: ActorRef
    stop: Event
    port: int
    _proxy_ps: multiprocessing.Process
    _q: multiprocessing.Queue

    def __init__(self, mgr: ActorRef, stop: Event, port: int):
        super().__init__()
        self.mgr = mgr
        self.stop = stop
        self.port = port

    def on_start(self) -> None:
        self._q = multiprocessing.Queue()
        self._proxy_ps = multiprocessing.Process(
            target=_mitmdump_wrapper, args=(self._q, self.port)
        )
        self._proxy_ps.start()

    def on_stop(self) -> None:
        self._proxy_ps.terminate()
        self._proxy_ps.join()

    def watch(self) -> None:
        if not _check_liveness(self._proxy_ps, self.port):
            self.mgr.tell(RuntimeError("fatal: proxy didn't go live"))
            self.stop.set()

        old_line, buffer = "-", []
        old_buffer: List[str] = []
        while not self.stop.is_set():
            with suppress(Empty):
                line = self._q.get(timeout=2).strip()
                if line:
                    buffer.append(line)
                elif old_line == "" and buffer:
                    self._process_buffer("".join(buffer), "".join(old_buffer))
                    old_buffer = copy.deepcopy(buffer)
                    buffer = []
                else:
                    buffer.append(line)
                old_line = line

    def _process_buffer(self, buffer: str, old_buffer: str) -> None:
        if (
            MARKERS.ws_header in old_buffer
            and MARKERS.ws_host in old_buffer
            and MARKERS.ws_action in buffer
        ):
            url = old_buffer[old_buffer.index(MARKERS.ws_host) :]
            if (job_id := _extract_job_id(buffer)) is not None:
                self.mgr.tell(WsSub(url=url, subs=buffer, job_id=job_id))


def _extract_job_id(buffer: str) -> int | None:
    with suppress(Exception):
        dec = (base64.b64decode(k) for k in json.loads(buffer)["subscribe"].keys())
        items = (str(json.loads(d[: d.index(b"}") + 1].decode())["c"]) for d in dec)
        good = next(item for item in items if item.startswith("check_runs"))
        return int(good.split(":")[1])
    return None


def _mitmdump_wrapper(q: multiprocessing.Queue, port: int) -> None:
    sys.argv = f"mitmdump --flow-detail=4 --no-rawtcp -p {port}".split()
    setattr(sys.stdout, "isatty", lambda: False)
    setattr(sys.stdout, "write", q.put)
    mitmdump()


@retries(50, 0.2)
def _check_liveness(proxy_ps: multiprocessing.Process, port: int) -> Result[bool] | Retry:
    if not is_port_open(port):
        return Ok(True)
    if not proxy_ps.is_alive():
        return Ok(False)
    return Retry()
