# action-cat

A __cursed__ way to have your cake and eat it too.

## Prerequisites

- python 3.12
- gh CLI
- a working Chrome-based browser under `/usr/bin/chromium`

## Installation

Clone the repo:

```
git clone https://github.com/rarescosma/action-cat.git
cd action-cat
```

Make a virtual environment, activate it, and install requirements:

```
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

Make sure `/usr/bin/chromium` points to a working Chrome-based browser.

If unsure, and on Arch:

```
paru ungoogled-chromium-bin
```

Run `mitmproxy` once and install its root certificate:

```
mitmproxy
^C

sudo trust anchor ~/.mitmproxy/mitmproxy-ca-cert.cer
```

## Usage

```
./main.py <commit_sha> <job_name>
```

Will look for an active action/job for the given `<commit_sha>` and `<job_name>`
and attempt via skull-crushing voodoo magic to tail its logs.

NOTE: the `<commit_sha>` has to be of the full 40 characters length.

## As a post-receive hook

A more clever way to use this curse is as a `post-receive` hook on a bare
git repository that proxies to the real GitHub origin.

See `post-receive.sample` for an example.
