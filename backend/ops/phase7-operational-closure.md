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

Stage 2's scoped provisioning, local activation, and checker proxy correction
were separately approved and verified on 2026-10-19. Stage 2 is complete for
the current local test environment while its short-lived credentials remain
valid. Stages 3–4 still require the live fixture deployment, second release,
manual rollback, and restart/replay evidence. Cleaner access remains deferred
and disabled.

The user supplied a publisher login through Docker and changed only the checker
package to public. Do not copy the PAT into `.env`, this repository, logs, or
chat. The initial publisher-login blocker and its resolution are recorded below.

### Stage 2 prepared proposal — historical preparation

Stage 2 preparation was resumed on 2026-10-19 after the Stage 1 changes were
committed as `7f13177`. The checker OCI artifact and local image still match the
candidate digest below. The local `deployflow-kube-deployer-tunnel` image was
built successfully as
`sha256:8145c15f68563e475916f191eeacd750eb5640ac664c32faf40a685d33bcb2e6`;
no deployer container was started. Fresh SSH read-only checks confirmed the
namespace still contains only the existing reader/validator setup. The live
database remains at the new head with zero enabled policies and execution jobs.
Anonymous registry access for the checker returned HTTP 403, and Docker still
had no GHCR publisher login. The user was asked to authenticate locally; no
registry push, token request, Kubernetes mutation, or further backend restart
has been performed.

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

Publishing was initially blocked on a GHCR publisher login. For this terminal publishing
route, [GitHub documents a classic PAT with `write:packages`](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
The user can run `docker login ghcr.io -u DamiBeGud` and enter the token only at
Docker's password prompt. No token is needed in the chat or repository.
No GitHub CLI or publisher environment credential is available as an alternate
local authentication route.

### Stage 2 registry publishing — completed and public access verified

After the user confirmed a successful local Docker login on 2026-10-19, the
prepared image was pushed to
`ghcr.io/damibegud/deployflow-health-checker:phase7-acd28bb`. The push completed
successfully. Authenticated registry inspection confirmed the exact candidate
digest `sha256:1baa051801e554b17a330fb74e5ee2cae9a2bac72e14ac52a7355c5429defe6f`,
platform `linux/amd64`, and nonroot user `10001:10001`. No rebuild or candidate
manifest image change was needed. Credentials were consumed by Docker's normal
login mechanism, not printed or copied into the repository.

The initial post-push anonymous pull-authorization request returned HTTP 401.
After the user made the checker package public, anonymous authorization,
manifest download, and config-blob download passed. Both downloaded payloads'
SHA-256 hashes matched their expected digests. Platform and nonroot user were
confirmed without using the publisher credential. This verifies registry
access, not an actual cluster image pull or healthy rollout.

Fresh SSH read-only checks confirmed `homeserver1`, user `connectops`, x86_64,
namespace UID `84a720fd-dc36-4592-a2bb-12c8f54ddc7c`, restricted enforcement
pinned to `v1.36`, and ingress class `nginx`. No Deployment, Service, or Pod
exists in `deployflow-test`; only the existing reader/validator identities and
RBAC are present. The live database remains at `91d2e0a7b534`, OIDC is active,
execution and cleanup flags are false, and enabled policies and execution jobs
both number zero. Registry publishing and these checks made no Kubernetes
changes, requested no cluster token, and restarted no local service. The
five-resource cluster proposal is still unapproved and unapplied.

After publishing `ghcr.io/damibegud/deployflow-health-checker` and making the
package public, verify the exact registry manifest matches this candidate and
supports anonymous amd64 pulls. If the registry digest differs, update and
re-present the proposal before requesting Kubernetes approval.

The user subsequently approved the following exact Stage 2 actions. Their
execution and the verification blocker are recorded in the next section:

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
3. Set the new deployer token path in the ignored `.env`, force-recreate only
   the local reader/validator MCP processes, and verify authenticated reads and
   admission. Read-only Stage 3 diagnostics found that these processes remained
   in old network namespaces after their tunnel containers restarted. A simple
   credential replacement is insufficient. Reattach them to the current
   tunnels with the already-built backend image:

   ```bash
   docker compose --profile phase5 up -d --no-deps --no-build \
     --force-recreate kube-reader kube-validator
   ```

   This interrupts only those two local MCP endpoints and remains subject to
   Stage 2 approval; it has not been executed.
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

### Stage 2 approved execution — initial verification blocker

The user explicitly approved the five-resource namespace addition, three token
requests, scoped service recreations, and executor activation. The applied
manifest's SHA-256 was
`ad194f92870d46feb80d59c48b15d7513fa4d1b80fb008339aee1c958450a81f`
(later edits to its header only record approval; resource definitions are
unchanged). Immediately before applying it, SSH reconfirmed `homeserver1` and
the expected namespace UID, and the database had no enabled policies or
execution jobs. All five resources were reported **created**, not updated.

| New resource | UID |
| --- | --- |
| ServiceAccount `deployflow-deployer` | `a2a815a7-b0bd-4a29-a628-d9f573e80daf` |
| Role `deployflow-deployer` | `72f5a639-b437-4608-9300-de3b0888eeb4` |
| RoleBinding `deployflow-deployer` | `e9bd97d5-432e-4cc3-a559-46400319fc50` |
| Deployment `deployflow-health-checker` | `189ad4f2-8db1-4268-94e2-2ea8d37cf85f` |
| Service `deployflow-health-checker` | `9522f9d9-8865-480e-b7f6-8a89363b9cc6` |

The checker successfully pulled the public pinned image in approximately 77
seconds. Pod `deployflow-health-checker-7bf4b8b7d7-9klsf`, UID
`8bc8242c-f6fc-4214-9dc9-df44bb39e6fb`, became ready with zero restarts. Its
EndpointSlice is ready. The initial 40-second rollout wait expired during the
image download; subsequent reads confirmed one ready replica and the exact
expected image digest. No workload other than this approved checker was added.

Reader, validator, and deployer tokens were requested for 24 hours and stored
as `0600` files outside Git. Their actual expirations are respectively
`2026-10-20T18:00:04Z`, `2026-10-20T18:00:06Z`, and
`2026-10-20T18:00:08Z`. Previous token files were preserved privately in
`/Users/damibegud/.config/deployflow/phase7/credentials-before-POi7xZ`.
The previous ignored backend environment file was backed up privately at
`/Users/damibegud/.config/deployflow/phase7/local-config-before-PVqDXV/backend.env`.
No credential contents were printed or committed.

Locally, only `kube-reader`, `kube-validator`, `api`, `delivery-worker`, and
`dispatcher` were recreated. Only `kube-deployer-tunnel`, `kube-deployer`, and
`external-probe` were newly started, with the executor profile's isolated
external-health network. Existing PostgreSQL, Redis, Keycloak, analysis/GitHub
workers, webhook, and reader/validator tunnels were not restarted. The ignored
`.env` now sets the deployer token path, execution `true`, and cleanup `false`.
API, delivery, dispatcher, and deployer loaded execution enablement. All
application policies remain disabled, and there are still zero execution jobs.
The existing fixture deployment remains `waiting_for_executor`.

Reader MCP access passed, and the fresh six-file fixture bundle passed the
validator's real server-side dry-run for ConfigMap, Deployment, and Service.
The deployer Role's effective permissions match the proposal. Explicit checks
deny Secret access, Deployment deletion, standalone Pod creation, and ConfigMap
access in `default`. The real deployer token also received HTTP 403 when listing
Secrets. No Secret contents or unrelated namespace resources were read.

**Blocking defect: checker Service-proxy port selection.** The current backend
calls proxy name `deployflow-health-checker` without the named Service port.
Despite ready endpoints, that request returns HTTP 503 / `no endpoints
available`. A read-only request using `deployflow-health-checker:http` returns
`{"status":"ready"}` with operator access. With the deployer token, the same
port-qualified name returns HTTP 403: RBAC currently allows only the unqualified
`deployflow-health-checker` resource name. The numeric `:8080` variant is also
denied. This is not a failed image pull, expired token, or unhealthy checker.

Work stopped before further cluster changes. The proposed correction was to have the
backend use `deployflow-health-checker:http` and change only the deployer Role's
`services/proxy` resource name to that exact port-qualified value, retaining
`get` only and all other existing limits. The Role correction and additional
deployer recreation required a separate approval, which the user subsequently
provided. No checker image or Deployment/Service change was required.

### Stage 2 proxy correction — approved, applied, and verified

The user explicitly approved the port-qualified Role correction, backend fix,
and local deployer rebuild/recreation. Before mutation, target identity,
namespace UID, Role UID, disabled application policies, and an empty execution
queue were rechecked. A JSON patch tested the Role UID, resource version, and
old proxy resource name before replacing only that name with
`deployflow-health-checker:http`. All other rules and the Role UID were retained.
The original Role and namespace inventory were saved privately at
`/Users/damibegud/.config/deployflow/phase7/role-proxy-before-pphvMV`.

Real verification also exposed the generated Kubernetes client's response
coercion: its declared `str` return type converted JSON objects into Python
dictionary representations, which `json.loads` cannot parse. The backend now
requests the raw HTTP response, parses at most 4096 bytes as JSON, and closes
and releases the connection. Invalid, oversized, or mismatched responses still
fail closed. No checker or additional Kubernetes modification was needed.

- Backend fix: `app/kubernetes/executor.py`; both deployer RBAC manifests match
  the exact named proxy. Regression tests cover the actual port-qualified
  call, raw-response option, both manifests, invalid/oversized/mismatched
  responses, and connection closure.
- The final image is `deployflow-backend:phase7-checker-port-7f13177-v2`, Docker
  ID `sha256:8f23a135e34b69118d06be15cbba022f408394540eb86f31aa48080c579bf95c`.
  It is also tagged `deployflow-backend:local` for subsequent normal Compose
  usage. Existing containers were not recreated merely by updating that tag.
  The previous Stage 1 image and the intermediate first-fix image are retained.
- Full backend suite: **124 passed**, one existing third-party deprecation
  warning, against the dedicated `deployflow_test` database already at head.
  No live database migration or restore was run. Targeted Ruff and
  `git diff --check` passed. No frontend source changed.
- Only `kube-deployer` was recreated (twice within this approved correction,
  because the live raw-response issue was found after the first build). A
  before/after identity check confirms the other 16 containers and their start
  times are unchanged. The final worker runs as UID 10001, reports ready, has
  zero restarts, and has no traceback or error-level startup log.
- Using the real deployer credential as UID 10001, the named checker proxy
  returned `{"status":"ready"}`. The checker self-probe through
  `deployflow-health-checker.deployflow-test.svc.cluster.local:8080/ready`
  returned `passed`, proving in-cluster DNS/HTTP and the proxy response path.
  This is checker health, **not fixture application health**.
- The real deployer credential still receives HTTP 403 for Secrets, the old
  unqualified proxy, other Service proxies, and workloads in `default`.
  SelfSubjectAccessReviews deny Deployment deletion and Pod creation.
  The external-probe service is reachable from the executor and rejects a
  disallowed hostname with HTTP 400 without contacting that hostname.
- Comparison of all 14 observed namespace resources' UIDs and substantive
  configuration shows no change except the approved Role proxy rule. The
  checker Deployment, Service, Pod, and reader/validator identities remain
  unchanged. No other namespace resource was changed.
- API readiness still passes. OIDC remains active, execution is enabled, and
  cleanup is disabled. All application policies are disabled, execution jobs
  number zero, and the fresh candidate still has no approval or PR. Existing
  deployment `614111f1-1e9a-46ca-817e-021a79987543` remains
  `waiting_for_executor`; no fixture deployment, merge, rollback, or cleanup
  occurred.

Stage 2 is verified and closed for this local test setup on 2026-10-19.
Credentials expire on 2026-10-20 around 18:00 UTC (exact times above); obtain
new explicit renewal approval if testing resumes after expiry. This does not
claim automated production credential rotation or close Phase 7. The next
boundary is Stage 3's reviewed PR/CI/release and explicitly authorized fixture
deployment.

### Stage 2 browser verification

The frontend testing skill was used with regular Playwright because the Browser
plugin is not available. Desktop environment: `http://127.0.0.1:5174`,
1600 × 1100. The tested interaction was real OIDC login → existing candidate's
Review step → **Retry validation** → passed admission and enabled PR-approval
button. The approval button was not clicked. No approval or PR was created.

| Browser check | Result |
| --- | --- |
| Correct URL/title and meaningful page content | Passed |
| No framework error overlay | Passed |
| Console/runtime error collection | No errors |
| Retry interaction and persisted validation result | Passed |
| PR-approval button available but not invoked | Passed |
| Screenshot inspection | Passed; `/tmp/deployflow-phase7-stage2-admission-passed.png` |

The temporary script is `/tmp/deployflow-phase7-stage2-browser.mjs`; it does not
contain credential values. No frontend source changed. These checks establish
login and admission behavior, not live deployment health, rollback, or replay.

## Stage 3 — preparation completed, live deployment pending

On 2026-10-19 the user requested Stage 3 preparation. No GitHub merge,
Kubernetes mutation, container restart, policy enablement, or deployment was
performed during that preparation. Stage 2 was subsequently completed as
recorded above; Stage 3's actual deployment acceptance is still not satisfied.

### Historical release verification

- Fixture `main` is `2b6c773f3d9d23828db404165603c30533bdf892`. PR #5
  reverted the infrastructure introduced by PR #4; the current tree is
  source-only again.
- The six files at PR #4's merge commit
  `10850d7273fa9a3c13cc492f51f5b3ef8c36998a` match its saved approved bundle.
  The stored release files and hashes also verify. This is historical
  integrity evidence, not permission to deploy an obsolete revision.
- CI run `38540787752`, attempt 1, was a successful `push` on `main` for that
  exact merge commit and the expected workflow. Its unexpired artifact
  `11881743481` (`deployflow-build`) was downloaded using read-only GitHub App
  permissions. Its repository, commit, run, attempt, image repository, and
  digest match the saved verified build. Public GHCR access verifies the exact
  fixture image digest recorded in the preflight above.
- The deployment guard rejected the old release with
  `verified_revision_not_target_head`, correctly detecting the later revert.
  Saving the fresh intent subsequently invalidated the old approval with
  `intent_changed`; the old plan and historical records were preserved.

### Fresh reviewed candidate

- A real OIDC browser session reanalyzed the existing application through the
  normal frontend. Analysis `0a1cf560-c1ae-4de6-b501-2c4b35391d51` is pinned
  to current `main`, used the agent, and completed successfully.
- Assessment version 2 resolves generic deployment questions using existing
  agreed configuration: Kubernetes raw manifests, port 4587, `/healthz` for
  both probes, the configured GHCR repository, production mode, and omission
  of optional `APP_GREETING` so the source default applies. These decisions
  were saved through the assessment-review API as the authenticated operator,
  not presented as newly inferred repository facts.
- Intent version 3 uses one replica, internal exposure, the recommended
  resource bounds, `npm ci`, `npm test`, and `npm start`. No secret was entered.
- Proposed plan: `904e67d0-8e24-46ab-a0e5-2e82e16f83cc`.
  Bundle digest:
  `e4441ef2ff316aa40f90280a7f5e3f375b17f1b8cbbf5fd536d8e845dc8b43b5`.
  It contains six files: `.dockerignore`, `Dockerfile`, the GitHub workflow,
  and Kubernetes ConfigMap, Deployment, and Service manifests.
- Deterministic regeneration from the stored assessment, intent, and current
  profile produced byte-identical files and digest. Every saved file hash and
  the local manifest policy check passed. The proposed inventory is only
  ConfigMap `node-express-service-config`, Deployment `node-express-service`,
  and Service `node-express-service`, all in `deployflow-test`. No ingress,
  runtime Secret, or other namespace is included.
- The normal review UI displayed assessment version 2 and the six-file bundle.
  It correctly disabled PR approval while admission was unavailable. No browser
  runtime errors or framework overlay occurred. Screenshot:
  `/tmp/deployflow-phase7-stage3-review.png`.
- Review URL (select **Review** if the wizard opens at Source):
  `http://127.0.0.1:5174/deploy?application=89aef724-10fd-42b1-b073-c07975fdf9bb&run=0a1cf560-c1ae-4de6-b501-2c4b35391d51`.

### Initial blocking boundary and next actions

The normal server-side admission attempt returned `unavailable` /
`cluster_unavailable`. Read-only socket and network-namespace checks confirmed
connection refusal at both MCP endpoints: their tunnel containers restarted
on 2026-10-19 while the MCP processes still used network namespaces from
2026-10-14. Stage 2 subsequently renewed these tokens and recreated only the
approved processes. The normal UI's retry now passes admission, as recorded
above. This initial connectivity blocker is resolved.

With Stage 2 completed, obtain a fresh passing admission result as needed,
review/approve the new infrastructure PR, and obtain separate merge approval.
Verify the new merge's
CI artifact/image and prepared release before proposing the exact deployment
and application-policy enablement for approval. Do not reuse the reverted
release or bypass admission to create a PR.

The initial post-preparation checks confirmed zero enabled deployment policies, zero
execution jobs, no approval or PR for the new plan, and execution and cleanup
flags still disabled. Existing deployment
`614111f1-1e9a-46ca-817e-021a79987543` remains `waiting_for_executor`.
No healthy rollout or health result is claimed.

### Stage 3 observed rollout failure — numeric application image user

During a later user-initiated fixture attempt, read-only checks observed
application `601c738c-abe7-4206-8270-965576f6e88a`, deployment run
`17bd1499-c80c-47c2-9c9a-3d3b27419499`, and release
`6dcf4c37-e302-4c6d-8895-4d1c9ebc326c`. The active Pod
`node-express-service-5995ff6c6f-hkn2c` reports `CreateContainerConfigError`:
`runAsNonRoot` is true but image user `node` is nonnumeric. The image is
`ghcr.io/damibegud/deployflow-node-express-fixture@sha256:0fcbf8bf8c5cacfbf59dd383db882b325bd51879dbe3a49a54e841e2aac6f61d`.
The namespace UID and server identity still match the intended target. This is
not proof of an image-pull or ConfigMap failure. No application process starts.

The exact deployed amd64 image was pulled and inspected locally without
credentials or network access inside the container. Its configured user is
`node`; `id node` verifies UID 1000 and GID 1000. The deterministic generator
now uses numeric `USER 1000:1000` and adds a build-time check of that mapping.
It retains `runAsNonRoot: true`; it does not grant root, change Pod Security,
or patch a live Deployment. Template version advances to `node-web-v4` so older
plans cannot be reused as if they contained this fix.

Verification of the local fix:

- Full backend suite: **125 passed**, one existing third-party deprecation
  warning, in the dedicated test database. Targeted Ruff and diff checks pass.
- The actual generator output was compared byte-for-byte with the Dockerfile
  used for a local amd64 fixture build from main commit
  `f016d91012b2df8be19e2db58e49af3d17f6c916`.
- The built image declares user `1000:1000`, and the UID/GID base-image guard
  passed. A temporary local container with read-only root filesystem, all
  capabilities dropped, no privilege escalation, 128Mi memory, and no network
  except loopback started successfully. Its `/healthz` returned HTTP 200 with
  `status: ok`; the probe process confirms UID/GID 1000. Both fixture tests
  also pass under these restrictions. Only that temporary smoke-test container
  was stopped and auto-removed; existing services were not restarted.
- Prepared backend image: `deployflow-backend:phase7-numeric-user-7f13177`.
  Local test fixture image: `deployflow-fixture:phase7-numeric-user-smoke`.
  Neither was published, and the backend image was not activated in the normal
  local stack. No existing approved bundle, image digest, or release was edited.

At the final read-only check, this deployment run still reports `monitoring`,
its job is `retry_wait` / `rollout_pending`, and this application's policy is
disabled. No attempt was made to bypass the active-deployment guard, alter its
database state, clean its inventory, or hot-patch the managed Pod/Deployment.
The fix is local code only; the live failure is not yet resolved. Activating
the backend fix needs a separately approved local restart. A fresh reviewed
bundle, verified CI image, and separately authorized recovery/deployment are
required afterward. Review the existing active run before choosing its safe
recovery path; do not overwrite immutable release bytes or silently discard
its inventory. Phase 7 remains open.

### Reader/validator renewal — approved and verified on 2026-10-20

The user approved requesting new 24-hour tokens for the existing reader and
validator identities and recreating only those two local MCP containers.
Read-only inspection first confirmed both expired tokens and stale network
namespaces: the API could not reach either MCP endpoint. SSH verified
`homeserver1` and the expected `deployflow-test` namespace UID before renewal.

New reader and validator tokens were stored atomically as `0600` files outside
Git. Actual expirations are `2026-10-21T18:50:23Z` and
`2026-10-21T18:50:25Z`, respectively (approximately 20:50 Berlin time).
Previous tokens were preserved privately in
`/Users/damibegud/.config/deployflow/phase5/renewal-before-70dYoZ`.
No token value was printed. Only `kube-reader` and `kube-validator` were
recreated with the existing selected backend image, reattaching them to their
current tunnels. No backend fix was activated and no other service restarted.

The API's reader call then succeeded. The latest saved bundle, plan
`bd315c80-19eb-46f2-9730-0bc10a7b9a97`, passed the validator's real server-side
dry-run for ConfigMap `slaven-test-config`, Deployment `slaven-test`, and Service
`slaven-test`. No resources were saved by validation. The UI still needs
**Retry validation** to replace its previous persisted unavailable result.
No workload or RBAC change, deployment, merge, database migration, or cleanup
was performed. The deployer token was deliberately left unchanged and remains
expired; it needs separate renewal approval before a live deployment attempt.

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
| Approved scoped deployer and working in-cluster checker | Passed on 2026-10-19; short-lived credential expiry recorded above |
| Merged fixture reaches healthy deployed state | Unproven |
| Second release deploys and manually rolls back | Unproven |
| Worker restart/replay preserves authorization and avoids duplicate effects | Unproven |
| First-deployment cleanup on reviewed inventory | Deferred; separate destructive-test approval |
