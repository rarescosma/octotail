# action-cat

Live tail GitHub Action runs on `git push`. It's cursed.

<img src="https://github.com/user-attachments/assets/dc4a218f-cae1-4fc5-9f0b-c32a3ecb7e93" width="1200px">

## Prerequisites

- python 3.12
- gh CLI
- a working Chrome-based browser under `/usr/bin/chromium`

## Installation

Clone the repo:

```shell
git clone https://github.com/rarescosma/action-cat.git
cd action-cat
```

Make a virtual environment, activate it, and install requirements:

```shell
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

Make sure `/usr/bin/chromium` points to a working Chrome-based browser.

If unsure, and on Arch:

```shell
paru ungoogled-chromium-bin
```

Run `mitmproxy` once and install its root certificate:

```shell
mitmproxy
^C

sudo trust anchor ~/.mitmproxy/mitmproxy-ca-cert.cer
```

## Usage

```shell
./main.py <commit_sha> <job_name>
```

Will look for an active action/job for the given `<commit_sha>` and `<job_name>`
and attempt via skull-crushing voodoo magic to tail its logs.

_NOTE:_ the `<commit_sha>` has to be of the full 40 characters length.

## As a post-receive hook

A slightly more advanced use case that lets you stream the job outputs on
`git push`, similar to how you get the test runs results when pushing
to [Codecrafters][].

For this to work we'll need control over the remote's output, so we can't use
the GitHub remote directly. Instead, we'll use a bare repository a our "proxy"
remote and set up its post-receive hook to call this cursed script.

```shell
export PROXY_REPO="${HOME}/src/proxy-repo"
git init --bare $PROXY_REPO

cd your-original-repo
export ORIG_REMOTE="$(git remote get-url origin)"
git remote add proxy $PROXY_REPO
git push proxy --all
cd -

cp post-receive.sample $PROXY_REPO/hooks/post-receive
cd $PROXY_REPO
git remote add origin $ORIG_REMOTE
cd -
```

Edit `$PROXY_REPO/hooks/post-receive` and change things according to 
your setup:

- set `_ACTION_CAT` to the path where you actually cloned this repo
- set `_GH_USER` to your GitHub username
- set `_GH_PASS_CMD` to a command that outputs the GitHub password, e.g. 
  `_GH_PASS="pass github"`
- _if using 2FA_ - set `_GH_TOKEN_CMD` to a command that outputs an OTP token 
  for the GitHub 2FA, e.g. `_GH_PASS="totp github"`
- set `_JOB_NAME` to the name of the job you want to tail
- replace `"refs/heads/main"` with `refs/tags/*` (without the quotes) if
  you expect the job to run on tags

NOTE: the hook assumes you're using `zsh`, change the shebang to your own shell,
just make sure to invoke it with the right flags to get an interactive, login
shell. Useful to get access to custom functions and aliases.

That's it! (phew) - no try pushing some commits to the `proxy` remote and check
if you get the GitHub action logs streaming right back:

```shell
cd your-original-repo
git commit --allow-empty -m 'test action-cat'
git push proxy
```

[Codecrafters]: https://codecrafters.io/
