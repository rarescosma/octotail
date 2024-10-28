#!/usr/bin/env bash

set -eux

# install sudo
pacman -Sy --quiet --noconfirm sudo vi git binutils fakeroot debugedit openssh oath-toolkit

# install paru
PARU_VERSION=v2.0.4
curl --fail --location "https://github.com/Morganamilo/paru/releases/download/v2.0.4/paru-${PARU_VERSION}-x86_64.tar.zst" --output /tmp/paru.tar.zst
tar --directory "/usr/local/bin" -xvf "/tmp/paru.tar.zst" --wildcards "paru"
rm /tmp/paru.tar.zst

chmod -R +x /usr/local/bin

# add an user
USER_NAME=e2e
useradd -m $USER_NAME

# god mode
grep -q $USER_NAME /etc/sudoers || {
  echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" | EDITOR='tee -a' visudo
}

# install chromium + uv/uvx + gh
sudo -u $USER_NAME bash -x -c "paru -S --noconfirm ungoogled-chromium-bin uv github-cli; uv python install 3.12"

# yolo ssh
mkdir -p /home/$USER_NAME/.ssh
cat << __EOF__ > /home/${USER_NAME}/.ssh/config
Host *
   StrictHostKeyChecking no
   UserKnownHostsFile=/dev/null
   LogLevel ERROR
__EOF__
chown -R ${USER_NAME}: /home/$USER_NAME/.ssh

rm -rf /var/cache /var/lib/pacman /home/$USER_NAME/.cache
