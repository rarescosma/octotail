#!/usr/bin/env -S /bin/sh -c 'exec "$(dirname $(readlink -f "$0"))/../.venv/bin/python3" "$0" "$@"'

"""Helper scripts for octotail such as certificate generation, etc."""
import sys
import time
from pathlib import Path
from unittest.mock import patch

from rich.panel import Panel
from typer import Typer
from xdg.BaseDirectory import xdg_data_home

from octotail.cli import NO_FRILLS, NO_RICH
from octotail.utils import debug, find_free_port, log

GENERATE_CERT_TRIES = 25
PROXY_REPOS = Path(xdg_data_home) / "octotail" / "proxy_repos"
DOT = Path().resolve()


app = Typer(
    add_completion=False,
    rich_markup_mode=(None if NO_RICH else "markdown"),
    pretty_exceptions_show_locals=False,
)


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

    log(f"{cert_file}", skip_prefix=True)
    mitm.stop()


@app.callback(no_args_is_help=True)
def show_help() -> None:
    pass


def _main() -> None:
    with patch(
        "typer.rich_utils.Panel",
        lambda *args, **kwargs: Panel(*args, **{**kwargs, "box": NO_FRILLS}),
    ) as _:
        app()


if __name__ == "__main__":
    _main()
