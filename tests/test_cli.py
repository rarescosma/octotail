from unittest.mock import MagicMock

import pytest
from typer import BadParameter, Typer
from typer.testing import CliRunner

from octotail import __version__
from octotail.cli import Opts, _noop, _post_init, _repo_callback, _sha_callback, entrypoint


@pytest.mark.parametrize(
    "inp, res",
    [
        ("", BadParameter),
        ("not-40-chars-long", BadParameter),
        ("babafacecacababafacecacababafacecacababa", "babafacecacababafacecacababafacecacababa"),
        ("0000000000000000000000000000000000000000", BadParameter),
    ],
)
def test_sha_callback(inp, res):
    if type(res) == type and issubclass(res, Exception):
        with pytest.raises(res):
            _sha_callback(inp)
    else:
        assert _sha_callback(inp) == res


@pytest.mark.parametrize(
    "inp, res",
    [
        (None, None),
        ("not-a-slash", BadParameter),
        ("user/repo", "user/repo"),
        ("under_scor3s-and-dashes/repo", "under_scor3s-and-dashes/repo"),
        ("under_scor3s-and-dashes/123_-ads", "under_scor3s-and-dashes/123_-ads"),
        ("a" * 100 + "/" + "b" * 100, "a" * 100 + "/" + "b" * 100),
        ("a" * 101 + "/" + "b" * 100, BadParameter),
        ("a" * 100 + "/" + "b" * 101, BadParameter),
    ],
)
def test_repo_callback(inp, res):
    if type(res) == type and issubclass(res, Exception):
        with pytest.raises(res):
            _repo_callback(inp)
    else:
        assert _repo_callback(inp) == res


def test_can_build_options():
    _callback = MagicMock()
    _post_init.set(_callback)
    opts = Opts(commit_sha="foo", gh_pat="bar", gh_user="baz", gh_pass="heh")
    _post_init.set(_noop)
    _callback.assert_called_once_with(opts)


def test_entrypoint():
    _callback = MagicMock()
    app = Typer()
    app.command()(Opts)

    runner = CliRunner()
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0
    assert __version__ in res.stdout

    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "Full commit SHA" in res.stdout

    res = runner.invoke(app, ["babafacecaca"])
    assert res.exit_code == 2
    assert "Invalid value" in res.stdout

    expected_opts = Opts(
        commit_sha="82e24ca0efbaa2cdd12454c6c0a2bba98a6f5e4e",
        gh_user="foo",
        gh_pass="bar",
        gh_pat="baz",
    )
    _post_init.set(_callback)
    res = runner.invoke(
        app,
        [
            "82e24ca0efbaa2cdd12454c6c0a2bba98a6f5e4e",
            "--gh-user=foo",
            "--gh-pass=bar",
            "--gh-pat=baz",
        ],
    )
    _post_init.set(_noop)
    assert res.exit_code == 0
    _callback.assert_called_once_with(expected_opts)
