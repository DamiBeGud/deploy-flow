# Historical initial k3s readiness — P0-01

Checked **2026-10-07** on the existing shared host `homeserver1`.
This is an initial-access record, not current runtime status or a setup checklist.
Later credential provisioning, deployment integration, and acceptance results are
in the [operational status record](../phase7-operational-closure.md).
Use the [operational guide](../README.md) for current procedures.

| Initial check | Recorded result |
| --- | --- |
| k3s API | v1.36.3+k3s1, reached over a local-only SSH tunnel; CA-verified TLS, unauthenticated 401 |
| Namespace | `deployflow-test` with restricted Pod Security enforcement pinned to v1.36 |
| Ingress | `nginx`, controller `k8s.io/ingress-nginx` |
| Reader | Permitted namespace reads; no Deployment create, Secret read/list, or cross-namespace Deployment list |
| Validator | Supported namespace dry-run kinds authorized for get/create/patch; no Deployment delete, Secret access, or cross-namespace patch |
| Registry network | GHCR `/v2/` returned an authentication challenge; network reachability only |

The seven administrator-owned resources in [p0-01-access.yaml](p0-01-access.yaml)
were applied: one namespace, two ServiceAccounts, two Roles, and two RoleBindings.
No application workload was deployed by that provisioning. The manifest is not
an application-generated bundle and is not authorization to reapply or delete it.

At this initial checkpoint, scoped backend credentials and later deployment/
registry integration were pending. Those statements are historical; do not use
them to undo or repeat later approved setup. The tunnel grants transport only.
Validator RBAC authorizes write-equivalent verbs for server-side dry-run; its
service must force `dryRun=All` and accept only stored bundles.
Reader, validator, deployer, and cleaner credentials remain separate.
