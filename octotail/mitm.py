"""Mitmproxy actor."""

import base64
import copy
import json
import multiprocessing as mp
import sys
import typing as t
from argparse import Namespace
from contextlib import suppress
from dataclasses import dataclass, field
from multiprocessing.queues import Queue
from pathlib import Path
from queue import Empty
from threading import Event

from pykka import ActorRef, ThreadingActor, traversable
from returns.converters import result_to_maybe
from returns.maybe import Maybe, Nothing, Some
from returns.pipeline import flow
from returns.pointfree import map_
from returns.result import ResultE, Success, safe
from xdg.BaseDirectory import xdg_data_home

from octotail.manager import Manager
from octotail.msg import ProxyLive, WsSub
from octotail.utils import Retry, debug, is_port_open, retries

MITM_CONFIG_DIR = Path(xdg_data_home) / "octotail" / "mitmproxy"
MARKERS = Namespace(
    ws_header="WebSocket text message",
    ws_host="alive.github.com",
    ws_action='"subscribe":',
)


@dataclass
class BufferState:
    """Holds output buffer state."""

    buffer: list[str] = field(default_factory=list)
    old_buffer: list[str] = field(default_factory=list)
    old_line: str = "-"

    def process_line(self, line: str) -> Maybe[WsSub]:
        ret: Maybe[WsSub] = Maybe.from_optional(None)
        if line:
            self.buffer.append(line)
        elif self.old_line == "" and self.buffer:
            ret = _extract_ws_sub("".join(self.buffer), "".join(self.old_buffer))
            self.old_buffer = copy.deepcopy(self.buffer)
            self.buffer = []
        else:
            self.buffer.append(line)
        self.old_line = line
        return ret


class ProxyWatcher(ThreadingActor):
    """Watches for websocket subscriptions done through the mitmproxy."""

    mgr: ActorRef[Manager]
    port: int
    stop_event: Event

    queue: Queue[str] = traversable(mp.Queue())

    _state: BufferState
    _proxy_ps: mp.Process

    def __init__(self, mgr: ActorRef[Manager] | None, port: int):
        super().__init__()
        if mgr is not None:
            self.mgr = mgr
            self.stop_event = mgr.proxy().stop_event.get()
        self.port = port
        self._state = BufferState()

    def on_start(self) -> None:
        MITM_CONFIG_DIR.mkdir(exist_ok=True, parents=True)
        self._proxy_ps = run_mitmdump(self.queue, self.port)
        self._proxy_ps.start()

    def on_stop(self) -> None:
        self._proxy_ps.terminate()
        self._proxy_ps.join()

    def watch(self) -> None:
        if _check_liveness(self._proxy_ps, self.port).unwrap():
            self.mgr.tell(ProxyLive())
        else:
            self.mgr.tell("fatal: proxy didn't go live")
            self.mgr.stop()

        while not self.stop_event.is_set():
            with suppress(Empty):
                line = self.queue.get(timeout=0.25).strip()
                self._state.process_line(line).apply(Some(self.mgr.tell))
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
                job_id=t.cast(int, job_id),
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


def run_mitmdump(queue: Queue[str], port: int) -> mp.Process:  # pragma: no cover
    def _inner(_queue: Queue[str], _port: int) -> None:
        from mitmproxy.tools.main import mitmdump

        sys.argv = [
            "mitmdump",
            "--flow-detail=4",
            "--no-rawtcp",
            "-p",
            str(_port),
            "--set",
            f"confdir={MITM_CONFIG_DIR}",
        ]
        # hijack .isatty and always return False to disable colors & shenanigans
        setattr(sys.stdout, "isatty", lambda: False)
        # hijack .write so lines go to our queue instead
        setattr(sys.stdout, "write", _queue.put)
        mitmdump()

    return mp.Process(target=_inner, args=(queue, port))


@retries(50, 0.2)
def _check_liveness(proxy_ps: mp.Process, port: int) -> ResultE[bool] | Retry:  # pragma: no cover
    if not is_port_open(port):
        return Success(True)
    if not proxy_ps.is_alive():
        return Success(False)
    return Retry()
