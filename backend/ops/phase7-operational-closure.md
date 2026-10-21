# Phase 7 operational closure

## Latest live checkpoint — 2026-10-21, 14:55 UTC

**Backend activation and first-release operational acceptance passed. Phase 7
remains open for automatic update, rollback and replay evidence.** This section
supersedes earlier same-day preparation snapshots below.

The user explicitly approved continuation after the six-step activation and
acceptance summary. Executed the concrete local activation proposal and scoped
baseline recovery. No k3s resources were applied, patched, deleted, restarted,
scaled or adopted; no Kubernetes token was requested. Cleanup remains disabled.

### Local runtime refresh — 2026-10-21, 17:23 UTC

At the user's request to run the latest local backend/frontend, compared all
**86** runtime source/configuration files against the running API: no differences.
API readiness passed. Updated the two remaining older helper containers,
`kube-validator` and `external-probe`, to image `59fb4d9…`; all ten backend
services now use the same verified runtime image. Validator MCP initialization/
tool discovery and external-probe HTTP responsiveness passed, without validation,
probe or deployment operations against a target application.

Vite was already serving the correct frontend checkout on `127.0.0.1:5174`.
Fresh `npm run build` and `npm run lint` passed. No staging or commit occurred;
a proposed commit message is available in `/tmp/deployflow-commit-message.txt`.

Read-only SSH confirmed target app still ready/available 1/1. A browser smoke
check initially reported unavailable observation. Diagnosed a local networking
issue: reader and deployer containers still shared the *old* network namespaces
of their automatically restarted SSH-tunnel containers. Validator, just recreated,
matched its tunnel. Recreated only `kube-reader` and `kube-deployer` against their
current tunnel namespaces with the same image/configuration. Tunnels, policies,
flags, credentials and cluster resources were not changed by this refresh.

Final read-only checks passed: all three identity services share their current
tunnel network namespace; 10/10 backend services run the verified image; API
`/health/ready` returns ready. Real desktop Playwright sign-in, lifecycle refresh
and reload passed with current 1/1 ready/available, durable succeeded, no browser
runtime/console errors and no Vite overlay. Evidence is outside the repository:
`/tmp/deployflow-latest-local-smoke.json` and `/tmp/deployflow-latest-local.png`.
Browser plugin was unavailable, so the existing Playwright installation was used.

Operational follow-up: tunnel container restart/resume may require recreation
of its dependent reader/validator/deployer container because shared network
namespace attachment can become stale. This is a local Compose recovery issue;
record and diagnose it explicitly rather than widening RBAC or changing cluster
resources. It does not establish automatic-release/rollback/replay acceptance.

### What changed and what is running

1. Authenticated `PUT /api/v1/applications/{id}/deployment-policy` disabled only
   app3213214 (`ad7c395b-836d-4874-a5a6-e35ba836f366`) and app31312
   (`36ae77de-39eb-45bb-9a33-07971d08a955`). All four existing policies were
   then verified disabled. Other policies were not enabled.
2. Stopped exactly `api worker github-worker delivery-worker dispatcher webhook
   kube-deployer kube-reader`. PostgreSQL, Redis, Keycloak, validator, external
   probe and tunnels were not recreated. Source/image hashes were rechecked.
3. Took a fresh private custom-format backup after writer stop. Full isolated
   restore matched revision/counts: `91d2e0a7b534`, 104 applications, 5 runs,
   5 releases and zero enabled policies. No live DB restore was performed.
4. Updated ignored `.env` only for the approved image and execution/cleanup
   selectors (0600). Ran:

   ```bash
   DF_BACKEND_IMAGE=deployflow-backend:phase7-source-lifecycle-20261021 docker compose run --rm --no-deps migrate alembic upgrade c3a71d9e4208
   DF_BACKEND_IMAGE=deployflow-backend:phase7-source-lifecycle-20261021 DF_DEPLOYMENT_EXECUTION_ENABLED=false DF_DEPLOYMENT_CLEANUP_ENABLED=false docker compose --profile phase5 --profile phase7-delivery --profile phase7-executor up -d --no-deps --no-build --force-recreate api worker github-worker delivery-worker dispatcher webhook kube-deployer kube-reader
   ```

   Live schema is **`c3a71d9e4208`**; original application/run/release counts
   were preserved. All eight services now run image
   `sha256:59fb4d9a76995c7d8b9cba6f185f1081ce5dfffcdef9bcb07d4bc12e16b72232`.
   Running dispatcher hash is
   `57b86518771b810a9fcfe85ded5ca985f81a8e8f2a1c2c0a312172ed3fd38b8c`.
5. Real OIDC sign-in and browser reload passed. Fresh reader observation showed
   ready/available 1/1. Broker replay naturally blocked both expired jobs with
   `execution_deadline_exceeded` and moved their runs to reconciliation; no
   database status/deadline reset was used.
6. For the approved baseline recovery, enabled execution in `.env` and recreated
   only `api dispatcher delivery-worker kube-deployer` with the same image,
   execution true, cleanup false. Through the normal authenticated API enabled
   **only** app3213214 with `allowed_source_sha` restricted to
   `5ee02c99f312269f95854b3c26d0301d6088df00`, then called
   `POST /api/v1/deployments/1a326ed7-c5e4-43ef-8443-fb4c1e36c7b0/retry-execution`
   with a distinct idempotency key. Inventory was verified fully applied before
   retry; the executor followed its observe-only path, with no reapply.
7. Deployment reached **`succeeded`**, execution job **`completed`**. Fresh
   rollout timestamp `2026-10-21T14:51:08.136978Z`: desired/updated/ready/available
   **1/1/1/1**, generation/observedGeneration 1. Internal `/healthz` passed at
   `14:51:08.504867Z`. Browser details and page reload displayed success correctly.

Current authorization is intentionally restricted to that baseline SHA; it
**does not authorize marker-2, marker-3 or arbitrary future source revisions**.
Other application policies remain disabled. Global execution is enabled in the
four relevant services above; the GitHub/analysis/webhook processes retained
execution false. Cleaner remains disabled/unprovisioned locally.

### Durable baseline and resource evidence

Application `ad7c395b-836d-4874-a5a6-e35ba836f366`, deployment
`1a326ed7-c5e4-43ef-8443-fb4c1e36c7b0`, release
`2feab925-c237-4651-aebe-d495e580195b` now form the successful baseline.
Image remains
`ghcr.io/damibegud/deployflow-node-express-fixture@sha256:f1b3a237d1baa8e1391ccfa20f3e41859c3132c0af3dd57a0fa2797a47416fd9`.
No new application release or rollback lineage was manufactured during recovery.

- [Persisted/API baseline and current observation](evidence/phase7-20261021/baseline.json)
- [Real authenticated lifecycle screenshot](evidence/phase7-20261021/baseline-succeeded.png)
- [Namespace identities/spec hashes before recovery](evidence/phase7-20261021/namespace-before-recovery.json)
- [Namespace identities/spec hashes after recovery](evidence/phase7-20261021/namespace-after-recovery.json)

The two namespace snapshots compare exactly equal across all **10** Deployment,
Service, ConfigMap and Pod resources, including unrelated workloads. Only
identity/labels and spec/data digests are retained; no ConfigMap/secret values.
The application resource UIDs remain those in the earlier identity table below.
No RBAC, namespace, checker, token or Kubernetes resources were changed.

Fresh backup: private directory
`/Users/damibegud/.config/deployflow/backups/phase7-activation-otc0ohcf` (0700),
`deployflow.dump` 476168 bytes, SHA256
`a6c71f1b291e2a5f4ba4d2e294422fe0878ecf0353a59f6347882ad0f46fc993`.
Includes 0600 `.env` snapshot, archive index, restore log and verification JSON.
Only the isolated backup-check container/network were removed afterward.
Fallback image remains `deployflow-backend:phase7-recovery-7f3d74b`, with additive
schema retained and execution disabled if fallback is required. Database restore
is a separate operational proposal, never an automatic downgrade.

### Source PR prepared; automatic release remains blocked

Under the approved source-change scope, created draft
[fixture PR #14](https://github.com/DamiBeGud/deployflow-node-express-fixture/pull/14):
branch `phase7/automatic-release-3`, head
`fc6e750d64c32e62de2eb3887eb8b2af39149a44`, base main
`43d762ecf74d86d331bcca4bb34f8395e30dc648`. It changes only `src/app.js` and
`test/app.test.js`, marking `/` and `/api/info` as `phase7-release-3`.
`/healthz`, Dockerfile, workflow and Kubernetes files are unchanged.
Fresh fixture `npm test`: **3 passed**; `git diff --check`: passed.
PR CI [run 39621047976](https://github.com/DamiBeGud/deployflow-node-express-fixture/actions/runs/39621047976),
attempt 1, completed successfully for `pull_request` at the exact PR head. This
run tests/builds but does not publish the deployable push/main image.
PR is draft and **not merged**; main remains unchanged. The remote commit was
created through repository-scoped GitHub App operations; local fixture edits
remain uncommitted/unstaged and were preserved. Main project work is also
uncommitted/unstaged. Do not confuse source-branch CI with deployable push/main CI.

Fresh GitHub read-only audit confirmed App `4957845`, installation `162024583`,
events `pull_request`, `push`, `workflow_run`; required existing permissions are
present. Endpoint remains `https://wh.damibegud.cloud/webhooks/github`, JSON,
TLS validation enabled. DNS is still **NXDOMAIN**, including via public resolver
1.1.1.1. Nameservers are `launch1.spaceship.net` / `launch2.spaceship.net`.
No configured local Cloudflare/ngrok tunnel or DNS access was found; the k3s host
has no active cloudflared/nginx/caddy service. Local nginx serves another project
and was left untouched. The webhook receiver still lacks its signing secret.

The operator has been asked which endpoint to prepare: restore the stable
hostname using DNS/proxy access, or explicitly select a temporary Cloudflare
Quick Tunnel for the bounded acceptance test. No endpoint was published, no
GitHub App hook settings changed and no signing secret rotated while that
configuration decision is pending. Do not treat general approval as a choice
of a new public endpoint/provider or silently substitute manual reconciliation.

### Updated acceptance matrix

| Criterion | Status |
| --- | --- |
| Reviewed infrastructure PR → verified immutable baseline image | Passed |
| New backend/schema and retry correction actually running | Passed |
| Authorized baseline retry → ready → internal health → durable success | Passed |
| Real desktop lifecycle, fresh 1/1 observation, success after reload | Passed |
| Unrelated namespace resources unchanged by recovery | Passed |
| Signed real webhook ingestion | Failed/unconfigured; endpoint decision pending |
| Eligible later source → automatic deployed new digest/version | Unproven; draft PR ready |
| New rollback release restoring previous healthy version | Unproven |
| Worker replay after intentional rollback | Unproven |
| Credential renewal | Not required yet; expiries still October 21, 18:50 / 19:54 UTC |
| Cleanup | Disabled and unproven |
| External application ingress/TLS | Not applicable for this internal-only fixture |

After endpoint configuration, obtain/verify exact final merge SHA authorization
before a fresh completed CI event; do not race source policy against ingestion.
Pending marker-2 remains outside the current source bound. Complete the genuine
automatic update, then exact rollback and relevant-worker replay evidence.
Removing the SHA bound for standing future automation remains a distinct explicit
policy scope. Phase 7 is **not complete**.

## Earlier preparation checkpoint — 2026-10-21 (superseded by live checkpoint above)

**Local implementation prepared and tested; operational acceptance incomplete.**
Phase 7 remains open. No live migration, normal-stack restart, policy change,
GitHub write, token request, or k3s mutation was performed during this work.
Historical approvals below do not authorize the proposals in this section.

The focused [automatic-releases agent handoff](automatic-releases-handoff.md)
documents the missing activation/configuration/evidence, approval boundaries,
and a suggested prompt for resuming this work later.

### Acceptance matrix

| Criterion | Status | Evidence / remaining work |
| --- | --- | --- |
| Reviewed bootstrap PR → merge → verified immutable image | Passed | Fixture PR #12, baseline/build/image below; GitHub and registry queried read-only |
| Due retry publication → worker claim → persisted observations | Passed locally; live unproven | Real PostgreSQL transaction/row-lock test, multiple observation retries, expired lease fencing; old dispatcher still running |
| Current replica/freshness UI, errors, missing resources, reload | Passed locally; live activation unproven | Desktop browser scenarios and reader/API comparison; mocked scenarios do not establish deployment success |
| Subsequent source provenance, compatibility, ordering, policy gates | Passed locally; not activated | New migration, source reconciliation, preflight/executor tests; conservative Node response-data policy |
| Real ready workload and internal reachability | Passed | Target 3213214 is ready/available 1/1; independent internal `/healthz` returns `{"status":"ok"}` |
| Durable succeeded result and matching normal lifecycle UI | Failed currently | Target remains monitoring with stale 0/1; expired execution deadline; no fabricated DB success |
| GitHub event delivery to local receiver | Failed currently | Latest completed delivery 502; current public DNS NXDOMAIN; local receiver lacks signing secret |
| Eligible later commit → genuinely automatic update → new visible version | Unproven | Marker-2 image exists but is not deployed; marker-3 patch is local only |
| Manual rollback → new release → previous healthy version | Unproven live | Local release lineage/replay tests pass; no successful live baseline/rollback target yet |
| Relevant worker restart/replay without unauthorized effects | Unproven live | Isolated regression tests pass; live restart needs separate approval |
| Namespace/ownership and reader/validator/deployer separation | Passed for read-only audit/local tests | Namespace UID verified; no permissions widened; live mutation revalidation still pending |
| External DNS/HTTP/TLS for application | Not applicable | Fixture is internal-only; webhook reachability is a separate required integration |
| Destructive first-deployment cleanup | Unproven | Remains disabled; separate approval/test if requested |

### Current audit, identities, and discrepancy from historical notes

- Main HEAD remains `7f3d74b`. Preserved the preexisting dispatcher correction,
  cleanup/delivery/execution tests, and closure-record edits. All new work is
  uncommitted; nothing staged or pushed. Fixture checkout is
  `/Users/damibegud/uni/BA/deployflow-node-express-fixture`; older checkout untouched.
- Live database is still `91d2e0a7b534`: 104 applications, 5 deployment runs,
  5 releases. No queued/running analysis or GitHub jobs and no pending delivery
  jobs at the last audit. There are two expired `retry_wait` execution jobs:
  `5f8a5f05-9158-49a2-a94d-bdc24d7b77af` (3213214, deadline
  `2026-10-20T21:24:46.979283Z`) and
  `1702bc45-bd5b-420e-95b9-665dcf74bad3` (31312, deadline
  `2026-10-20T20:33:08Z`). Recheck immediately before any activation.
- Both applications' policies remain enabled. Global execution is true in
  API/dispatcher/delivery-worker/kube-deployer; cleanup is false. This contradicts
  earlier disabled-state snapshots. No policies or runtime flags were changed.
- API, dispatcher, delivery-worker, kube-deployer and github-worker use image
  `sha256:a8a3364e93961ce7936767608e38fc3c6a89654a102bbf7bbdc446933e4163bc`.
  Reader, validator, analysis worker, webhook and external probe use older image
  `sha256:7c33ac24a1bc7571ced09da59ef3115a272c4a6a3df3daf5eb9e365f3042a617`.
  The running dispatcher does not contain the retry fix. Edited files do not
  establish runtime activation. OIDC works through local Keycloak; real browser
  sign-in, reload, and authenticated read-only lifecycle requests succeeded.
- Read-only SSH confirmed `connectops@homeserver1` at `91.89.185.146`.
  Namespace `deployflow-test` UID is
  `84a720fd-dc36-4592-a2bb-12c8f54ddc7c`; restricted Pod Security is pinned to
  v1.36. Ingress class is nginx; no Ingress was introduced.
- Private reader/validator tokens expire respectively
  `2026-10-21T18:50:23Z` / `18:50:25Z`; deployer expires
  `2026-10-21T19:54:38Z`. Files are 0600. Renewal requires a separate exact
  ServiceAccount/duration/destination proposal; no token values were printed.

### Selected live application (not the older deleted application)

| Identity | Value |
| --- | --- |
| Application | `node-express-service3213214` |
| Application ID | `ad7c395b-836d-4874-a5a6-e35ba836f366` |
| Infrastructure PR | #12; DB `95ecb4d7-1eb4-4953-a77a-5c627b6f22aa` |
| Approved merge/source baseline | `5ee02c99f312269f95854b3c26d0301d6088df00` |
| Verified build | `09cfbad5-6647-4176-b539-640200fe8797`, run `39498021958`, attempt 1 |
| Deployment | `1a326ed7-c5e4-43ef-8443-fb4c1e36c7b0` |
| Release | `2feab925-c237-4651-aebe-d495e580195b` |
| Release digest | `80be449f819212832ceee59ee386a09c7035f6b68f76c841b22ef88f83c7c08b` |
| Image | `ghcr.io/damibegud/deployflow-node-express-fixture@sha256:f1b3a237d1baa8e1391ccfa20f3e41859c3132c0af3dd57a0fa2797a47416fd9` |
| Deployment UID | `a4e5a93a-75a9-4339-91f4-5da8a4693f79` |
| Service UID | `d4a13c5f-adf7-4f57-8922-4baf83649f1e` |
| ConfigMap UID | `64fae1ec-088e-41a3-b6f8-9135e71271a8` |
| Pod | `node-express-service3213214-8477f6db49-6ltg4`, UID `ffaddc40-5c8b-427f-b645-3da20384f5c4` |

Deployment and Service names are `node-express-service3213214`; ConfigMap is
`node-express-service3213214-config`. Ownership matches this application.
Live desired/updated/ready/available counts are **1/1/1/1**, generation and
observedGeneration both 1; Pod Ready is true, no restarts. Persisted/API snapshot
is desired 1, updated 1, ready 0, available 0; health is null. The defect is stale
worker observation, not evidence that a Running Pod alone means readiness.
Read-only server curl to Service `10.43.136.112:4587/healthz` passed;
`/api/info` has no release marker. This is not marker-2 or marker-3.

Older app `node-express-service31312`, ID
`36ae77de-39eb-45bb-9a33-07971d08a955`, deployment
`ce60fd15-9fe1-4e69-98a1-81af27105f45`, release
`f8b75cb9-8aed-430f-b87c-777e6bf1e032`, has no Deployment, Service, or ConfigMap
remaining. Its old healthy observation cannot establish a current baseline.
The older `node-express-service` partial inventory and unrelated checker were
left untouched. No new live rollback lineage exists.

### Later source build and webhook reachability

GitHub repository ID `1372078565`, App ID `4957845`, installation `162024583`.
Remote main and fixture checkout HEAD both equal
`43d762ecf74d86d331bcca4bb34f8395e30dc648`. Run `39499725243`, attempt 1,
completed successfully for push/main at `.github/workflows/deployflow.yml`.
Artifact `12298414767` (`deployflow-build`, 364 bytes) resolves to verified
registry digest
`sha256:98b2579ed0f2409280a79e57e38d425407efbb97b3a05b95825547f8e4690d3e`
in `ghcr.io/damibegud/deployflow-node-express-fixture`. This image is published,
not deployed. It contains `phase7-release-2` in `/` and `/api/info`.

App webhook URL is `https://wh.damibegud.cloud/webhooks/github`, JSON, TLS
validation enabled. Latest completed delivery `3849431776327909376` at
`2026-10-20T21:22:36.341Z` returned 502; other audited deliveries also failed.
Current DNS inspection gives NXDOMAIN, and public curl cannot connect. The
local receiver has no signing secret configured and returns 503; webhook inbox
has zero rows. Neither a successful build nor a local source refresh proves an
automatic release path.

Configuration choices requiring separate approval:

1. Restore the named public HTTPS endpoint and explicitly route only
   `/webhooks/github` to local receiver port 8002 through a persistent reverse
   tunnel/proxy. Keep the API, Keycloak, Docker, and databases private. Provision
   one matching high-entropy HMAC secret privately for this App/receiver, retain
   TLS validation, and verify signed deliveries. DNS/proxy/tunnel ownership and
   credentials must be confirmed first; laptop/tunnel uptime limits delivery.
2. Use an approved hosted HTTPS tunnel with an exact stable URL and the same
   path/HMAC restrictions. This requires an App webhook URL change and introduces
   a tunnel provider/availability dependency. Do not publish a random endpoint
   or change App settings without approval.
3. Until reachable, authenticated `POST /api/v1/applications/{id}/reconcile-source`
   with an idempotency key provides a manual fallback. First disable execution
   and/or bound the policy through an approved operation. Reconciliation can
   prepare and deploy under enabled policy; it is not a harmless read or evidence
   of automatic webhook ingestion.

### Local implementation and test evidence

- Retained due-retry dispatcher fix; publication failure/backoff, transaction
  ordering, worker claims, slow observation, lease recovery/fencing covered.
- Separate source/baseline provenance and immutable release lineage; exact
  workflow/run/attempt/artifact/registry verification; current-head forward-only
  admission, serialized per app, duplicate/rerun/replay suppression. Old admitted
  sources cannot automatically undo a manual rollback. Optional exact
  `allowed_source_sha` bounds a policy. Authorization is rechecked before apply.
- Conservative Node compatibility accepts response-data/marker changes and
  docs/tests. Approved infrastructure/workflow, dependency/runtime/startup/port/
  health/configuration changes and ambiguous executable edits require review.
  This intentionally does not establish general JavaScript semantic equivalence.
- Separate current read-only observation and historical results; honest missing,
  drifted, unavailable, stale and zero values; bounded/coalesced server reads and
  nonoverlapping browser polling; newest release selection and session restore.
- Prepared additive migration `91d2e0a7b534 → c3a71d9e4208`; SQL in
  [phase7-source-lifecycle-migration.sql](phase7-source-lifecycle-migration.sql).
  Source columns backfill historical merge SHAs. No policy is enabled by migration.
  Downgrade refuses provenance loss. Compose now supports `DF_BACKEND_IMAGE`.
- Fixture patch changes only response marker `phase7-release-2` to
  `phase7-release-3` and matching tests. It is uncommitted/unpushed; `/healthz`
  unchanged. A later exact commit and bounded auto-test need separate approval.

Actual final commands/results (2026-10-21):

| Working directory | Command | Result |
| --- | --- | --- |
| `deploy-flow/backend` | `DF_TEST_IMAGE=deployflow-backend:phase7-source-lifecycle-20261021 sh scripts/test_phase7_isolated.sh` | **166 passed**, 24.35s; one existing Starlette/AnyIO warning |
| `deploy-flow/frontend` | `npm run build` | Passed (TypeScript + Vite) |
| `deploy-flow/frontend` | `npm run lint` | Passed, no warnings |
| `deploy-flow/frontend` | `npm run test:e2e -- tests/phase7-lifecycle.spec.ts tests/oidc-callback.spec.ts --workers=2` | **9 passed**, about 1.2 min; mocked lifecycle APIs |
| `deployflow-node-express-fixture` | `npm test` | **3 passed** |
| Backend isolated image with final worktree mounted | `ruff check app tests scripts migrations` | Failed only on existing `I001` extra blank line in untouched `tests/test_local_oidc.py`; unrelated formatter edit was restored |
| Same isolated check | `ruff check app tests scripts migrations --exclude tests/test_local_oidc.py` | Passed |
| `deploy-flow/backend` | `docker compose config --quiet` | Passed |
| Project and fixture | `git diff --check` | Passed |

The backend runner creates its own PostgreSQL/network, uses no normal Compose
.env, and mounts no live GitHub/Kubernetes credentials. It exercises actual DB
claims/observations, migration preservation, negative provenance, concurrent
notifications, source supersession, revoked/disabled/bounded policy and rollback
replay. No test reset/truncate touched the live database. Local tests do not prove
live deployment or rollback. Existing test-local-OIDC whitespace was restored
rather than keeping an unrelated formatter edit.
That preexisting import-format issue is recorded as an out-of-scope lint item.

Desktop screenshots in `/tmp`: `deployflow-phase7-ready.png`, `-failed.png`,
`-unavailable.png`, `-missing.png`, `-stale.png`, `-zero.png`, and
`deployflow-phase7-rollback-review.png`. The real old-backend read-only session is
`deployflow-phase7-live-before-activation.png`. `/tmp` artifacts are ephemeral;
this record and committed test sources (once separately approved) retain the
reproducible evidence. Final ready screenshot was inspected for history wrapping.

### Prepared activation artifacts (not activated)

Prepared local image: `deployflow-backend:phase7-source-lifecycle-20261021`, ID
`sha256:59fb4d9a76995c7d8b9cba6f185f1081ce5dfffcdef9bcb07d4bc12e16b72232`.
It passed the final isolated suite. Runtime source bytes match the local worktree;
`app/dispatcher.py` SHA256 is
`57b86518771b810a9fcfe85ded5ca985f81a8e8f2a1c2c0a312172ed3fd38b8c`.
No image was published or substituted into running services.

Private backup directory:
`/Users/damibegud/.config/deployflow/backups/phase7-source-lifecycle-bah3zoh0`
(0700; files 0600). `deployflow.dump` is 476000 bytes, SHA256
`24a58ebffed672720f34f0a206b9cf8eb2ad465e6b29ccc0a6cc8f83c47b0bc4`.
Includes private `backend.env.before`, archive index, restore log, verification
JSON. Never print that environment copy. The full dump restored successfully via
`pg_restore --exit-on-error` into isolated `phase7_restore_check`, with original
revision/counts. The pending migration then applied to that copy; fallback ORM
could read all 28 mapped tables and preserved 104 apps/5 runs/5 releases. This
backup predates a final approved writer stop; take and verify a fresh backup
before live migration.
After verification, only the task-created isolated PostgreSQL container
`d2316d26971a5459323e1012cf657eae1562c3e9634340e3e7d07fe9036bc50d`
and network `deployflow-phase7-tests` were removed. Private backup files remain.

For restore, stop the exact DB-writing services with separate approval, retain
the current DB, restore the reviewed dump into a **new** explicitly named recovery
database using `pg_restore --no-owner --no-acl --exit-on-error`, verify schema and
counts, then approve switching the service DB URL. Do not overwrite/truncate the
live DB or assume restoring a database reverses cluster changes. A restore loses
changes after the dump and requires a reconciled cluster/job inventory.

### Next approval proposal: local activation with deployment disabled

**Proposed only.** Scope is this local DeployFlow stack/database; no k3s mutation,
token request, GitHub write, source reconciliation, application deployment,
rollback, cleanup, tunnel, DNS or App setting change is included.

1. Recheck pending jobs, image identities and policies. If new actionable work
   changes this inventory, stop and revise the proposal. Through the existing
   authenticated API, use `PUT /api/v1/applications/{id}/deployment-policy` with
   `{"enabled":false}` for exactly app `ad7c395b-836d-4874-a5a6-e35ba836f366`
   (3213214) and `36ae77de-39eb-45bb-9a33-07971d08a955` (31312). Other policies
   are not enabled or changed. These policies remain disabled after activation.
2. Stop only `api worker github-worker delivery-worker dispatcher webhook
   kube-deployer kube-reader`; keep PostgreSQL, Redis, Keycloak, validator,
   external probe and existing tunnels running. Preserve ignored `.env` privately;
   set `DF_BACKEND_IMAGE=deployflow-backend:phase7-source-lifecycle-20261021`,
   `DF_DEPLOYMENT_EXECUTION_ENABLED=false`, and
   `DF_DEPLOYMENT_CLEANUP_ENABLED=false`. Preserve all credentials/other settings.
3. Take a fresh private custom-format pg_dump after stopping writers; verify a
   full restore in an isolated database. Apply only the reviewed additive
   migration below, then recreate only those eight services. From `backend/`:

   ```bash
   docker compose --profile phase5 --profile phase7-delivery --profile phase7-executor stop api worker github-worker delivery-worker dispatcher webhook kube-deployer kube-reader
   # After the private backup/restore verification and the exact .env edits above:
   DF_BACKEND_IMAGE=deployflow-backend:phase7-source-lifecycle-20261021 docker compose run --rm --no-deps migrate alembic upgrade c3a71d9e4208
   DF_BACKEND_IMAGE=deployflow-backend:phase7-source-lifecycle-20261021 DF_DEPLOYMENT_EXECUTION_ENABLED=false DF_DEPLOYMENT_CLEANUP_ENABLED=false docker compose --profile phase5 --profile phase7-delivery --profile phase7-executor up -d --no-deps --no-build --force-recreate api worker github-worker delivery-worker dispatcher webhook kube-deployer kube-reader
   ```

4. Expect roughly 1–3 minutes of API/worker interruption plus backup verification
   time. Recheck image IDs, dispatcher hash, schema, flags, both disabled policies,
   OIDC and reader-only lifecycle state. The two expired jobs may undergo normal
   guarded deadline transitions into reconciliation; no retry API is included,
   no journal is reset, and disabled flags/policies prevent resource apply.
5. Fallback: recreate those same services with available tested image
   `deployflow-backend:phase7-recovery-7f3d74b` (ID `a8a3364…`) while keeping
   execution/cleanup false and both policies disabled. Retain the additive schema;
   do not run downgrade or restore automatically. New lifecycle/source features
   would be unavailable. A live DB restore or re-enabling execution requires a
   separate proposal/approval.

This boundary comes from the user's explicit restrictions on live migrations,
service interruption, and policy/flag changes. Generic “continue” is not approval.

### Subsequent boundaries and acceptance sequence

After the non-deploying activation, select only app3213214. Inspect its normal
expired-deadline outcome; propose the exact `retry-execution` API operation and
scoped authorization to finish fully journaled read-only rollout/health through
the intended state machine. If resources no longer exist or ownership changed,
stop and propose a new baseline deployment rather than calling it an upgrade.
Require durable `succeeded` and matching fresh UI before choosing it as rollback
baseline. Never directly edit deployment status/deadlines to manufacture success.

Separately restore approved signed webhook reachability. Review/approve the
marker-3 patch, exact GitHub commit/push and a bounded automatic test for only
3213214. Bind policy to the exact resulting SHA; existing marker-2/pending releases
must not execute outside that bound. Observe real delivery, verification,
preflight, policy and execution with no hidden manual reconciliation. Verify a
different running digest, marker-3, internal health, ready/available replicas,
durable success, UI, and unchanged unrelated resource UIDs/specs.

Then propose rollback to the previously succeeded baseline image/configuration,
create a new rollback release through DeployFlow, verify previous version/health
and preserved lineage, and separately approve only relevant worker restarts for
replay acceptance. Replay must not undo intentional rollback. Cleanup remains
disabled and unproven. Phase 7 cannot close until these live criteria pass.

## Historical evidence (retained; current state is above)

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

### Phase 7 recovery preparation — 2026-10-20, subsequently approved

The user requested continued Phase 7 work after the new service did not deploy.
Read-only inspection found repository HEAD `7f3d74b` with the numeric-user fix
committed, but the normal API still loads `node-web-v3` from image
`sha256:7c33ac24a1bc7571ced09da59ef3115a272c4a6a3df3daf5eb9e365f3042a617`.
The running API, dispatcher, and deployer still enable execution and disable
cleanup. Reader/validator access is valid after the renewal above; the separate
deployer token is expired, and its worker remains attached to an old tunnel
network namespace.

Current target is application `slaven-test`, ID
`708446f7-8487-4baf-9b71-1716ab18befd`. Its PR #8 is verified; deployment
`d1a5c288-ae99-4018-af28-4b48634c4756` is `checking`, with a queued execution
job at zero attempts and **no resource inventory**. Its application policy is
enabled. The execution deadline is `2026-10-20T19:06:29Z`. Thus simply renewing
the deployer credential and restarting it could resume already-authorized
queued work; continued Phase 7 work is not blanket approval to deploy it.

Fresh SSH reads confirmed `homeserver1`, the expected namespace UID, the
healthy checker, and the previously approved port-qualified Role. No
`slaven-test` workload exists. The older failed application's Deployment and
Pod are no longer present, while Service `node-express-service` remains. Its
old database inventory was not changed to hide that drift. That application
and its remaining resources are outside this new service's recovery scope.

A fresh image was built locally from committed HEAD as
`deployflow-backend:phase7-recovery-7f3d74b`, ID
`sha256:a8a3364e93961ce7936767608e38fc3c6a89654a102bbf7bbdc446933e4163bc`.
It has not been selected as the normal backend image, published, or activated.
No container interruption or Kubernetes mutation occurred during preparation.
The fresh image passed all **125 backend tests** in the dedicated test database
already at schema head, with the existing third-party deprecation warning.
Targeted Ruff checks and `git diff --check` passed. No live migration was run.

The next approval proposal is strictly a **non-deploying activation**:

1. Through the normal authenticated, audited API, disable only `slaven-test`'s
   deployment policy. Preserve existing run, release, and journal history.
2. Privately back up the ignored local configuration. Set global execution
   to `false`, retaining OIDC and cleanup `false`, so the replacement is not a
   deployment-enabling action.
3. Request only `deployflow-deployer`'s new 24-hour token using
   `sudo -n k3s kubectl -n deployflow-test create token deployflow-deployer --duration=24h`.
   Back up the expired token and atomically store the replacement as `0600`
   outside Git. No reader/validator renewal, RBAC, workload, or SSH-key change
   is included.
4. Preserve the previous local image and select the tested recovery image.
   Recreate only `api`, `github-worker`, `delivery-worker`, `dispatcher`, and
   `kube-deployer`, using `--no-deps --no-build --force-recreate` with the
   delivery/executor profiles. This briefly interrupts those five local
   services and reconnects the deployer to its existing tunnel. Database,
   Redis, Keycloak, other workers, reader/validator, and all tunnels stay up.
5. Verify `node-web-v4`, OIDC, disabled execution/cleanup and target policy,
   worker readiness and credential/RBAC boundaries. Existing queued obsolete
   releases may be marked failed by the normal worker's deadline/source/
   authorization guards; do not directly edit job or deployment state. No
   existing inventory is deleted. Keep execution disabled afterward.

Only after that approval and verification should a fresh `node-web-v4` bundle
be prepared for review. PR approval/creation, merge, policy re-enablement, and
deployment remain later explicit boundaries. This proposal contains no live
deployment, rollback, cleanup, migration, or server resource modification.

### Phase 7 non-deploying recovery — approved and completed on 2026-10-20

The user approved the complete non-deploying recovery scope above. The normal
OIDC-authenticated policy API disabled only `slaven-test` automatic deployment
before executor credential renewal. The policy actor and successful idempotent
command receipt were verified. The policy route does not emit a separate
`AuditEvent` row; the authenticated command/actor records, not a nonexistent
event, are the evidence for this operation.

The expected server identity, namespace UID, and restricted enforcement pinned
to `v1.36` were checked again before requesting only the new
`deployflow-deployer` 24-hour token. Its actual expiry is
`2026-10-21T19:54:38Z` (21:54:38 Berlin time). It was stored atomically as
`0600` outside Git. The unchanged reader/validator tokens still expire around
20:50 Berlin time on 2026-10-21 and therefore become the first credential
renewal boundary if testing continues past that time.

Private recovery backup:
`/Users/damibegud/.config/deployflow/phase7/recovery-20261020-Yvex7t` (`0700`).
It contains the prior ignored `.env`, expired deployer token, and non-secret
container/namespace verification records (`0600` files). No credential value
was printed or copied into Git. The previous selected image was preserved as
`deployflow-backend:phase7-before-recovery-7f3d74b`, ID
`sha256:7c33ac24a1bc7571ced09da59ef3115a272c4a6a3df3daf5eb9e365f3042a617`.

The approved recovery image, ID
`sha256:a8a3364e93961ce7936767608e38fc3c6a89654a102bbf7bbdc446933e4163bc`,
is now selected by `deployflow-backend:local`. Only `api`, `github-worker`,
`delivery-worker`, `dispatcher`, and `kube-deployer` were recreated. The other
12 container identities and start times are unchanged; no tunnel, database,
Redis, Keycloak, reader/validator, analysis worker, webhook, or external probe
was restarted. All five replacements run the tested image with OIDC,
execution **false**, cleanup **false**, and zero container restarts. The GitHub,
delivery, and execution workers report ready without startup tracebacks or
error-level logs. The normal API readiness check passes.

Verification evidence:

- The normal API and UID-10001 deployer load `node-web-v4`. An in-memory preview
  from the current saved assessment/intent emits `USER 1000:1000`. That preview
  was not saved, approved, pushed, or deployed; the previous bundle/release
  bytes remain unchanged.
- The real deployer credential reaches `deployflow-health-checker:http` and
  reads `status: ready`. Real calls still deny Secret listing, other namespace
  workloads, and other Service proxies with HTTP 403. No kube apply/delete or
  RBAC operation was performed.
- Reader observations and the existing bundle's server-side admission dry-run
  pass through the normal API gateways. Validation saved no cluster resources.
- The old expired `slaven-test` deployment
  `d1a5c288-ae99-4018-af28-4b48634c4756` now reports `failed` with
  `execution_deadline_exceeded`; its job is blocked and its inventory is empty.
  The worker's normal deadline guard made this transition. No deployment row
  was edited directly and no old image was deployed.
- The older application's run `17bd1499-c80c-47c2-9c9a-3d3b27419499` now reports
  `reconciling` with the same deadline error, retaining all three journal rows.
  Its drifted/missing workload was not recreated and its remaining Service was
  not deleted. Resolving that unrelated historical inventory needs its own
  reviewed recovery/cleanup decision.
- All application deployment policies are disabled. Execution and cleanup
  remain disabled globally; the old `slaven-test` command is not automatically
  retried or replaced by a new deployment.
- All 16 observed namespace resources retained the same UIDs and substantive
  configuration across recovery. The only server-side mutation was the
  approved temporary TokenRequest. No workload, Role, binding, ServiceAccount,
  ConfigMap, or Service was modified. No PR creation/merge, live deployment,
  rollback, cleanup, database migration, or restore occurred.

Frontend testing used Playwright because the Browser plugin is not available.
The initial probe overlapped the approved API interruption and failed while
that container was being recreated. After readiness, a fresh real Keycloak
sign-in and the `slaven-test` lifecycle/Refresh interaction passed with no
runtime or console errors and no framework overlay. URL:
`http://127.0.0.1:5174/applications/708446f7-8487-4baf-9b71-1716ab18befd`.
The 1600 × 1100 screenshot `/tmp/deployflow-phase7-recovery-auth.png` was
visually inspected: it shows the historical deadline failure and that execution
controls are unavailable, as intended. No deployment or policy-enable button
was invoked. The temporary script is
`/tmp/deployflow-phase7-recovery-auth.mjs`; it contains no credential values.

The recovery activation is complete. Phase 7 remains open: prepare a fresh
reviewed `node-web-v4` bundle and infrastructure PR, verify its merged CI image,
then separately approve global execution/application-policy enablement and
deployment. Preserve the expired attempt as history. Second-release, rollback,
and durable restart/replay proof still require real approved runs. Restoring
the old image or re-enabling execution is not an automatic fallback; either
needs renewed approval and must not resurrect the obsolete bundle.

### Approved deployment ce60fd15 — application healthy, durable completion blocked

The user explicitly approved global execution activation, enabling only
application `node-express-service31312` (`36ae77de-39eb-45bb-9a33-07971d08a955`),
and execution of deployment `ce60fd15-9fe1-4e69-98a1-81af27105f45` in
`deployflow-test`. The exact approved release is
`f8b75cb9-8aed-430f-b87c-777e6bf1e032`, digest
`49dc66efca873bc2ac4729857d2ad312ce98a67ed7b9b1ab05f51828f1ccaa22`, image
`ghcr.io/damibegud/deployflow-node-express-fixture@sha256:c5fb47152a8c574ae5432bddf264b49b5c334ef9c7af3db82a6de82236ff55c4`.

Before activation, read-only checks confirmed the exact host/namespace UID,
restricted enforcement pinned to `v1.36`, valid credentials, and absence of the
three target resources. PR #10's merge SHA
`874bf0669706ab4ad63d8f08eb4a81791e19c01e` is the target branch head; all six
merged files match the approved bundle. CI run `39489383460`, attempt 1, and
artifact `12293824803` pass repository/commit/run/image identity checks. The
public registry digest exists, and its actual image config is `linux/amd64`
with `User: 1000:1000`. Stored release hashes and deterministic image binding
match. A fresh server-side admission dry-run passes all three resources.

Execution was enabled in the ignored `.env`, retaining cleanup disabled.
Only local `api`, `delivery-worker`, `dispatcher`, and `kube-deployer` were
recreated with the already-tested recovery image; all other 13 containers and
start times are unchanged. Private pre-activation configuration and namespace
inventory backup:
`/Users/damibegud/.config/deployflow/phase7/deploy-ce60fd15-0Sy1SZ`.

A real Keycloak administrator session enabled only this application's policy
through the API, then clicked **Deploy this prepared release** in its normal
lifecycle UI. The command returned HTTP 202 and execution job
`1702bc45-bd5b-420e-95b9-665dcf74bad3`. The user was informed that this policy
also permits future verified releases to queue automatically. All other
application policies remain disabled.

The normal executor recorded these three applied resources, all generation 1:

| Kind | Name | UID |
| --- | --- | --- |
| ConfigMap | `node-express-service31312-config` | `6e2ae07c-23ff-45e8-8324-d2a890aa8bd8` |
| Service | `node-express-service31312` | `aa62ad4d-1565-4c27-b161-4b6c2ff48940` |
| Deployment | `node-express-service31312` | `3f122274-9e19-4bad-b319-01d9b1af2ca5` |

Controller-created Pod `node-express-service31312-6f66bfbdfc-znh4m`, UID
`9add2fd4-4abe-4e10-8162-dfb45bf9fe29`, is Running and ready with zero restarts,
using the exact approved image digest. The Deployment has one ready and one
available replica at generation 1. Using the real UID-10001 executor's
`service_health` path and the recorded Service UID/owner/port, an independent
checker request returns `passed` for `/healthz`. Exposure is internal; there is
no Ingress or external DNS/TLS claim. Comparison of all 16 pre-existing
namespace resources shows unchanged UIDs and substantive configuration. Only
the three approved top-level resources and their controller-owned Pod were
added in the observed resource kinds. No unrelated resource was modified.

**Blocking retry defect:** the first observation legitimately returned
`rollout_pending`, leaving the run `monitoring` and job `retry_wait` after one
attempt. The dispatcher publishes the due retry but immediately advances the
same `next_dispatch_at` used by the worker's retry-backoff guard. The worker
then sees a future timestamp and refuses the claim, so the durable state/UI
does not advance despite a healthy application. The job deadline is
`2026-10-20T20:33:08Z`; it must not be silently extended or marked successful.
The Playwright observation ended after 180 seconds still at `monitoring`, with
evidence in `/tmp/deployflow-ce60fd15-observation.png`. No framework failure or
database success override was used. The temporary UI script is
`/tmp/deployflow-ce60fd15-execute.mjs` and contains no credential values.

A local dispatcher correction now changes a due, successfully published retry
from `retry_wait` to `queued` before advancing publication throttling. Backoff
is still checked before publication; failed publication preserves retry state
and time. Existing claim leases and authorization/UID fences remain unchanged.
The same defect affects delivery and cleanup retry publication, so both common
dispatcher paths were corrected without enabling cleanup. Four strengthened
dispatcher-to-worker tests (rollout, health, preflight, cleanup) reproduce the
failure against the old code and pass with the fix. Additional checks retain
future-backoff and failed-publication behavior. Full suite: **127 passed**,
one existing third-party warning; targeted Ruff checks pass. Only the dedicated
test database was used, with no live migration.

Prepared image `deployflow-backend:phase7-retry-dispatch-7f3d74b`, ID
`sha256:6c8cdaeb5aaca8e70ba7b02b86c53648d45d18a992bcb5470a69cb67f040c7b4`,
is built but **not activated**. The runtime worker code is unchanged by this
dispatcher-only fix. Further local dispatcher recreation requires explicit
approval. If the deadline has elapsed by then, a separately approved normal
retry-reconciliation command for this exact deployment is needed; do not reset
the database directly or reapply an unrelated release. Already-applied journal
entries must remain intact and not be applied a second time.

The live application is healthy, but Stage 3's durable/UI completion remains
unverified until that correction is activated and normal observation completes.
Global execution and this application's policy remain enabled as approved;
cleanup remains disabled. No second release, rollback, cleanup, additional PR
merge, or further service restart occurred after encountering this boundary.

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
| Merged fixture reaches healthy deployed state | Approved ce60fd15 Pod/internal health passed; durable completion/UI blocked on staged dispatcher retry fix |
| Second release deploys and manually rolls back | Unproven |
| Worker restart/replay preserves authorization and avoids duplicate effects | Unproven |
| First-deployment cleanup on reviewed inventory | Deferred; separate destructive-test approval |
