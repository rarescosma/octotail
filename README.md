# octotail

Live tail GitHub Action runs on `git push`. It's cursed.

<img src="https://github.com/user-attachments/assets/1813ffc3-a101-48b5-92c2-634c2468670d" width="1200px">

## Prerequisites

- python 3.12
- a working Chrome-based browser under `/usr/bin/chromium`

## Installation

Clone the repo:

```shell
git clone https://github.com/rarescosma/octotail.git
cd octotail
```

Make a virtual environment, activate it, and install requirements:

```shell
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

Install the package itself:

```shell
pip3 install -e .
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

```
# octotail --help
Usage: octotail [OPTIONS] COMMIT_SHA WORKFLOW

Arguments:
  COMMIT_SHA  [required]
  WORKFLOW    [required]

Options:
  --gh-user TEXT              [env var: _GH_USER; required]
  --gh-pass TEXT              [env var: _GH_PASS; required]
  --gh-otp TEXT               [env var: _GH_OTP; required]
  --gh-pat TEXT               [env var: _GH_PAT; required]
  --headless / --no-headless  [env var: _HEADLESS; default: headless]
  --help                      Show this message and exit.
```

Will look for an active run for the given `<commit_sha>` and `<workflow>`
and attempt via skull-crushing voodoo magic to tail its logs.

_NOTE:_ the `<commit_sha>` has to be of the full 40 characters length.

## As a post-receive hook

A slightly more advanced use case that lets you stream the run outputs on
`git push`, similar to how you get the test runs results when pushing
to [Codecrafters][].

For this to work we'll need control over the remote's output, so we can't use
the GitHub remote directly. Instead, we'll use a bare repository a our "proxy"
remote and set up its post-receive hook to call this cursed script.

```shell
cd your-original-repo
export ORIG_REMOTE="$(git remote get-url origin)"
export PROXY_REPO="${HOME}/src/proxy-repo"

git clone --mirror $ORIG_REMOTE $PROXY_REPO
git remote add proxy $PROXY_REPO
# back to octotail
cd -

cp post-receive.sample $PROXY_REPO/hooks/post-receive
```

Edit `$PROXY_REPO/hooks/post-receive` and change things according to 
your setup:

- set `_ACTION_CAT` to the path where you actually cloned this repo
- set `_GH_USER` to your GitHub username
- set `_GH_PASS_CMD` to a command that outputs the GitHub password, e.g. 
  `_GH_PASS_CMD="pass github.com"`
- _if using 2FA_ - set `_GH_OTP_CMD` to a command that outputs an OTP token 
  for the GitHub 2FA, e.g. `_GH_OTP_CMD="totp github.com"`
- set `_GH_PAT_CMD` to a command that outputs your GitHub PAT token, e.g.
  `_GH_PAT_CMD="pass github_pat"`
- set `_WORKFLOW` to the name of the workflow whose runs you want to tail
- replace `"refs/heads/main"` with `refs/tags/*` (without the quotes) if
  you expect the workflow to run on tags

NOTE: the hook assumes you're using `zsh`. You can change the shebang to your 
own shell, but you might want to invoke it with the right flags to get an 
interactive, login shell. Useful to get access to custom functions and aliases.

That's it! (phew) - now try pushing some commits to the `proxy` remote and check
if you get the GitHub Actions run logs streaming right back:

```shell
cd your-original-repo
git commit --allow-empty -m 'test octotail'
git push proxy
```

[Codecrafters]: https://codecrafters.io/
