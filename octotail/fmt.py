"""
Routines for pretty formatting the output.
"""

import multiprocessing as mp
from contextlib import suppress
from functools import partial
from queue import Empty
from typing import Callable, Dict, Generator, List

from pykka import ThreadingActor
from termcolor import colored
from termcolor._types import Color

from octotail.streamer import OutputItem
from octotail.utils import debug, flatmap, remove_consecutive_falsy

WHEEL: List[Color] = [
    "light_green",
    "light_yellow",
    "light_blue",
    "light_magenta",
    "light_cyan",
]


class Formatter(ThreadingActor):
    """The output formatting actor."""

    queue: mp.JoinableQueue
    _wheel_idx: int
    _color_map: Dict[str, int]

    def __init__(self, queue: mp.JoinableQueue):
        super().__init__()
        self.queue = queue
        self._wheel_idx = 0
        self._color_map = {}

    def _get_color(self, group: str) -> Color:
        if group == "workflow":
            return "white"

        if group not in self._color_map:
            self._color_map[group] = self._wheel_idx
            self._wheel_idx = (self._wheel_idx + 1) % len(WHEEL)
        return WHEEL[self._color_map[group]]

    def print_lines(self) -> None:
        while True:
            # gives us a chance to check the stop event
            with suppress(Empty):
                item = self.queue.get(timeout=2)
                self.queue.task_done()
                if item is None:
                    break
                print("\n".join(self._handle_item(item)))
        debug("exiting")

    def _handle_item(self, item: OutputItem) -> Generator[str, None, None]:
        _colored = partial(colored, color=self._get_color(item.job_name), force_color=True)
        _decorate = partial(_decorate_line, job_name=item.job_name, _colored=_colored)

        prefix = _colored(f"[{item.job_name}]:")
        return (
            f"{prefix} {line}" for line in remove_consecutive_falsy(flatmap(_decorate, item.lines))
        )


def _decorate_line(line: str, job_name: str, _colored: Callable) -> List[str]:
    if line.startswith("[command]"):
        return [colored("$ " + line.removeprefix("[command]"), "white", force_color=True)]

    if line.startswith("##[group]"):
        unprefixed = line.removeprefix("##[group]")
        rem = _colored(unprefixed, attrs=["bold"])
        sep = _colored("⎯" * (80 - len(f"remote: [{job_name}]: ") - len(unprefixed) - 6))
        return ["", f"{_colored('⎯⎯')}  {rem}  {sep}"]

    if line.startswith("##[endgroup]"):
        sep = _colored("⎯" * (80 - len(f"remote: [{job_name}]: ")))
        return [sep, ""]

    if line.startswith("##[conclusion]"):
        unprefixed = line.removeprefix("##[conclusion]").lower()
        color: Color = (
            "green" if unprefixed == "success" else "red" if unprefixed == "failure" else "yellow"
        )
        _conc_colored = partial(colored, color=color, attrs=["bold"], force_color=True)
        return ["", _conc_colored(f"Conclusion: {unprefixed.upper()}"), ""]

    return [_colored(line)]
