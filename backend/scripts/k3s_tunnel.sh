#!/usr/bin/env bash
# Private local-development route to the k3s API. A scoped kubeconfig is still required.
set -euo pipefail

target=${DF_K3S_SSH_TARGET:?Set DF_K3S_SSH_TARGET to user@host}
key=${DF_K3S_SSH_KEY:-$HOME/.ssh/id_ed25519}
port=${DF_K3S_LOCAL_PORT:-16443}
if [[ ! $target =~ ^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+$ ]]; then
  echo "Invalid DF_K3S_SSH_TARGET" >&2
  exit 2
fi
if [[ ! $port =~ ^[0-9]+$ ]] || (( port < 1024 || port > 65535 )); then
  echo "Invalid DF_K3S_LOCAL_PORT" >&2
  exit 2
fi

exec ssh -i "$key" -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -N -L "127.0.0.1:$port:127.0.0.1:6443" "$target"
