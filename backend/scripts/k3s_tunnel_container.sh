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
exec su-exec 10001:10001 /bin/sh /usr/local/bin/k3s-tunnel-session
