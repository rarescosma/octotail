"""Mitmproxy actor."""

import base64
import copy
import json
import multiprocessing as mp
import sys
from argparse import Namespace
from contextlib import suppress
from multiprocessing.queues import Queue
from pathlib import Path
from queue import Empty
from threading import Event
from typing import cast

from mitmproxy.tools.main import mitmdump
from pykka import ActorRef, ThreadingActor
from returns.converters import result_to_maybe
from returns.maybe import Maybe, Nothing, Some
from returns.pipeline import flow
from returns.pointfree import map_
from returns.result import ResultE, Success, safe
from xdg.BaseDirectory import xdg_data_home

from octotail.manager import Manager
from octotail.msg import WsSub
from octotail.utils import Retry, debug, is_port_open, retries

MITM_CONFIG_DIR = Path(xdg_data_home) / "octotail" / "mitmproxy"
MARKERS = Namespace(
    ws_header="WebSocket text message",
    ws_host="alive.github.com",
    ws_action='"subscribe":',
)


class ProxyWatcher(ThreadingActor):
    """Watches for websocket subscriptions done through the mitmproxy."""

    mgr: ActorRef[Manager]
    port: int
    _stop_event: Event
    _proxy_ps: mp.Process
    _q: Queue[str]

    def __init__(self, mgr: ActorRef[Manager] | None, port: int):
        super().__init__()
        if mgr is not None:
            self.mgr = mgr
            self._stop_event = mgr.proxy().stop_event.get()
        self.port = port

    def on_start(self) -> None:
        self._q = mp.Queue()
        MITM_CONFIG_DIR.mkdir(exist_ok=True, parents=True)
        self._proxy_ps = mp.Process(target=_mitmdump_wrapper, args=(self._q, self.port))
        self._proxy_ps.start()

    def on_stop(self) -> None:
        self._proxy_ps.terminate()
        self._proxy_ps.join()

    def watch(self) -> None:
        if not _check_liveness(self._proxy_ps, self.port):
            self.mgr.tell("fatal: proxy didn't go live")
            self.mgr.stop()

        old_line, buffer = "-", []
        old_buffer: list[str] = []
        while not self._stop_event.is_set():
            with suppress(Empty):
                line = self._q.get(timeout=1).strip()
                if line:
                    buffer.append(line)
                elif old_line == "" and buffer:
                    _extract_ws_sub("".join(buffer), "".join(old_buffer)).apply(Some(self.mgr.tell))
                    old_buffer = copy.deepcopy(buffer)
                    buffer = []
                else:
                    buffer.append(line)
                old_line = line
        debug("exiting")


def _extract_ws_sub(buffer: str, old_buffer: str) -> Maybe[WsSub]:
    if not (
        MARKERS.ws_header in old_buffer
        and MARKERS.ws_host in old_buffer
        and MARKERS.ws_action in buffer
    ):
        return Nothing

    return flow(
        _extract_job_id(buffer),
        map_(
            lambda job_id: WsSub(
                url=old_buffer[old_buffer.index(MARKERS.ws_host) :],
                subs=buffer,
                job_id=cast(int, job_id),
            )
        ),
        result_to_maybe,
    )


@safe
def _extract_job_id(buffer: str) -> int:
    dec = (base64.b64decode(k) for k in json.loads(buffer)["subscribe"])
    items = (str(json.loads(d[: d.index(b"}") + 1].decode())["c"]) for d in dec)
    good = next(item for item in items if item.startswith("check_runs"))
    return int(good.split(":")[1])


def _mitmdump_wrapper(queue: Queue[str], port: int) -> None:
    sys.argv = (
        f"mitmdump --flow-detail=4 --no-rawtcp -p {port} --set confdir={MITM_CONFIG_DIR}".split()
    )
    # hijack .isatty and always return False to disable colors & shenanigans
    setattr(sys.stdout, "isatty", lambda: False)
    # hijack .write so lines go to our queue instead
    setattr(sys.stdout, "write", queue.put)
    mitmdump()


@retries(50, 0.2)
def _check_liveness(proxy_ps: mp.Process, port: int) -> ResultE[bool] | Retry:
    if not is_port_open(port):
        return Success(True)
    if not proxy_ps.is_alive():
        return Success(False)
    return Retry()
