import importlib
import io
from unittest.mock import patch

import pytest
from returns.io import impure_safe
from returns.result import Failure, Success

from octotail import utils
from octotail.utils import Retry, perform_io


@pytest.mark.parametrize(
    ("f", "xs", "res"),
    [
        (lambda a: [a, a + 1], [1, 2, 3], [1, 2, 2, 3, 3, 4]),
        (lambda a: [a], [1, 2, 3], [1, 2, 3]),
        (lambda a: [a], [], []),
        (lambda a: [], [1, 2], []),
    ],
)
def test_flatmap(f, xs, res):
    assert list(utils.flatmap(f, xs)) == res


@pytest.mark.parametrize(
    ("line", "skip_prefix", "res"),
    [
        ("", True, ""),
        ("trivial", True, "trivial"),
        ("not so trivial", False, "[test_utils:test_log]: not so trivial"),
    ],
)
def test_log(line: str, skip_prefix: bool, res: str):
    capture = io.StringIO()
    utils.log(line, file=capture, skip_prefix=skip_prefix)
    assert capture.getvalue().strip() == res


def test_log_no_module():
    capture = io.StringIO()
    with patch("inspect.getmodule", lambda *_, **__: None):
        utils.log("something", file=capture)
    assert capture.getvalue().strip() == "[?:test_log_no_module]: something"


def test_log_offstack():
    capture = io.StringIO()
    utils.log("something", file=capture, stack_offset=10000)
    assert capture.getvalue().strip() == "[pytest:<module>]: something"


@pytest.mark.parametrize(
    ("line", "debug_on", "res"),
    [
        ("trivial", False, ""),
        ("", True, "[test_utils:test_debug]:"),
    ],
)
def test_debug(monkeypatch, line: str, debug_on: bool, res: str):
    monkeypatch.setenv("DEBUG", "1" if debug_on else "0")
    importlib.reload(utils)
    capture = io.StringIO()
    utils.debug(line, file=capture)
    assert capture.getvalue().strip() == res


def test_perform_io():
    @impure_safe
    def _inner() -> str:
        return "foo"

    assert perform_io(_inner)() == Success("foo")


@pytest.mark.parametrize(
    ("num_tries", "values", "res"),
    [
        (0, [], Failure(RuntimeError("retries exceeded"))),
        (1, [Success("lucky one")], Success("lucky one")),
        (9, [Failure("nope"), Success("lucky one")], Failure("nope")),
        (2, [Retry(), Success("lucky one")], Success("lucky one")),
        (2, [Retry(), Retry(), Success("lucky one")], Failure(RuntimeError("retries exceeded"))),
        (2, [Retry(), Success("lucky one"), Success("not it")], Success("lucky one")),
        (3, ["foo", "bar", Success("baz")], Success("baz")),
    ],
    ids=[
        "didnt-even-try",
        "first-try",
        "fails-if-failure-returned",
        "retries",
        "not-enough-retries",
        "retries-returns-first-success",
        "spurious-values-retry-without-sleep",
    ],
)
def test_retries(num_tries: int, values: list, res):
    def __generator():
        yield from values

    _generator = __generator()

    @utils.retries(num_tries, 0)
    def _inner():
        return next(_generator)

    got = _inner()
    if isinstance(res, Failure):
        assert isinstance(got.failure(), type(res.failure()))
        assert str(got.failure()).startswith(str(res.failure()))
    else:
        assert got == res


def test_find_free_port():
    port = utils.find_free_port(max_port=65000)
    assert utils.is_port_open(port)


def test_cannot_find_free_port(bound_socket):
    port = utils.find_free_port(max_port=65000)
    with bound_socket(port):
        assert utils.find_free_port(min_port=port, max_port=port) is None
    assert utils.is_port_open(port)


@pytest.mark.parametrize(
    ("xs", "res"),
    [
        ([], []),
        (["", ""], [""]),
        (["foo", ""], ["foo", ""]),
        (["foo", "", False, "bar"], ["foo", "", "bar"]),
        (["foo", *([""] * 100), "bar"], ["foo", "", "bar"]),
    ],
)
def test_remove_consecutive_falsy(xs, res):
    assert list(utils.remove_consecutive_falsy(xs)) == res
