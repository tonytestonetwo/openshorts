#!/bin/sh
set -eu

public_key="${SSH_PUBLIC_KEY:-${PUBLIC_KEY:-}}"
if [ -z "$public_key" ]; then
    echo "Runpod did not provide an SSH public key; refusing to start without SSH access." >&2
    exit 1
fi

install -d -m 700 /root/.ssh
printf '%s\n' "$public_key" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

ssh-keygen -A
mkdir -p /run/sshd
/usr/sbin/sshd -e

exec gosu appuser python /app/run_services.py
