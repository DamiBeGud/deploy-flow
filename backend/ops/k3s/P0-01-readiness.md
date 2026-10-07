# P0-01 k3s readiness record

Checked: 2026-10-07. Target: the existing shared k3s host (`homeserver1`),
not a new or dedicated cluster. This record contains no kubeconfig, token, or
private key.

| Check | Result |
| --- | --- |
| k3s API | v1.36.3+k3s1; reachable through a local-only SSH tunnel to the server's loopback API. CA-verified TLS succeeded; unauthenticated requests returned 401 as expected. |
| Namespace | `deployflow-test` exists with restricted Pod Security admission labels pinned to v1.36. |
| Ingress | `nginx` exists and uses controller `k8s.io/ingress-nginx`. The local profile now names `nginx`. |
| Reader | `deployflow-test/deployflow-reader` can read permitted workloads, Services, and Ingresses. It cannot create Deployments, read/list Secrets, or list Deployments in `default`. |
| Validator | `deployflow-test/deployflow-validator` can get/create/patch the supported dry-run kinds in its namespace. It cannot delete Deployments, read/list Secrets, or patch Deployments in `default`. |
| Registry network | GHCR `/v2/` responded with an HTTP 401 authentication challenge from the host. This proves network reachability, not image pull/push authorization. |

The seven administrator-owned resources in `p0-01-access.yaml` were applied to
the shared cluster: one namespace, two ServiceAccounts, two Roles, and two
RoleBindings. No application workload was deployed. The manifest is a record of
those resources, **not** part of an application-generated bundle.

P0-01 remains **partially closed**. A distinct deployment identity is still
needed for Phase 7; the validator is not a deployer. Scoped client credentials
and a kubeconfig outside Git have not yet been provisioned/tested for the
backend. GHCR publish and cluster pull authorization have not been demonstrated.
The tunnel proves transport only and does not grant API access. No further
server changes should be made without the operator's explicit confirmation.

Run `DF_K3S_SSH_TARGET=user@host bash backend/scripts/check_k3s_readiness.sh`
from the repository root for repeatable read-only checks. An optional
`DF_K3S_SSH_KEY` can select an SSH key outside the repository. This check does
not verify registry authorization or scoped Kubernetes credentials.

The validator's Kubernetes RBAC grants write-equivalent verbs because the API
authorizes dry-run as it would a real write. The future validation service must
force `dryRun=All` and refuse arbitrary manifests; never expose its credential
to the analysis model or a general-purpose client. Deployment and Secret access
must remain separate.
