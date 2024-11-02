#!/usr/bin/env bash

set -euo pipefail

DOT=$(cd -P "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)
sudo chown -R $(id -u):$(id -g) $DOT

OCTOTAIL_REF="${1:-main}"

UVX="uvx --from=git+https://github.com/getbettr/octotail.git@${OCTOTAIL_REF}"

# generate mitmproxy cert and trust it
sudo trust anchor "$($UVX octotailx generate-cert)"

test -d octotail-e2e && rm -rf octotail-e2e

# git setup
git config --global user.email "hubber-e2e@getbetter.ro"
git config --global user.name "hubber-e2e-gb"
echo $HUBBER_KEY | base64 -d >$HOME/.ssh/id_rsa
chmod 400 $HOME/.ssh/id_rsa

git clone git@github.com:getbettr/octotail-e2e.git

# push an empty commit
pushd octotail-e2e
git commit --allow-empty -m "$(date "+%F@%T") e2e trigger: ${OCTOTAIL_REF}"
git push origin --force

# octotail setup
echo $HUBBER_CREDS | base64 -d >/tmp/hubber-creds
source /tmp/hubber-creds
rm -f /tmp/hubber-creds
export OCTOTAIL_GH_OTP="$(eval $OCTOTAIL_GH_OTP_CMD | tr -d '\n')"
export DEBUG=1
export PYTHONUNBUFFERED=1

# look for marker and cancel ongoing run
CANCELLED=""
while IFS= read -r line; do
  echo "[octotail]: $line"
  if [[ "$line" == *"%%octotail_marker%%"* ]] && test -z "$CANCELLED"; then
    # remove colors with sed
    RUN_ID="$(echo ${line##*: } | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,2};?)?)?[mGK]//g")"
    echo "[run.sh]: Marker found! Run ID is: $RUN_ID"
    gh auth login --with-token < <(echo $OCTOTAIL_GH_PAT)
    gh run cancel $RUN_ID
    CANCELLED=1
  fi
done < <($UVX octotail $(git rev-parse HEAD))

wait
