"""I'm the Baahwss."""

import dataclasses
import multiprocessing as mp
from multiprocessing.queues import JoinableQueue, Queue
from threading import Event

from github.WorkflowJob import WorkflowJob
from pykka import ThreadingActor

from octotail.msg import (
    BrowseRequest,
    CloseRequest,
    ExitRequest,
    JobDone,
    OutputItem,
    ProxyLive,
    StreamerMsg,
    VisitRequest,
    WorkflowDone,
    WsSub,
)
from octotail.streamer import run_streamer
from octotail.utils import debug

type MgrMessage = WorkflowJob | WsSub | JobDone | WorkflowDone | ProxyLive


class Manager(ThreadingActor):
    """I'm the Baahwss."""

    browse_queue: Queue[BrowseRequest]
    output_queue: JoinableQueue[StreamerMsg]
    stop_event: Event

    streamers: dict[int, mp.Process]
    job_map: dict[int, str]

    def __init__(
        self,
        browse_queue: Queue[BrowseRequest],
        output_queue: JoinableQueue[StreamerMsg],
        stop: Event,
    ):
        super().__init__()
        self.browse_queue = browse_queue
        self.output_queue = output_queue
        self.stop_event = stop

        self.streamers = {}
        self.job_map = {}

    def on_receive(self, message: MgrMessage) -> None:
        debug(f"{message!r}")

        match message:
            case ProxyLive() as proxy_live:
                self.browse_queue.put_nowait(proxy_live)

            case WorkflowJob() as job:
                self.browse_queue.put_nowait(VisitRequest(job.html_url, job.id))
                self.job_map[job.id] = job.name

            case WsSub() as ws_sub:
                self.browse_queue.put_nowait(CloseRequest(ws_sub.job_id))
                if ws_sub.job_id in self.job_map:
                    ws_sub = dataclasses.replace(ws_sub, job_name=self.job_map[ws_sub.job_id])
                self._replace_streamer(ws_sub.job_id, run_streamer(ws_sub, self.output_queue))

            case JobDone() as job:
                self.output_queue.put(OutputItem(job.job_name, [f"##[conclusion]{job.conclusion}"]))
                self._terminate_streamer(job.job_id)

            case WorkflowDone() as wf_done:
                self.output_queue.put(
                    OutputItem("workflow", [f"##[conclusion]{wf_done.conclusion}"])
                )
                self.stop()

    def on_stop(self) -> None:
        self.stop_event.set()
        self.browse_queue.put_nowait(ExitRequest())
        self.output_queue.put_nowait(None)
        for streamer in self.streamers.values():
            streamer.terminate()
        debug("manager exiting")

    def _terminate_streamer(self, job_id: int) -> None:
        if job_id in self.streamers:
            self.streamers[job_id].terminate()
            del self.streamers[job_id]

    def _replace_streamer(self, job_id: int, streamer: mp.Process) -> None:
        self._terminate_streamer(job_id)
        self.streamers[job_id] = streamer
