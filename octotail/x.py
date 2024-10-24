#!/usr/bin/env -S /bin/sh -c 'exec "$(dirname $(readlink -f "$0"))/../.venv/bin/python3" "$0" "$@"'

"""Helper scripts for octotail such as certificate generation, etc."""
import os
import stat
import sys
import time
import typing as t
from pathlib import Path
from unittest.mock import patch

import shellingham
from returns.pipeline import is_successful
from rich import print as rprint
from rich.panel import Panel
from rich.prompt import InvalidResponse, Prompt
from typer import Option, Typer
from xdg.BaseDirectory import xdg_data_home

from octotail.cli import NO_FRILLS, NO_RICH, version_callback
from octotail.git import check_git, get_remotes, get_repo_dir
from octotail.utils import debug, find_free_port, perform_io

GENERATE_CERT_TRIES = 25
PROXY_REPOS = Path(xdg_data_home) / "octotail" / "proxy_repos"
DOT = Path().resolve()


app = Typer(
    add_completion=False,
    rich_markup_mode=(None if NO_RICH else "markdown"),
    pretty_exceptions_show_locals=False,
)


class NonEmptyPrompt(Prompt):
    """A Prompt that doesn't accept empty answers."""

    def process_response(self, value: str) -> str:
        if not value.strip():
            raise InvalidResponse("[red]Please enter a non-empty value[/red]")
        return super().process_response(value)


hook_template = """#!/usr/bin/env -S {shell} -li

git push origin --mirror

COMMIT=""; REF_NAME=""; CNT=0
while read old_oid new_oid ref_name; do
  COMMIT="$new_oid"; REF_NAME="$ref_name"; CNT=$(( CNT + 1 ))
done

if [[ $CNT -ge 2 ]]; then
  echo "pushed more than one ref; cannot tail"
  exit 0
fi

echo "commit: $COMMIT"
echo "ref_name: $REF_NAME"

export PYTHONUNBUFFERED=1
export OCTOTAIL_HEADLESS=1

export OCTOTAIL_GH_PAT="$(eval "{gh_pat_cmd}")"
export OCTOTAIL_GH_PASS="$(eval "{gh_pass_cmd}")"
if ! test -z "{gh_otp_cmd}"; then
  export OCTOTAIL_GH_OTP="$(eval "{gh_otp_cmd}")"
fi
DEBUG=1 {octotail_cmd} $COMMIT --ref-name $REF_NAME --gh-user "{gh_user}"
"""


@app.command()
def generate_cert() -> None:
    """Start mitmproxy and wait until a certificate is generated."""
    from octotail.mitm import MITM_CONFIG_DIR, ProxyWatcher

    port = find_free_port()
    mitm = ProxyWatcher.start(None, port)
    cert_file = MITM_CONFIG_DIR / "mitmproxy-ca-cert.cer"
    tries = 0
    while (not cert_file.exists()) or (not cert_file.stat().st_size):
        if tries > GENERATE_CERT_TRIES:
            debug("giving up waiting for mitmproxy to generate a certificate")
            sys.exit(1)
        time.sleep(0.2)
        tries += 1

    rprint(f"[green]{cert_file}[/green]")
    mitm.stop()


@app.command()
def install_proxy_remote() -> None:  # noqa: PLR0915
    """Install an octotail proxy remote for the current git repository."""
    repo_dir_res = perform_io(get_repo_dir)()
    if not is_successful(repo_dir_res):
        rprint("fatal: could find the repository directory")
        sys.exit(1)
    repo_dir: Path = repo_dir_res.unwrap()

    remotes_res = perform_io(get_remotes)()
    if not is_successful(remotes_res):
        rprint("fatal: could not list repository remotes")
        sys.exit(1)

    github_remotes = [r for r in remotes_res.unwrap() if "github.com" in r.url]
    if not github_remotes:
        rprint(
            f"fatal: expected repository to have at least one github remote;"
            f" found: {remotes_res.unwrap()}"
        )
        sys.exit(1)

    if len(github_remotes) > 1:
        for n, _remote in enumerate(github_remotes):
            rprint(f"{n + 1}. {_remote.url} ({_remote.name})")
        _idx = Prompt.ask(
            "Which remote should I clone from?",
            choices=[str(_ + 1) for _ in range(len(github_remotes))],
        )
        github_remote = github_remotes[int(_idx) - 1]
    else:
        github_remote = github_remotes[0]

    if any(r.name == "proxy" for r in remotes_res.unwrap()):
        rprint("fatal: there is already a remote named 'proxy' for this repo")

    PROXY_REPOS.mkdir(exist_ok=True, parents=True)
    proxy_repo_path = PROXY_REPOS / repo_dir.name

    if proxy_repo_path.exists():
        rprint(f"fatal: {proxy_repo_path} already exists")
        sys.exit(1)

    rprint(
        "[green]Cloning the original repo to the proxy remote."
        " You might get asked for your SSH password.[/green]"
    )
    clone_result = perform_io(check_git)(f"clone --mirror {github_remote.url} {proxy_repo_path}")
    if not is_successful(clone_result):
        rprint(f"fatal: clone failed: {clone_result.failure()}")
        sys.exit(1)

    rprint(f"[green]Clone successful. Adding {proxy_repo_path} as the 'proxy' remote.[/green]")

    add_remote = perform_io(check_git)(f"remote add proxy {proxy_repo_path}")
    if not is_successful(add_remote):
        rprint(f"fatal: could not add the proxy remote: {add_remote.failure()}")
        sys.exit(1)

    def _inject_default(env_var: str) -> t.Any:
        val = os.getenv(env_var)
        if val is None:
            return {}
        return {"default": val}

    gh_user = NonEmptyPrompt.ask(
        "Enter your github.com username", **_inject_default("OCTOTAIL_GH_USER")
    )
    gh_pass_cmd = NonEmptyPrompt.ask(
        "Enter [bold]a command[/bold] that will output your GitHub password",
        **_inject_default("OCTOTAIL_GH_PASS_CMD"),
    )
    gh_otp_cmd = Prompt.ask(
        "If using 2FA, enter [bold]a command[/bold] that will output the OTP token."
        " Leave blank if not using 2FA",
        **_inject_default("OCTOTAIL_GH_OTP_CMD"),
    )
    gh_pat_cmd = NonEmptyPrompt.ask(
        "Enter [bold]a command[/bold] that will output a GitHub personal access token",
        **_inject_default("OCTOTAIL_GH_PAT_CMD"),
    )

    hook_path = proxy_repo_path / "hooks" / "post-receive"

    match os.getenv("_", ""):
        case s if s.endswith("/uvx"):
            octotail_cmd = "uvx --from=octotail octotail"
        case s if s.endswith("/octotailx"):
            octotail_cmd = s[:-1]
        case _:
            octotail_cmd = "octotail"

    hook_path.write_text(
        hook_template.format(
            shell=shellingham.detect_shell()[0],
            gh_user=gh_user,
            gh_pass_cmd=gh_pass_cmd,
            gh_pat_cmd=gh_pat_cmd,
            gh_otp_cmd=gh_otp_cmd,
            octotail_cmd=octotail_cmd,
        )
    )

    # ensure hook is executable
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@app.callback(no_args_is_help=True)
def _show_help(
    _version: t.Annotated[
        bool | None,
        Option(
            "--version",
            help="Show the version and exit.",
            callback=version_callback,
            is_eager=True,
            rich_help_panel="Options",
        ),
    ] = None
) -> None:
    pass


def _main() -> None:
    with patch(
        "typer.rich_utils.Panel",
        lambda *args, **kwargs: Panel(*args, **{**kwargs, "box": NO_FRILLS}),
    ) as _:
        app()


if __name__ == "__main__":
    _main()
