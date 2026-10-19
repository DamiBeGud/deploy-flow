# Phase 7 operational closure

## Preflight observed on 2026-10-19

Phase 7 remains open. This record does not authorize a database migration,
container interruption, GitHub merge, Kubernetes change, deployment, or rollback.
Each requires the operator's approval of its concrete proposal.

- Repository HEAD: `acd28bb` (`Add deployment lifecycle controls, rollback, and guarded cleanup`).
  The worktree was clean at the start of this preflight.
- Live database: `deployflow`, approximately 13 MB, at `2f73b94e8c16`.
  No analysis, GitHub, or delivery job was queued or running.
- Existing deployment: `614111f1-1e9a-46ca-817e-021a79987543`, waiting for executor.
  Application: `89aef724-10fd-42b1-b073-c07975fdf9bb` (`node-express-service`).
  Release: `d9eca7c6-958c-4541-a7f9-20728a23c156`.
- Stored fixture image:
  `ghcr.io/damibegud/deployflow-node-express-fixture@sha256:e38c7b49da294e263aae75372b4ff5a16bf1ba01146671f13d5f3bfff4f23c63`.
  Stored merge commit: `10850d7273fa9a3c13cc492f51f5b3ef8c36998a`.
  Eligibility must be checked again before executing this release.
- API, analysis worker, GitHub worker, delivery worker, dispatcher, and webhook
  still use development authentication and the older backend source. The live
  API returns 404 for `/api/v1/auth/config`.
- Keycloak discovery is available at the configured local issuer. The ignored
  `.env` selects OIDC, but running backend containers have not loaded it.
- Reader and validator token files are private (`0600`) but expired on
  2026-10-15. Token renewal and MCP restarts require a later approval.
- SSH read-only checks confirmed `homeserver1`, `connectops`, and an x86_64
  server. `deployflow-test` has restricted Pod Security enforcement pinned to
  `v1.36`; the ingress class is `nginx`.
- The namespace contains the reader/validator identities and their RBAC, and
  the default `kube-root-ca.crt` ConfigMap. It has no application workload,
  deployer/cleaner identity, or health checker. Other namespaces were not queried.
- Local Docker is arm64. The checker image for this k3s server must include
  `linux/amd64`; the manifest still has an image placeholder.

## Stage 1 — approved and completed on 2026-10-19

The user approved the backup, migration, and interruption described below.
The replacement image is now selected by all six backend services.

Prepared local images (not published):

| Image | Docker image ID |
| --- | --- |
| `deployflow-backend:phase7-stage1-acd28bb` | `sha256:f897ca6ff6f827653015455fc47d963e61cbb7f9e34f3b6a5d9812164e059692` |
| `deployflow-backend:phase7-rollback-e8a2e46` | `sha256:9883649c3f68527a11698ba63447c1fa51ca1a9a101ab53408ba29b83fc974f7` |

The replacement contains HEAD plus the migration-preview fix and its regression
test. The images use the frozen dependency lock. The rebuilt fallback has the
same runtime source as the running backend, as described below.

The interruption is limited to these six local Compose services:
`api`, `worker`, `github-worker`, `delivery-worker`, `dispatcher`, and `webhook`.
Expected interruption after preparation: approximately 1–3 minutes, subject to
migration and readiness results. PostgreSQL, Redis, Keycloak, and the existing
reader/validator processes remain running.

Execution and cleanup were explicitly forced to `false` for every recreated
backend process. Application deployment policies remain disabled. The prepared
release remains `waiting_for_executor`.

### Execution evidence

- Backup directory: `/Users/damibegud/.config/deployflow/backups/phase7-YPUmG8XI`
  (`0700`). Dump: `deployflow.dump` (`0600`, 387872 bytes).
- Dump SHA-256:
  `888d2aed4fd1853dac93fd3c305a1aa78a5f47d9f6f641baac2bd080a6b9d3cc`.
  The archive index contained all 26 original table-data entries and its checksum
  verified before migration.
- Exact counts for all 26 original tables were compared before and after the
  migration and matched. The database is now at `91d2e0a7b534`.
- New deployment-policy, execution-job, and resource-journal tables are empty.
  The existing release has kind `standard`. The waiting deployment and release
  IDs listed above are unchanged.
- The prior local image tag was preserved as
  `deployflow-backend:phase7-local-before-20261019-ypumg8xi`.
- API readiness passes; `/api/v1/auth/config` reports OIDC; unauthenticated
  `/api/v1/me` returns 401. The API reaches the configured JWKS endpoint.
- All six recreated services are running with OIDC, execution disabled, cleanup
  disabled, and zero container restarts. Worker logs report readiness.
- The normal frontend is running at `http://127.0.0.1:5174/`, proxying to the
  normal API at port 8000.
- Real browser flow passed: sign in as `deployflow-local` through Keycloak,
  view applications, open the existing fixture lifecycle, refresh, reload with
  session restoration, and leave the workspace. The API accepted the signed
  administrator token. No browser runtime error or framework overlay occurred.
- The dashboard's `/api/v1/services` request still returns 503 because the
  existing cluster credentials expired. That expected limitation is deferred
  to the Stage 2 token-renewal approval; application listing and lifecycle reads
  passed independently.
- Screenshots are outside the repository:
  `/tmp/deployflow-phase7-stage1-applications.png` and
  `/tmp/deployflow-phase7-stage1-lifecycle.png` (1440 × 1000).
- The callback and lifecycle browser regression suite passed all three tests.
  The broader development-auth integration suite was not run against this
  authenticated live stack.
- No SSH command, Kubernetes write, PR merge, policy enablement, deployment, or
  rollback was executed as part of Stage 1.

### Migration contents

Apply exactly these revisions, in order:

| Revision | Change |
| --- | --- |
| `76e4c12a8b03` | Add rollout/health/prior-release columns and deployment-policy, execution-job, and resource-journal tables; extend the active-deployment index. |
| `8c27b4f1a902` | Add rollback lineage and release kind; replace release uniqueness with a standard-release partial index. Existing releases receive kind `standard`. |
| `91d2e0a7b534` | Add execution purpose with default `deploy`; include cleanup states in the active-deployment index. |

The [SQL review artifact](phase7-stage1-migration.sql) was generated with networking
disabled. The migrations perform schema changes and set defaults; they do not
delete application rows or enable deployment. Index replacement requires table
locks, which is why writers will be stopped first.

### Backup and activation sequence

The following is a proposed sequence, not an instruction to execute without
approval. Run commands from `backend/`. Abort on any failed check; do not start
new writers against a failed or uncertain migration.

1. Recheck the database revision, job states, and prepared release, and verify
   that the expected replacement and fallback images are available.
2. Stop the six writers listed above after confirming the approved interruption.
3. Create a unique private backup directory under
   `/Users/damibegud/.config/deployflow/backups/phase7-XXXXXXXX` using `mktemp -d`.
   Its parent and the new directory must be `0700`; generated files must be
   `0600`. Never reuse or overwrite an existing backup.
4. Produce a custom-format dump of only `deployflow`:

   ```bash
   docker compose exec -T postgres pg_dump -U deployflow -d deployflow \
     --format=custom --no-owner --no-acl > "$phase7_backup_dir/deployflow.dump"
   docker compose exec -T postgres pg_restore --list \
     < "$phase7_backup_dir/deployflow.dump"
   shasum -a 256 "$phase7_backup_dir/deployflow.dump"
   ```

   Record the schema revision and non-secret row counts beside the dump. Check
   the dump is nonempty, its archive index is readable, and its checksum is
   recorded before migrating. Do not print database row contents.
5. Preserve the prior `deployflow-backend:local` tag under a dated fallback tag,
   then select the prepared `deployflow-backend:phase7-stage1-acd28bb` image as
   `deployflow-backend:local`. Run the pinned target rather than an unreviewed
   future head:

   ```bash
   docker compose run --rm --no-deps migrate alembic upgrade 91d2e0a7b534
   ```

6. Verify `alembic_version`, row counts, new tables, and defaults. Recreate the
   six services with the already-built image and these explicit overrides:

   ```bash
   DF_AUTH_MODE=oidc \
   DF_DEPLOYMENT_EXECUTION_ENABLED=false \
   DF_DEPLOYMENT_CLEANUP_ENABLED=false \
   docker compose --profile phase7-delivery up -d --no-deps --no-build \
     --force-recreate api worker github-worker delivery-worker dispatcher webhook
   ```

7. Verify API readiness, OIDC discovery/configuration and browser sign-in,
   application visibility, worker health, disabled execution, and preservation
   of the waiting release. Log only sanitized diagnostics. No Kubernetes
   mutation or GitHub merge is part of Stage 1.

### Fallback and actual restore procedure

The running image's original ID is no longer available in Docker's image store.
A fallback was therefore rebuilt as
`deployflow-backend:phase7-rollback-e8a2e46` from commit `e8a2e46`. All 104 tracked
backend paths were compared with the running container: only README and
`.env.example` differed. Runtime source and the dependency lock matched. This
is a source rebuild, not a byte-identical copy of the original image.

If activation fails, stop the new writers and select that fallback image with
`DF_AUTH_MODE=development`, execution disabled, and cleanup disabled. Legacy ORM
reads were checked against the isolated database at the new schema head. Do
not automatically downgrade migrations: later rollback or cleanup history must
be preserved.

A full database restore requires its own explicit approval and would discard
database writes made after the backup. With all writers stopped and the exact
backup path verified, the procedure is:

```bash
docker compose exec -T postgres dropdb -U deployflow --force deployflow
docker compose exec -T postgres createdb -U deployflow -O deployflow deployflow
docker compose exec -T postgres pg_restore -U deployflow -d deployflow \
  --no-owner --no-acl --exit-on-error < "$phase7_backup_dir/deployflow.dump"
```

Then verify the restored revision and counts before starting the fallback
backend. PostgreSQL roles are unchanged; the dump is restored to the existing
`deployflow` owner. Do not run the migrate dependency automatically during this
fallback restart.

## Later approval gates

Stage 2 still needs an approved, published amd64 checker image/digest and the
exact additive deployer/checker proposal. Reader/validator token renewal and
restarts must also be approved. Cleaner access remains deferred and disabled.

Docker's configured credential helper has no saved `ghcr.io` publishing
credential. A new `deployflow-health-checker` package needs a publisher login
and public visibility before the credential-free cluster checker can pull it.
Do not copy the PAT into `.env`, this repository, logs, or chat.

### Stage 2 prepared proposal — not approved or applied

The [candidate manifest](k3s/phase7-stage2-proposal.yaml) contains five resources,
all in `deployflow-test`:

| Resource | Proposed effect |
| --- | --- |
| ServiceAccount `deployflow-deployer` | Separate identity for the local execution worker. |
| Role `deployflow-deployer` | Get/create/patch ConfigMaps, Services, Deployments, and Ingresses; list Pods; get only the `deployflow-health-checker` Service proxy. No Secret or delete access. |
| RoleBinding `deployflow-deployer` | Bind that Role only to the new ServiceAccount. |
| Deployment `deployflow-health-checker` | One restricted, nonroot replica, no API token; requests 50m CPU/64Mi and limits 250m CPU/256Mi. |
| Service `deployflow-health-checker` | Internal ClusterIP port 8080 for the checker. |

The checker was built for `linux/amd64` with
[Dockerfile.health-checker](k3s/Dockerfile.health-checker). It includes only the
HTTP checker module and frozen runtime dependencies. Its candidate OCI digest is
`sha256:1baa051801e554b17a330fb74e5ee2cae9a2bac72e14ac52a7355c5429defe6f`.
The OCI artifact is `/tmp/deployflow-phase7-checker-minimal-amd64.tar`; build
metadata is `/tmp/deployflow-phase7-checker-minimal-metadata.json`. This is a
locally verified digest, not a claim that GHCR already contains the image.
The image passed a local amd64 smoke test with no network, a read-only
filesystem, all capabilities dropped, UID/GID 10001, and a 256Mi memory limit:
`/ready` returned 200 and an invalid probe target returned 400. The backend API,
credential bootstrap, and `.env` are absent from this image. The five candidate
resources also passed local YAML and security checks without cluster access.

Publishing is blocked on a GHCR publisher login. For this terminal publishing
route, [GitHub documents a classic PAT with `write:packages`](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
The user can run `docker login ghcr.io -u DamiBeGud` and enter the token only at
Docker's password prompt. No token is needed in the chat or repository.
No GitHub CLI or publisher environment credential is available as an alternate
local authentication route.

After publishing `ghcr.io/damibegud/deployflow-health-checker` and making the
package public, verify the exact registry manifest matches this candidate and
supports anonymous amd64 pulls. If the registry digest differs, update and
re-present the proposal before requesting Kubernetes approval.

The later Stage 2 approval must cover these exact actions:

1. Apply only the five resources in the candidate manifest to `homeserver1`.
   The proposed command is `sudo -n k3s kubectl apply -f -` over the configured
   SSH connection, with that reviewed manifest as stdin.
2. Request 24-hour tokens using
   `sudo -n k3s kubectl -n deployflow-test create token <service-account> --duration=24h`
   for `deployflow-reader`, `deployflow-validator`, and `deployflow-deployer`.
   Check actual expirations privately; atomically replace the expired Phase 5
   files and store the new deployer token at
   `/Users/damibegud/.config/deployflow/phase7/deployer.token` (`0600`, directory
   `0700`). No tokens are printed or stored in Git.
3. Set the new deployer token path in the ignored `.env`, restart only the local
   reader/validator MCP processes, and verify authenticated reads and admission.
4. With cleanup still disabled and every application policy still disabled,
   enable the execution flag for API/delivery/dispatcher, recreate those three
   local services, and start only `kube-deployer-tunnel`, `kube-deployer`, and
   `external-probe`. No execution job exists, so a separate approved application
   policy and deployment command are still required before fixture mutation.
5. Verify the new RBAC boundaries, checker rollout and readiness, local worker
   health, empty execution queue, and preservation of the waiting release.

Reversal would first disable execution and stop the three newly started local
services. Following explicit reversal approval, remove only the new checker
Deployment/Service and new deployer RoleBinding/Role/ServiceAccount by their
exact names. Leave existing reader/validator RBAC and application resources
alone. No cleaner identity, delete permission, ingress, DNS change, or runtime
Secret is included in this proposal.

Stages 3–4 require separate approval for each PR merge, deployment-policy
enablement, first/second deployment, manual rollback, and relevant worker
restart. Preserve the exact release IDs and resource UIDs as evidence.

## Pre-activation verification

- Replacement image: 119 backend tests passed against the dedicated
  `deployflow_test` database, which was already at the migration head. The
  isolated container received a test registry profile and no GitHub or
  Kubernetes credentials. One third-party deprecation warning remains.
- Frontend production build and lint passed.
- Migration-preview lint and regression test passed. The actual SQL preview
  also succeeded in a container with networking disabled.
- Fallback ORM reads succeeded against the isolated database at the new schema.
- Compose configuration and `git diff --check` passed.
- Post-check read-only queries confirmed live `deployflow` remains at
  `2f73b94e8c16`, has no `execution_jobs` table, and retains the same waiting
  deployment. The six existing backend containers were not recreated.
- At preflight, normal-stack browser sign-in was pending; Stage 1 evidence above
  now covers it. Live deployments, rollback, and restart/replay remain unproven.

| Acceptance evidence | Status |
| --- | --- |
| Normal local stack uses OIDC and current schema | Passed on 2026-10-19 |
| Approved scoped deployer and working in-cluster checker | Pending Stage 2 |
| Merged fixture reaches healthy deployed state | Unproven |
| Second release deploys and manually rolls back | Unproven |
| Worker restart/replay preserves authorization and avoids duplicate effects | Unproven |
| First-deployment cleanup on reviewed inventory | Deferred; separate destructive-test approval |
