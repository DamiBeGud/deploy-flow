#!/bin/sh
# Reconnect SSH inside the existing container/network namespace. MCP/worker
# containers share this namespace and must not lose it on an SSH disconnect.
set -eu
: "${DF_K3S_SSH_TARGET:?Set DF_K3S_SSH_TARGET}"
child_pid=
stop() {
  trap - TERM INT
  if [ -n "$child_pid" ]; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}
trap stop TERM INT

while :; do
  ssh -i /run/deployflow-runtime/ssh_key \
    -o BatchMode=yes -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile=/run/deployflow-runtime/known_hosts \
    -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -N -L 127.0.0.1:16443:127.0.0.1:6443 "$DF_K3S_SSH_TARGET" &
  child_pid=$!
  if wait "$child_pid"; then status=0; else status=$?; fi
  child_pid=
  printf 'SSH tunnel ended (status %s); reconnecting in 5 seconds.\n' "$status" >&2
  sleep 5 &
  child_pid=$!
  wait "$child_pid" || true
  child_pid=
done
