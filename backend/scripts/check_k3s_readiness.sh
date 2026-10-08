#!/usr/bin/env bash
# Read-only P0-01 checks. Run from a machine with SSH access to the k3s host.
set -euo pipefail

target=${DF_K3S_SSH_TARGET:?Set DF_K3S_SSH_TARGET to user@host}
key=${DF_K3S_SSH_KEY:-$HOME/.ssh/id_ed25519}
if [[ ! $target =~ ^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+$ ]]; then
  echo "Invalid DF_K3S_SSH_TARGET" >&2
  exit 2
fi

remote() {
  ssh -i "$key" -o BatchMode=yes -o ConnectTimeout=10 "$target" "$1"
}

kctl() {
  remote "sudo -n k3s kubectl $1"
}

expect() {
  local label=$1 expected=$2 actual=$3
  if [[ $actual != "$expected" ]]; then
    echo "FAIL $label: expected $expected, got $actual" >&2
    exit 1
  fi
  echo "PASS $label: $actual"
}

echo "Host: $(remote hostname)"
echo "Cluster: $(kctl 'version -o json' | sed -n 's/.*"gitVersion": "\([^"]*\)".*/\1/p' | tail -1)"
expect namespace namespace/deployflow-test "$(kctl 'get namespace deployflow-test -o name')"
expect pod_security_enforce restricted "$(kctl "get namespace deployflow-test -o jsonpath='{.metadata.labels.pod-security\\.kubernetes\\.io/enforce}'")"
expect pod_security_version v1.36 "$(kctl "get namespace deployflow-test -o jsonpath='{.metadata.labels.pod-security\\.kubernetes\\.io/enforce-version}'")"
expect ingress_class k8s.io/ingress-nginx "$(kctl 'get ingressclass nginx -o jsonpath={.spec.controller}')"

for identity in reader validator; do
  expect "service_account_$identity" "serviceaccount/deployflow-$identity" \
    "$(kctl "get serviceaccount deployflow-$identity -n deployflow-test -o name")"
done

reader=system:serviceaccount:deployflow-test:deployflow-reader
validator=system:serviceaccount:deployflow-test:deployflow-validator
can_i() {
  kctl "auth can-i $1 -n $2 --as=$3"
}

expect reader_list_deployments yes "$(can_i 'list deployments' deployflow-test "$reader")"
expect reader_list_services yes "$(can_i 'list services' deployflow-test "$reader")"
expect reader_list_ingresses yes "$(can_i 'list ingresses' deployflow-test "$reader")"
expect reader_create_deployments no "$(can_i 'create deployments' deployflow-test "$reader")"
expect reader_get_secrets no "$(can_i 'get secrets' deployflow-test "$reader")"
expect reader_list_secrets no "$(can_i 'list secrets' deployflow-test "$reader")"
expect reader_other_namespace no "$(can_i 'list deployments' default "$reader")"

for resource in deployments services configmaps ingresses; do
  expect "validator_create_$resource" yes "$(can_i "create $resource" deployflow-test "$validator")"
  expect "validator_patch_$resource" yes "$(can_i "patch $resource" deployflow-test "$validator")"
done
expect validator_delete_deployments no "$(can_i 'delete deployments' deployflow-test "$validator")"
expect validator_get_secrets no "$(can_i 'get secrets' deployflow-test "$validator")"
expect validator_list_secrets no "$(can_i 'list secrets' deployflow-test "$validator")"
expect validator_other_namespace no "$(can_i 'patch deployments' default "$validator")"

registry_status=$(remote 'curl -sS -o /dev/null -w "%{http_code}" --max-time 10 https://ghcr.io/v2/')
if [[ $registry_status != 200 && $registry_status != 401 ]]; then
  echo "FAIL registry_network: HTTP $registry_status" >&2
  exit 1
fi
echo "PASS registry_network: GHCR responded (HTTP $registry_status)"
echo "Registry push/pull authorization requires a separate credentialed check."
