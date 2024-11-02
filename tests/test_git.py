import pytest
from returns.io import IOFailure, IOSuccess
from returns.maybe import Some
from returns.result import Failure, Success

from octotail import git
from octotail.git import GitRemote


@pytest.mark.parametrize(
    ("input_lines", "expected"),
    [
        ("", Success([])),
        (
            "origin\tgit@github.com:getbettr/octotail.git",
            Success([GitRemote(name="origin", url="git@github.com:getbettr/octotail.git")]),
        ),
        (
            "origin git@github.com:getbettr/octotail.git",
            Success([GitRemote(name="origin", url="git@github.com:getbettr/octotail.git")]),
        ),
        (
            "origin       git@github.com:getbettr/octotail.git",
            Success([GitRemote(name="origin", url="git@github.com:getbettr/octotail.git")]),
        ),
        (
            "origin  \t  \t   git@github.com:getbettr/octotail.git",
            Success([GitRemote(name="origin", url="git@github.com:getbettr/octotail.git")]),
        ),
        (
            "gibberish",
            Failure(TypeError("GitRemote")),
        ),
        (
            "origin  \t  \t   git@github.com:getbettr/octotail.git"
            "\ngh-priv git@github.com:getbettr/octotail-priv.git",
            Success(
                [
                    GitRemote(name="gh-priv", url="git@github.com:getbettr/octotail-priv.git"),
                    GitRemote(name="origin", url="git@github.com:getbettr/octotail.git"),
                ]
            ),
        ),
        (
            "origin  \t  \t   git@github.com:getbettr/octotail.git"
            "\ngibberish"
            "\ngh-priv git@github.com:getbettr/octotail-priv.git",
            Failure(TypeError("GitRemote")),
        ),
    ],
)
def test_parse_remotes(input_lines, expected):
    got = git._parse_remotes(input_lines)
    if isinstance(expected, Failure):
        assert isinstance(got.failure(), type(expected.failure()))
        assert str(got.failure()).startswith(str(expected.failure()))
    else:
        assert got == expected


@pytest.mark.parametrize(
    ("input_remotes", "expected"),
    [
        ([], Failure(RuntimeError("no remotes"))),
        (["foo", "bar"], Failure(RuntimeError("too many remotes"))),
        (["foo"], Success("foo")),
    ],
)
def test_limit_remotes(input_remotes, expected):
    got = git._limit_remotes(input_remotes)
    if isinstance(expected, Failure):
        assert isinstance(got.failure(), type(expected.failure()))
        assert str(got.failure()).startswith(str(expected.failure()))
    else:
        assert got == expected


@pytest.mark.parametrize(
    ("input_remote", "expected"),
    [
        (GitRemote(name="foo", url=""), Failure(RuntimeError("failed to extract"))),
        (
            GitRemote(name="foo", url="git@github.com:getbettr/octotail-priv.git"),
            Success("getbettr/octotail-priv"),
        ),
        (
            GitRemote(name="foo", url="git@github.com:getbettr/octotail-priv"),
            Failure(RuntimeError("failed to extract")),
        ),
    ],
)
def test_extract_github_repo(input_remote, expected):
    got = git._extract_github_repo(input_remote)
    if isinstance(expected, Failure):
        assert isinstance(got.failure(), type(expected.failure()))
        assert str(got.failure()).startswith(str(expected.failure()))
    else:
        assert got == expected


@pytest.mark.parametrize(
    ("check_git_lines", "filter_fn", "expected"),
    [
        (IOSuccess(""), lambda _: True, IOSuccess([])),
        (
            IOSuccess("gh-priv git@github.com:getbettr/octotail-priv.git"),
            lambda _: True,
            IOSuccess([GitRemote(name="gh-priv", url="git@github.com:getbettr/octotail-priv.git")]),
        ),
        (
            IOFailure.from_failure("oops"),
            lambda _: True,
            IOFailure.from_failure("oops"),
        ),
        (
            IOSuccess(
                "gh-priv git@github.com:getbettr/octotail-priv.git"
                "\norigin  git@github.com:getbettr/octotail.git"
            ),
            lambda r: r.name == "origin",
            IOSuccess([GitRemote(name="origin", url="git@github.com:getbettr/octotail.git")]),
        ),
    ],
)
def test_get_remotes(monkeypatch, check_git_lines, filter_fn, expected):
    def _mock_check_git(*_, **__):
        return check_git_lines

    monkeypatch.setattr(git, "check_git", _mock_check_git)
    got = git.get_remotes(Some(filter_fn))
    if isinstance(expected, IOFailure):
        assert isinstance(got.failure(), type(expected.failure()))
        assert str(got.failure()).startswith(str(expected.failure()))
    else:
        assert got == expected


@pytest.mark.parametrize(
    ("check_git_lines", "expected"),
    [
        (
            IOSuccess(""),
            Failure.from_failure("no remotes"),
        ),
        (
            IOSuccess("gh-priv git@github.com:getbettr/octotail-priv.git"),
            IOSuccess("getbettr/octotail-priv"),
        ),
        (
            IOSuccess(
                "gh-priv git@github.com:getbettr/octotail-priv.git"
                "\norigin  git@github.com:getbettr/octotail.git"
            ),
            Failure.from_failure("too many remotes"),
        ),
    ],
)
def test_guess_github_repo(monkeypatch, check_git_lines, expected):
    def _mock_check_git(*_, **__):
        return check_git_lines

    monkeypatch.setattr(git, "check_git", _mock_check_git)
    got = git.guess_github_repo()
    if isinstance(expected, Failure):
        assert str(got.failure()._inner_value).startswith(str(expected.failure()))
    else:
        assert got == expected
