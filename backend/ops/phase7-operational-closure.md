# Phase 7 operational status and evidence

**Phase 7 is not complete.** A healthy baseline with durable success and correct
UI is recorded; real subsequent automatic update, manual rollback, and replay
after rollback remain unproven.

Last recorded application-release acceptance: **2026-10-21, 19:32–19:34 UTC**, followed by the
fixture PR update below on the same date. Hosted foundation checks were performed
on **2026-10-22**, as recorded below. This compact record consolidates earlier
checkpoints and the former automatic-release handoff. The documentation cleanup
verified local files/code, not current runtime, GitHub, DNS, or credential state.
All operational values below are dated observations and must be rechecked.

Use the [operational guide](README.md) for procedures and approval boundaries,
and the [backend guide](../README.md) for implementation rules. No historical
approval in this record authorizes a new operation.

## Hosted-platform installation — public endpoints verified

**Current live status — 2026-10-22, 19:33 UTC: public platform running; two applications registered; one release ready; execution/cleanup still disabled pending scoped activation approval.**

The latest read-only capability audit found `node-express-service` and
`node-express-service2`. The latter (application
`2202caf2-759b-4fd2-84c2-58f8fac226ba`) has deployment
`607c97a6-2b6f-4a31-b70d-fbf70ac7c01b`, release
`4a949ede-3412-42ed-a12b-d6b6b9f86aca`, source
`6a31e8a015dfb181101b53625be344705c483d0e`, image
`ghcr.io/damibegud/deployflow-node-express-fixture@sha256:d6b121b81541cf4b69e3388f51c2615ba3613ee2654926db4a90d1adaa867946`.
Its preflight passed, status is `waiting_for_executor`, and its ConfigMap,
Deployment and Service are absent. No application policy or execution/cleanup job
exists. API/dispatcher/delivery-worker/deployer all parse both flags as false.
The hosted cleaner identity/worker is absent. An offline activation planner now
prepares both gates plus that separate worker/identity and four relevant restarts;
it grants no authority to execute a release or cleanup. Current private database
backups passed catalog/full-archive reads without a live restore; no current-state
restore rehearsal is claimed. No live flag, policy, RBAC or workload was changed
by this audit.

Read-only GitHub App inspection found the old `wh.damibegud.cloud` receiver URL;
five latest deliveries failed with HTTP 502. The hosted inbox is empty and all 13
completed GitHub jobs were manually queued. Public receiver availability alone
does not establish automatic ingestion. Agent analysis has no configured provider
key; deterministic analysis remains selected. Main pushes run checks, with manual
publication and a fresh-install-only operator at that audit. A separate protected
platform upgrade/rollback workflow is now implemented locally, not activated;
it preserves flags/policies and refuses unsupported schema/provider/template
changes. It binds publication/main/attempt and a current exact plan, keeps private
backups/receipts server-side and fences application DB writes during image rollout.
Real disposable-PostgreSQL and synthetic failure/rollback/replay tests are recorded
in [upgrade evidence](evidence/hosting-upgrade-implementation-20261022.json).
No platform image was changed on k3s, no feature was enabled and no GitHub write
or deployment was performed while implementing it. See the
[merge-to-upgrade guide](../../deploy/k3s/automation.md#7-upgrade-the-installed-platform-after-merging-main)
and [activation guide](../../deploy/k3s/automation.md#6-enable-execution-and-cleanup-on-the-installed-platform)
and [current review evidence](evidence/hosting-capabilities-review-20261022.json).

**Earlier installation evidence (18:24 UTC and before):**
After explicit approval, the guarded SSH operator applied the exact 34-object
foundation manifest (SHA256 `3d78629ec1e0a52b2af902d0b8cb3ef130d096a1db127c89c9e5d48357c2163a`).
The three PVCs are Bound (5Gi application PostgreSQL, 2Gi identity PostgreSQL,
1Gi Redis). After separate approval, all seven runtime/bootstrap Secrets were
created. Both PostgreSQL Pods and Redis are `1/1` Ready with zero restarts.
Authenticated SQL checks show zero user relations in both fresh databases; Redis
returns `PONG`. Both private pre-migration database backups passed archive listing
and an actual isolated restore, with zero relations matching their source.
Execution and cleanup flags remain `false`. All 111 unrelated resource identities,
generations and replica specifications checked before/after are unchanged; this
does not establish a byte-for-byte audit of unrelated resource data/status.
After separate approval, `Job/deployflow-migrate-c3a71d9e4208` completed successfully
(UID `f5924cf4-2e7f-453f-98ce-e5a281131df3`, Pod exit 0, zero restarts). PostgreSQL
stores revision `c3a71d9e4208`, with 29 public-schema tables and zero applications,
pending analysis/GitHub/delivery/execution jobs, or enabled policies. The identity
database was then initialized through separately approved
`Job/deployflow-identity-setup` (UID `4a4f880f-b6ac-4482-a150-5516e2028b37`);
its guard, bootstrap and import containers all exited 0. Read-only metadata checks
verified temporary master administrator `deployflow-bootstrap-admin` and operator
`deployflow-local`, the operator's `administrator` role and `UPDATE_PASSWORD`,
public browser client, S256 PKCE and the hosted redirect URL. No password/hash fields
were queried. The subsequently approved twelve-Deployment manifest (SHA256
`d68e82a48c62d3284872d9daeb73e414dea566b3ecfb72ff7907c9dbc9d97efa`)
was applied at 17:11–17:12 UTC. All twelve Deployments reached `1/1` ready and
available, generation observed, exact pinned images and zero container restarts.
API liveness/readiness, frontend API proxy, unauthenticated API rejection (401),
provider discovery and fresh reader MCP observations passed. Validator tool
discovery passed; no admission/apply was invoked. Reader, validator and deployer
actual Pod identities were denied cross-namespace access and a GET of a deliberately
nonexistent Secret (403; no Secret values queried). Only the reader can list target
Deployments; validator/deployer list denial is consistent with their narrow Roles.
Application/job/policy counts remain zero, execution/cleanup false, and the same
111 unrelated UID/generation/replica records are unchanged. Empty/error-free sampled
worker logs are not end-to-end job-processing evidence.

The separately approved hostname manifest (SHA256
`1acfbb8afb3da381ff7e2142ecafb31af58ab9c1351dc91821f106cecaa1e567`)
was applied at 18:20–18:21 UTC: narrow HTTP-01 policy, UID/resourceVersion-fenced
old Ingress removal, then new `deployflow-system/deployflow` (UID
`39f84dbb-61ed-4f95-aa64-f13bfe8f5128`). Certificate `deployflow-tls` is Ready,
expires 2027-01-20 17:22:55 UTC, with renewal scheduled for December 21. Public
HTTPS checks validate the real HTML UI, JSON OIDC configuration/discovery/JWKS,
401 for unauthenticated application reads, 404 for identity admin/master paths,
and 405 for GET of the POST-only webhook receiver. GET 405 does not prove signed
webhook ingestion. The initial HTTPS check during certificate issuance failed;
after waiting for Ready, validation passed without disabling certificate checks.
Desktop Chromium (1440×900) rendered the UI, with no overlay/console/request errors,
and Sign in opened the actual hosted username/password form using S256 PKCE.
No credentials were submitted, account changed, or browser storage captured.
The API Pod can fetch public JWKS. All twelve Deployments remain 1/1 ready and
available with zero restarts; API/deployer parsed execution/cleanup flags are false,
and application/job/policy counts remain zero. Of 111 earlier resource records,
110 are unchanged and only the approved old Ingress is removed; the old mockup
Deployment/Service/namespace remain. Full first login/password change,
authenticated API/SSE, signed webhook and application-release acceptance are unproven.
See [foundation evidence](evidence/hosting-foundation-20261022.json) and
[data-service/backup evidence](evidence/hosting-data-services-20261022.json) and
[migration evidence](evidence/hosting-migration-20261022.json) and
[identity evidence](evidence/hosting-identity-20261022.json) and
[workload evidence](evidence/hosting-workloads-20261022.json) and
[public hosting evidence](evidence/hosting-public-20261022.json).

A separate credential audit checked remote main
`54f04988e21f03bda6800bb6b7c74604e5a428e4`, all 22 reachable commits/440 unique
blobs, and the current tracked/nonignored working files. No real deployment
credential was found. The seven history scanner findings were four file SHA256
digests and three test idempotency keys; the working tree added a pinned image
digest finding. Exact comparisons against 21 private credential representations
also found no history matches. Development/example credentials remain intentionally
committed; generated hosted passwords, PEMs and tokens are private/ignored.
See [audit evidence and coverage limits](evidence/credential-audit-20261022.json).

**Earlier installation tooling preparation:**
Two GitHub workflows now provide push/PR checks, separately approved manual GHCR
publication and manual single-stage installation. The offline release bundle binds
source SHA, image digests, schema and exact manifests. The SSH operator checks the
server/namespace identity, resource ownership, fresh-install state and exact stage
approval digest. Private credentials remain outside Git; a separate helper prepares
the namespace GHCR pull Secret. PostgreSQL/Redis images are pinned. Hostname cutover
and restoration use saved route data and UID/resourceVersion-fenced deletion.
See the [installation guide](../../deploy/k3s/automation.md). These additions do not
authorize Git writes, SSH key installation, publication, migrations or cluster
changes. The five previously staged files were preserved in the index.

Before foundation activation, the new operator's read-only SSH inspection reconfirmed `homeserver1`, target UID
`84a720fd-dc36-4592-a2bb-12c8f54ddc7c`, no `deployflow-system`, and mockup Ingress UID
`8adb1de3-8d69-4c54-9e15-51b34d72fe67`. No GitHub-hosted-runner connection or cluster
mutation had been tested at that point. Live hosting and Phase 7 acceptance remain incomplete.
Latest registry-setup preparation: **45 offline hosting tests** and Ruff pass,
including synthetic-only server-side credential reuse and the option to provision
seven runtime Secrets without a local registry credential. Read-only metadata
inspection found `connect-app/ghcr-pull-secret`; the agent has not inspected its
credential values. The user's subsequent server-side run confirmed `read:packages`
only and access to all three published images; expiry was not reported.
The [server-side reuse command](../../deploy/k3s/automation.md#reuse-an-existing-server-side-ghcr-credential)
is prepared for the user to run. The user's first apply created Namespace
`deployflow-system` (UID `d611539e-df7d-4826-b76d-2bd0c4d97225`, 2026-10-22 16:00:44 UTC),
then stopped because the helper parsed kubectl's plain-text create acknowledgement
as JSON. Read-only inspection confirmed the expected ownership/PSA labels, no
workloads and no `deployflow-ghcr` Secret. The parser is fixed locally, with
regression coverage for a fresh apply and retry after namespace creation; the
user's retry successfully created `deployflow-system/deployflow-ghcr`, retaining
the source Connect Secret. The agent's subsequent read-only inspection confirmed
the namespace/Secret exist, no platform workloads exist, the three target Roles
and local-path storage are present, and approximately 20 GiB of root disk is free.
At that point the agent had made no cluster changes. The reviewable 34-object foundation manifest
(SHA256 `3d78629ec1e0a52b2af902d0b8cb3ef130d096a1db127c89c9e5d48357c2163a`)
was subsequently approved/applied as recorded above. Seven exact private runtime
Secret objects have been prepared outside Git (0700 directory/0600 files), with
SHA256 `b99426d4052a0198b04754731d3a9df9af21d88f87c5cadb2c1771403c6428d6`.
They were explicitly approved and created, starting the new data services as
recorded above. The subsequently approved application migration was only
`Job/deployflow-migrate-c3a71d9e4208`, manifest SHA256
`50fcb800f7cb7c21fcb1efaeff72ac99d8fb0ed60138974fa47e566b6162b840`,
using the published immutable backend image to migrate the empty application DB
from base to `c3a71d9e4208`. It completed as recorded above. Offline SQL and the
private tested backup/restore procedure remain outside Git; no live restore was
executed. The subsequent identity approval covered only `Job/deployflow-identity-setup`,
manifest SHA256 `d93f86c683cf11fdb93edbc0906c56be9a4efb82de3b97e69ffabbce28fb4179`:
read-only empty-identity-DB guard, one-time temporary master administrator creation
and application-realm/operator import. No automatic retry, account reset, identity
server Deployment or public route change was included. Identity setup completed
as recorded above. The next separately approved stage was exactly twelve platform
Deployments, manifest SHA256
`d68e82a48c62d3284872d9daeb73e414dea566b3ecfb72ff7907c9dbc9d97efa`.
Three separate Kubernetes identities use projected rotating tokens with the
existing narrow Roles. Execution/cleanup stay disabled; no public route switch,
GitHub setting change or managed-application deployment is included.

The subsequently approved hostname cutover included a narrow HTTP-01
solver NetworkPolicy. Read-only preparation verified the production ClusterIssuer
Ready, nginx (not host-networked), correct public DNS, and no platform Ingress or
Certificate. Default-deny would otherwise prevent nginx reaching temporary solver
Pods; the local renderer/operator now prepare the selected TCP 8089 rule before
removing the old route. The policy and route/TLS activation then completed as
recorded above. GitHub App settings, permanent private identity administration,
application execution and cleanup have not been changed by that approval.
Current offline hosting checks: **48 tests** and Ruff passed; these validate local
operator behavior; live certificate issuance and login-form navigation have
separate evidence above. Full authenticated login remains unproven.

Previously recorded checks: **34 hosting tests**, **176 isolated backend tests** (19.76s,
one dependency warning), Ruff, actionlint and relative links/whitespace passed.
The exact fresh-state/pending-job SQL also passed against disposable pinned
PostgreSQL with the real backend migration. Full commands and limits are in
[automation evidence](evidence/hosting-automation-20261022.json); mocked checks do
not establish a successful live stage.

The user selected `deployflow.damibegud.cloud`, fresh application data, the
existing Keycloak configuration, and the existing GHCR publisher namespace.
[Local hosting preparation](../../deploy/k3s/README.md) now includes a phased
renderer, private Secret preparation, frontend/Keycloak packaging, and explicit
in-cluster projected-identity support. **Current identity decision: create new
accounts after the Docker reset**, superseding the earlier preservation request.
A separate one-time setup Job creates a temporary master administrator and a new
application operator with required first-login password change. Generated passwords
stay in private files/Secrets; the long-running Keycloak Pod receives neither
bootstrap passwords nor automatic import flags. The optional full-export recovery
helper remains available but is not part of this fresh installation. No historical
account export/restore occurred; fresh live account creation is recorded above.

New read-only SSH checks reconfirmed `homeserver1`, the target namespace UID,
nginx, local-path storage, and ready Let's Encrypt issuers. DNS already points
to the server, but the mockup Ingress still owns the selected host/root. No
Ingress, certificate, Secret, workload, binding, token, or database was changed.

Fifteen offline hosting/identity checks and eight database-free credential-loader tests pass;
changed-file Ruff and frontend build/lint pass. Compiled assets served through
isolated native nginx passed desktop Chromium connect/form/reload, API forwarding,
unbuffered SSE, and private-path 404 checks with a fake API and no console errors.
Temporary screenshot: `/tmp/deployflow-hosted-prep-desktop.png` (not live evidence).
Docker was subsequently factory-reset by the user. Inspection found the engine
running with no containers, volumes, or images. Host-side application database
backups and the Keycloak bootstrap password file survived, but no full identity
export/volume backup was found in the known private backup directory. The user
accepted account recreation, removing identity recovery as a prerequisite. Original
users/IDs/MFA are not being preserved. The normal Compose stack has not been
recreated. Repeated local image builds passed for backend, frontend, and Keycloak
on `linux/amd64`; the isolated full backend suite passed **176 tests in 25.15s**
(one dependency deprecation warning). Fifteen current offline hosting checks and
changed-file Ruff pass. Disposable nonroot/read-only container checks passed for
PostgreSQL, authenticated Redis, the fresh pinned application migration, and
frontend SPA/API/SSE/private-path handling with a fake API. Two synthetic Keycloak
realms/users survived full CLI export/adaptation/import with matching account IDs,
credentials, and roles; administrator login/authenticated lookup passed. The test
found and fixed an optimized-image build-time health-path mismatch. Temporary
preparation harness/log: `/tmp/deployflow-hosted-container-check.py` and
`/tmp/deployflow-hosted-container-check.log` (not live acceptance evidence).
The backend image's client source hash matches the working tree. Interrupted or
failed harness runs are superseded by the successful final run; they were not
counted as passing results. Disposable test resources were removed.
The rendered import Job's exact command and three-variable environment also
passed against disposable PostgreSQL with synthetic exports. Local image IDs,
source hashes, and test results are recorded in
[hosting preparation evidence](evidence/hosting-preparation-20261021.json).

The subsequent new-account setup check (2026-10-22 local date) passed the exact
read-only empty-database guard, offline temporary-admin bootstrap, and application
realm import against disposable PostgreSQL. Replay was blocked by the guard before
either Keycloak mutation; the guard is necessary because duplicate bootstrap can
log an error while exiting zero. The operator explicitly receives `UPDATE_PASSWORD`
in addition to its temporary credential. Desktop Chromium (1440×1000) passed first
login/password change and PKCE token exchange with the required claims and no
console errors. The HTTPS origin was simulated through a local test proxy; real
Ingress/TLS/hosted login remain unproven. Temporary harness/log:
`/tmp/deployflow-fresh-identity-check.{py,log}`. Fifteen current hosting checks and
changed-file Ruff pass; application/backend code is unchanged since the 176-test
run. All disposable containers/networks were removed. Seven proposed Secret files
and initial passwords were prepared outside Git with restrictive permissions;
real credentials were not supplied to tests. The existing ignored App key was
restricted to 0600 without changing bytes and copied privately outside the checkout.
No real account has been created.

Post-reset read-only SSH reconfirmed `homeserver1`, the same target namespace UID,
and one ready/available replica each for `node-express-service3213214` and the
health checker. The platform namespace is still absent. The reset affected local
Docker state, not those observed cluster workloads. Real fresh-account creation,
hosted login, and cluster activation remain unproven.
After the user pushed `8d79ed5`, explicit approval covered three frozen platform
images. The backend published successfully with authenticated remote index digest
`47b2787a01f93371114ccbd5426ea1b4628e8b50446bb6735fe23a55ebbceb24`;
anonymous access returned 401. Frontend and Keycloak publication were denied after
layer uploads. Inspection found inherited upstream repository labels. Two local
Dockerfile metadata fixes now point at this repository and clear foreign revision
claims. Rebuilt images have identical RootFS layer chains and runtime configuration
to the tested originals. Following a second explicit approval, corrected frontend
index `627f7561027ec24b475572fc54df9083d5b70b4488c556372cb07842e7bad5da`
and Keycloak index `91fbecdd9f5ac089492f0905463a884d291b165b347d2da243c66b5add96de75`
published successfully. Authenticated registry reads confirmed all three indexes
and their `linux/amd64` manifests. Package permissions/visibility were not changed;
anonymous reads returned 401 for all three. Evidence contains exact tags, full
index/platform digests, old/new IDs, and filesystem/configuration comparisons.
At that earlier publication checkpoint, private pull access had not yet been
provisioned, no GitHub Actions workflow had been pushed, and no cluster activation
had occurred. Later installation and pushed workflows are recorded above. The
earlier baseline acceptance below does not
establish acceptance of this fresh installation.

## Acceptance matrix

| Criterion | Recorded status | Evidence / remaining requirement |
| --- | --- | --- |
| Reviewed infrastructure PR → merge → verified immutable baseline image | Passed | Fixture PR #12, exact build/release below |
| Retry correction/schema actually activated | Passed | Schema `c3a71d9e4208`; running dispatcher hash verified |
| Authorized baseline → readiness → internal health → durable success | Passed | Normal API reconciliation retry; persisted evidence |
| Real desktop lifecycle, fresh 1/1 observation, reload | Passed | Checked-in baseline screenshot; later access-recovery browser check recorded |
| Unrelated resources unchanged by baseline recovery | Passed | Equal before/after identity/spec hashes for 10 resources |
| Subsequent-source verification, compatibility, ordering, policy | Passed in isolated tests | Does not establish live automatic deployment |
| Signed GitHub notification ingestion | Failed in latest App delivery audit | Hosted receiver exists, but App uses old URL (502); zero hosted inbox deliveries; URL/signing alignment needs approval |
| Later source → new verified release → automatic healthy update | Unproven | Published marker-2 image and marker-3 PR are not deployed-version evidence |
| Manual rollback → new release → previous healthy version | Unproven live | No demonstrated upgrade/rollback lineage |
| Relevant worker restart/replay after rollback | Unproven live | Needs separate scoped approval and observed recovery |
| Ownership, namespace, credential separation | Passed for recorded checks | Revalidate immediately before any mutation |
| First-deployment cleanup | Unproven / disabled | Separate destructive test only if requested |
| External application Ingress/DNS/TLS | Not applicable | Selected fixture is internal-only; webhook HTTPS is separately required |

## Recorded runtime and access

- Local API: `http://127.0.0.1:8000`; frontend: `http://127.0.0.1:5174`.
  OIDC uses local Keycloak at `http://localhost:8180`.
- Target host: `homeserver1`; historical SSH target
  `connectops@91.89.185.146`. Namespace `deployflow-test`, UID
  `84a720fd-dc36-4592-a2bb-12c8f54ddc7c`; restricted Pod Security pinned to
  `v1.36`; IngressClass `nginx`. These identify the acceptance environment,
  not portable setup defaults or permission to mutate it.
- Live schema was `c3a71d9e4208` after approved migration from
  `91d2e0a7b534`. Activation preserved 104 applications, 5 deployment runs,
  and 5 releases.
- All ten backend/helper services had been aligned to
  `deployflow-backend:phase7-source-lifecycle-20261021`, image ID
  `sha256:59fb4d9a76995c7d8b9cba6f185f1081ce5dfffcdef9bcb07d4bc12e16b72232`.
  Running `app/dispatcher.py` SHA-256:
  `57b86518771b810a9fcfe85ded5ca985f81a8e8f2a1c2c0a312172ed3fd38b8c`.
  Source edits after this snapshot do not imply a running-image update.
- Execution was true in `api dispatcher delivery-worker kube-deployer`;
  analysis/GitHub/webhook processes retained execution false. Cleanup remained
  false and the separate cleaner unprovisioned locally.
- Only application `node-express-service3213214` had an enabled policy, bounded
  to source `5ee02c99f312269f95854b3c26d0301d6088df00`.
  Other application policies were disabled. This does not authorize marker-2,
  marker-3, or future commits.
- Latest job inventory: three blocked execution jobs and one completed;
  no queued/running GitHub, delivery, or execution work was reported. Reaudit
  all queues, leases, policies, and source bounds before startup or activation.

### Access recovery

The earlier 503 incident combined expired credentials with dependent processes
attached to old tunnel network namespaces. It was resolved with scoped approval:
three token requests and recreation of exactly `kube-reader-tunnel`,
`kube-validator-tunnel`, `kube-deployer-tunnel`, `kube-reader`, `kube-validator`,
and `kube-deployer`. No RBAC, ServiceAccount, namespace, application/checker
resource, or deployment-policy change was made in this recovery.

Requested and granted duration was **720h (30 days)**. Recorded actual expiries:

| ServiceAccount | Expiry UTC | Private host file |
| --- | --- | --- |
| `deployflow-reader` | 2026-11-20 19:32:08 | `~/.config/deployflow/phase5/reader.token` |
| `deployflow-validator` | 2026-11-20 19:32:09 | `~/.config/deployflow/phase5/validator.token` |
| `deployflow-deployer` | 2026-11-20 19:32:10 | `~/.config/deployflow/phase7/deployer.token` |

Files were 0600 in 0700 directories. No API-server duration setting changed.
Private renewal metadata/backups were recorded under
`~/.config/deployflow/backups/k3s-access-renewal-koheufad`; they were not
reopened during documentation cleanup. No automatic renewal exists.

The three tunnels ran `deployflow-k3s-tunnel:reconnect-20261021`, image ID
`sha256:8e159dabef37b8191ed01bb0da7c6eae9a54a77e0fe7cb32929ad9e5da21879b`.
All role processes authenticated as UID 10001 and matched their current tunnel
namespaces; tunnel restart counters were zero after activation. Services API
returned 200, with observation at `2026-10-21T19:33:25.992988Z`. Real desktop
Services/lifecycle refresh and reload showed 1 ready/1 available and historical
durable success, without runtime/console errors. Ten namespace identities/spec
hashes remained unchanged. These later browser/inventory artifacts were in
ephemeral `/tmp` files, not committed; their continued availability is unverified.

## Healthy baseline and committed evidence

Fixture repository:
[deployflow-node-express-fixture](https://github.com/DamiBeGud/deployflow-node-express-fixture).
Use that checkout name, not the older `node-express-service-fixture`.

| Identity | Recorded value |
| --- | --- |
| Application | `node-express-service3213214` |
| Application ID | `ad7c395b-836d-4874-a5a6-e35ba836f366` |
| Infrastructure PR | [#12](https://github.com/DamiBeGud/deployflow-node-express-fixture/pull/12); DB `95ecb4d7-1eb4-4953-a77a-5c627b6f22aa` |
| Approved baseline / deployed source SHA | `5ee02c99f312269f95854b3c26d0301d6088df00` |
| Verified build | `09cfbad5-6647-4176-b539-640200fe8797` |
| Workflow run / attempt | `39498021958` / 1 |
| Deployment | `1a326ed7-c5e4-43ef-8443-fb4c1e36c7b0` |
| Execution job | `5f8a5f05-9158-49a2-a94d-bdc24d7b77af` |
| Release | `2feab925-c237-4651-aebe-d495e580195b` |
| Release digest | `80be449f819212832ceee59ee386a09c7035f6b68f76c841b22ef88f83c7c08b` |
| Image | `ghcr.io/damibegud/deployflow-node-express-fixture@sha256:f1b3a237d1baa8e1391ccfa20f3e41859c3132c0af3dd57a0fa2797a47416fd9` |
| Rollback lineage | Standard release; `previous_release_id=null`; no new rollback release demonstrated |

Resource ownership matched this application:

| Kind | Name | UID |
| --- | --- | --- |
| Deployment | `node-express-service3213214` | `a4e5a93a-75a9-4339-91f4-5da8a4693f79` |
| Service | `node-express-service3213214` | `d4a13c5f-adf7-4f57-8922-4baf83649f1e` |
| ConfigMap | `node-express-service3213214-config` | `64fae1ec-088e-41a3-b6f8-9135e71271a8` |

On October 21, the approved normal `retry-execution` path completed an already
fully applied journal without reapplying resources. Rollout at
`14:51:08.136978Z` reported desired/updated/ready/available **1/1/1/1** and
generation/observedGeneration 1. Internal `/healthz` passed at `14:51:08.504867Z`;
deployment became `succeeded` and job `completed`. No direct DB status/deadline
change manufactured success. API and real browser reload agreed.

Checked-in evidence, preserved unchanged:

- [Persisted/API baseline and current observation](evidence/phase7-20261021/baseline.json)
- [Authenticated lifecycle screenshot](evidence/phase7-20261021/baseline-succeeded.png)
- [Namespace before recovery](evidence/phase7-20261021/namespace-before-recovery.json)
- [Namespace after recovery](evidence/phase7-20261021/namespace-after-recovery.json)

The before/after files matched across ten Deployment, Service, ConfigMap, and Pod
identities/spec hashes, including unrelated resources. No secret or ConfigMap
values are retained. Current read-only observation reports `health=not_checked`;
the health success above belongs to the persisted execution result.

### Migration backup and fallback

After stopping approved writers, the fresh custom-format dump was fully restored
in isolation and original revision/counts checked before activation.
Recorded private location:
`~/.config/deployflow/backups/phase7-activation-otc0ohcf/deployflow.dump`,
476168 bytes, SHA-256
`a6c71f1b291e2a5f4ba4d2e294422fe0878ecf0353a59f6347882ad0f46fc993`.
The directory also held private environment snapshot, archive index, restore log,
and verification metadata. This is recorded evidence, not a new backup check.

Fallback was `deployflow-backend:phase7-recovery-7f3d74b`, image ID
`sha256:a8a3364e93961ce7936767608e38fc3c6a89654a102bbf7bbdc446933e4163bc`,
with execution disabled and additive schema retained. Confirm image availability
and compatibility before proposing reuse. Restore/downgrade is not automatic;
use the [recovery procedure](README.md#activation-and-database-recovery).

## Fixture update and CD blockers

The existing uncommitted documentation updates were integrated here. Their
recorded fixture work changed only `src/app.js` and `test/app.test.js`:
`/` and `/api/info` return `release: "phase7-release-3"` and
`cdTest: "local-k3s-cd-1"`. Health, dependencies, startup, Dockerfile, workflow,
and Kubernetes files were unchanged. Three fixture tests/diff checks passed,
and independent approved-baseline compatibility returned compatible.
The local fixture edits remained unstaged/uncommitted; the remote branch advanced
without force push. No release was prepared/deployed by that PR task.

| Fixture state | Recorded value |
| --- | --- |
| PR | [#14](https://github.com/DamiBeGud/deployflow-node-express-fixture/pull/14), open/ready for review, **not merged** |
| Branch / head | `phase7/automatic-release-3` / `806f3ceb04502949cfe7ca7df03f3c43764097a9` |
| Main | `43d762ecf74d86d331bcca4bb34f8395e30dc648` |
| Latest PR CI | [39663527428](https://github.com/DamiBeGud/deployflow-node-express-fixture/actions/runs/39663527428), attempt 1, passed for `pull_request` at that head |
| Earlier PR head / CI | `fc6e750d64c32e62de2eb3887eb8b2af39149a44` / `39621047976`, superseded by the row above |
| GitHub App / installation / repository ID | `4957845` / `162024583` / `1372078565` |

PR CI tests/builds; it does not publish the deployable push/main image.
The already-published marker-2 main build used run `39499725243`, attempt 1,
artifact `12298414767`, image digest
`sha256:98b2579ed0f2409280a79e57e38d425407efbb97b3a05b95825547f8e4690d3e`
in the same fixture image repository. It was **not deployed**.

The App URL was `https://wh.damibegud.cloud/webhooks/github` with JSON and TLS
verification. Last audited completed delivery `3849431776327909376` returned
502 on October 20; later DNS returned NXDOMAIN. The local receiver lacked
`DF_GITHUB_WEBHOOK_SECRET`, returned 503, and had no inbox rows.
The App subscribed to `pull_request`, `push`, and `workflow_run`; the implemented
receiver ignores direct push events and uses completed workflow notifications.
No endpoint publication, App-setting change, signing-secret change, policy
change, or k3s mutation accompanied the fixture PR update.

## Remaining acceptance sequence

1. Reaudit runtime/credentials/jobs and the healthy baseline; do not repeat the
   already completed migration or baseline recovery without a new need.
2. Obtain a concrete endpoint/configuration decision: restore the stable
   DNS/proxy/tunnel or select an approved hosted tunnel, with private matching
   HMAC secret and only the webhook route exposed. Verify real signed ingestion.
3. Review PR #14's actual state and obtain separate Git-write/merge approval.
   Authorize only the exact selected source and application for a bounded test,
   with explicit disposition of marker-2 and all pending releases.
4. Prove PR merge → real notification → exact successful push CI/artifact/digest
   → immutable release → preflight/policy → automatic execution → a different
   running image and visible marker → ready/available replicas → internal health
   → durable success → correct UI after reload. Record IDs and unrelated-resource
   comparisons. Hidden manual reconciliation cannot establish automatic operation.
5. Review/approve rollback to the baseline bytes/image. Create a new rollback
   release through DeployFlow, verify previous version/health, and preserve lineage.
6. Obtain approval to restart only relevant workers. Observe durable replay with
   no unauthorized duplicate effects or old source undoing the intentional rollback.

Removing the source bound for ongoing future automation is a separate policy
decision. Cleanup stays disabled/unproven unless separately requested.
Internal fixture acceptance does not require new Ingress/TLS infrastructure.

## Recorded tests

These results predate this documentation cleanup; no tests or live operations were
rerun for prose changes. Commands are recorded with their original working scope.

| Working directory / scope | Command or check | Recorded result |
| --- | --- | --- |
| `backend/` | `DF_TEST_IMAGE=deployflow-backend:phase7-source-lifecycle-20261021 sh scripts/test_phase7_isolated.sh` | Latest full suite: **168 passed**, 38.02s; earlier 166 predates two tunnel tests |
| `backend/`, isolated image with source mounted | `ruff check app tests scripts migrations` | Existing `I001` in untouched `tests/test_local_oidc.py` |
| Same isolated scope | Same check with `--exclude tests/test_local_oidc.py` | Passed |
| `frontend/` | `npm run build`; `npm run lint` | Passed |
| `frontend/` | `npm run test:e2e -- tests/phase7-lifecycle.spec.ts tests/oidc-callback.spec.ts --workers=2` | **9 passed**, mocked APIs |
| Fixture checkout | `npm test` | **3 passed** |
| Isolated Docker harness | Two SSH disconnects, same container PID/netns, graceful shutdown | Passed; no live credentials/network |
| Approved local stack | Real OIDC desktop Services/lifecycle refresh and reload | Passed; baseline only |
| Backend / project / fixture | Compose validation and diff checks | Passed at their recorded checkpoints |

The backend tests used dedicated disposable PostgreSQL/network resources, including
actual dispatcher/worker claims, retries/leases, migration preservation,
provenance rejection, concurrent/reordered events, policy bounds, supersession,
and replay tests. Mocked tests do not prove the outstanding live criteria.
The lint issue is an out-of-scope code-format item, not repaired by this cleanup.

## Historical context retained for recovery

- **October 7:** Initial namespace/reader/validator inspection and provisioning;
  [P0-01 record](k3s/P0-01-readiness.md). Its then-pending integration work later
  progressed; it is not today's setup checklist.
- **October 19:** OIDC/execution-schema activation, separate deployer/checker
  provisioning, and checker proxy/RBAC correction were approved and verified.
  Published checker image:
  `ghcr.io/damibegud/deployflow-health-checker@sha256:1baa051801e554b17a330fb74e5ee2cae9a2bac72e14ac52a7355c5429defe6f`
  for `linux/amd64`. Named proxy `deployflow-health-checker:http` is required.
- **October 20:** An older `node-express-service` attempt failed with
  `CreateContainerConfigError` because its image declared nonnumeric `USER node`.
  Template `node-web-v4` fixes the generator; old bundles/releases stay immutable.
  Partial older inventory must not be silently adopted or removed.
- **Older app31312:** Application `36ae77de-39eb-45bb-9a33-07971d08a955`,
  deployment `ce60fd15-9fe1-4e69-98a1-81af27105f45`, release
  `f8b75cb9-8aed-430f-b87c-777e6bf1e032`, release digest
  `49dc66efca873bc2ac4729857d2ad312ce98a67ed7b9b1ab05f51828f1ccaa22`,
  image digest `sha256:c5fb47152a8c574ae5432bddf264b49b5c334ef9c7af3db82a6de82236ff55c4`
  had a ready Pod/internal health but stalled durable monitoring. Its Deployment,
  Service, and ConfigMap were later observed absent; its policy was disabled.
  It is not app3213214's rollback baseline.
- **October 21:** The shared retry publication bug was corrected and activated;
  normal guarded replay first blocked expired jobs, then the separately approved
  app3213214 API retry finished observation without reapply. Access renewal and
  tunnel supervision subsequently resolved the independent 503 incident.

Repeated superseded proposals, temporary helper commands, and old test counts
were consolidated rather than preserved as competing instructions. Private
backups and ephemeral screenshots were not copied into Git; their historical
existence is not a claim of current availability.
