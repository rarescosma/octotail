import io
import multiprocessing as mp
import re
import threading
from unittest.mock import MagicMock

import pytest
from termcolor import COLORS

from octotail.fmt import WHEEL, Formatter
from octotail.msg import OutputItem, WebsocketClosed

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _bleach(x: str) -> str:
    return ANSI_ESCAPE.sub("", x)


def test_get_color():
    # Should cycle through all colors (but also wrap)
    sut = Formatter(mgr=MagicMock(), queue=MagicMock())
    colors = {sut._get_color(str(i)) for i in range(len(WHEEL) * 2)}
    assert colors == set(WHEEL)
    assert all(c in COLORS for c in sorted(colors))

    # Should color workflow differently
    wf_color = sut._get_color("workflow")
    assert wf_color in COLORS
    assert wf_color not in WHEEL

    # Should re-assign colors
    assert sut._get_color("foo") == sut._get_color("foo")


@pytest.mark.parametrize(
    "items, output, called_stop",
    [
        ([None], "", False),
        (
            [OutputItem(job_name="foo", lines=["bar", "baz"]), None],
            "[foo]: bar\n" "[foo]: baz",
            False,
        ),
        (
            [OutputItem(job_name="foo", lines=["bar", "baz"]), "ignored", WebsocketClosed()],
            "[foo]: bar\n" "[foo]: baz",
            True,
        ),
        (
            [OutputItem(job_name="foo", lines=["[command]bar", "baz"]), None],
            "[foo]: $ bar\n" "[foo]: baz",
            False,
        ),
        (
            [OutputItem(job_name="foo", lines=["##[group]bar", "baz"]), None],
            "[foo]: \n"
            "[foo]: --  bar  --------------------------------------------------------\n"
            "[foo]: baz",
            False,
        ),
        (
            [OutputItem(job_name="foo", lines=["##[error]bar", "baz"]), None],
            "[foo]: \n" "[foo]: Error: bar\n" "[foo]: \n[foo]: baz",
            False,
        ),
        (
            [OutputItem(job_name="foo", lines=["##[endgroup]bar", "baz"]), None],
            "[foo]: -----------------------------------------------------------------\n"
            "[foo]: \n"
            "[foo]: baz",
            False,
        ),
        (
            [OutputItem(job_name="foo", lines=["##[conclusion]bar", "baz"]), None],
            "[foo]: \n" "[foo]: Conclusion: BAR\n" "[foo]: \n" "[foo]: baz",
            False,
        ),
    ],
)
def test_print_lines(items, output, called_stop):
    queue = mp.JoinableQueue()
    mgr = MagicMock()
    sut = Formatter.start(mgr=mgr, queue=queue)
    capture = io.StringIO()

    def _start_print_lines():
        sut.proxy().file = capture
        sut.proxy().print_lines().get()

    try:
        thread = threading.Thread(target=_start_print_lines)
        thread.start()
        for item in items:
            queue.put(item)
        queue.join()
        assert _bleach(capture.getvalue()).strip() == output
        if called_stop:
            mgr.stop.assert_called_once()
        thread.join()
    finally:
        sut.stop()
