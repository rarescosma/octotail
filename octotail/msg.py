"""Types used by actors communication."""

import typing as t
from dataclasses import dataclass


class _Marker:
    def __eq__(self, other: t.Any) -> bool:
        return isinstance(other, self.__class__)

    def __repr__(self) -> str:
        return self.__class__.__name__


class ProxyLive(_Marker):
    """Sent by the proxy watcher to indicate the proxy is live."""


class VisitRequest(t.NamedTuple):
    """Sent to the browser to request visiting of a job page."""

    url: str
    job_id: int


class CloseRequest(t.NamedTuple):
    """Sent to the browser to request closing of a job page."""

    job_id: int


class ExitRequest(_Marker):
    """Sent to the browser to quit."""


type BrowseRequest = VisitRequest | CloseRequest | ExitRequest | ProxyLive


class OutputItem(t.NamedTuple):
    """Sent by streamers on new websockets content."""

    job_name: str
    lines: list[str]


class WebsocketClosed(_Marker):
    """Sent by streamers to indicate the closure of a websocket."""


type StreamerMsg = OutputItem | WebsocketClosed | None


@dataclass(frozen=True)
class WsSub:
    """Sent by mitm.ProxyWatcher: represents an extracted websocket subscription."""

    url: str
    subs: str
    job_id: int
    job_name: str | None = None


class WorkflowDone(t.NamedTuple):
    """Sent by gh.RunWatcher to indicate a workflow concluded."""

    conclusion: str


class JobDone(t.NamedTuple):
    """Sent by gh.RunWatcher to indicate a job concluded."""

    job_id: int
    job_name: str
    conclusion: str
