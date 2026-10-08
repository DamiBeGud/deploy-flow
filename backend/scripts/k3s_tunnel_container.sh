#!/bin/sh
set -eu
: "${DF_K3S_SSH_TARGET:?Set DF_K3S_SSH_TARGET}"
mkdir -p /run/deployflow-runtime
chown 10001:10001 /run/deployflow-runtime
chmod 0700 /run/deployflow-runtime
cp /run/deployflow/ssh_key /run/deployflow-runtime/ssh_key
cp /run/deployflow/known_hosts /run/deployflow-runtime/known_hosts
chown 10001:10001 /run/deployflow-runtime/ssh_key /run/deployflow-runtime/known_hosts
chmod 0600 /run/deployflow-runtime/ssh_key /run/deployflow-runtime/known_hosts
exec su-exec 10001:10001 ssh -i /run/deployflow-runtime/ssh_key \
  -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/run/deployflow-runtime/known_hosts \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -N -L 127.0.0.1:16443:127.0.0.1:6443 "$DF_K3S_SSH_TARGET"
