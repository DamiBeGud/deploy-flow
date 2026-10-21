# Automatic application releases — agent handoff

Recorded 2026-10-21. This is a handoff, not authorization to activate anything.
All runtime, GitHub, credential and cluster observations below are snapshots;
reinspect them before acting. Phase 7 remains operationally incomplete.

## Update after approved activation — 2026-10-21, 14:55 UTC

The backend/schema activation and baseline recovery below are now **completed**.
Live revision is `c3a71d9e4208`; deployment `1a326ed7…` is durably succeeded,
ready/available 1/1 with passing internal health and matching real UI after reload.
The 10 namespace resource identities/spec hashes were unchanged; no resource
apply/delete, RBAC change or token request occurred. See the new top section of
the [closure record](phase7-operational-closure.md) and its checked-in evidence.

Only app3213214 policy is enabled, restricted to the successful baseline SHA;
other policies and cleanup are disabled. Draft fixture PR #14, head
`fc6e750d64c32e62de2eb3887eb8b2af39149a44`, prepares marker-3 and is not merged.
The local fixture patch remains uncommitted/unstaged; the remote source commit
was created through the GitHub App. Three fresh fixture tests passed.
PR CI run `39621047976` / attempt 1 passed; this is a pull-request test/build,
not the required publish/verification run for a merged push to main.

**Resume with webhook endpoint selection/configuration (step 2), not another
migration or baseline deployment.** Stable webhook DNS still returns NXDOMAIN;
no existing tunnel/DNS access was found and the receiver signing secret is absent.
The operator was asked to choose restored stable DNS/proxy access or a temporary
Cloudflare Quick Tunnel for the bounded test. No endpoint/App change was made.
Automatic-update, rollback and replay acceptance remain unproven. Earlier
snapshot descriptions below are historical where they conflict with this update.

## User-visible goal

After initial infrastructure review/approval, merging a compatible application
PR into the configured branch should automatically build, verify, prepare and
deploy the new application version when that application's policy authorizes it.
Direct pushes to the same branch should follow the same verification path.
The user should see the source version, release/image, progress, readiness,
health and fresh observations on the existing application page.

An image build/publication alone is not a deployment. A manual refresh or source
reconciliation is not proof of automatic event ingestion. The wizard retains
its infrastructure review purpose; ordinary compatible updates reuse that
approval instead of registering another application or opening another
infrastructure PR.

## Read first and preserve the current work

- Applicable `AGENTS.md` and repository instructions.
- [Current operational closure record](phase7-operational-closure.md), especially
  its top checkpoint, IDs, backup verification and exact activation proposal.
- [Backend README](../README.md), including source compatibility, authorization,
  automatic deployment, reconciliation and rollback.
- [Phase 7 plan/checkpoint](../../../BA-backend-plans/08-practical-implementation-plan.md).
- Current git status/diffs in both checkouts before editing. There is extensive
  uncommitted implementation, including changes that predate this handoff.
  Preserve it; do not reset, discard, stage, commit or rewrite history.

Paths:

- Main project: `/Users/damibegud/uni/BA/deploy-flow`
- Plans: `/Users/damibegud/uni/BA/BA-backend-plans`
- Fixture: `/Users/damibegud/uni/BA/deployflow-node-express-fixture`
- Fixture remote: `https://github.com/DamiBeGud/deployflow-node-express-fixture`
- Do not confuse the fixture with the older `node-express-service-fixture` checkout.

## Already implemented locally; verify rather than rebuild from scratch

- Due-retry dispatcher publication/worker-claim correction, including actual
  PostgreSQL transaction ordering, slow rollout retries and lease fencing tests.
- Independent approved infrastructure baseline and application source SHA;
  exact repository/branch/workflow/run/attempt/artifact/image verification.
  Notifications trigger independent GitHub and registry queries.
- Subsequent-source reconciliation and existing release preparation/preflight/
  policy/executor routing. Current-head forward-only ordering, per-application
  serialized admission, duplicate/rerun/replay suppression and rollback history.
- Optional `allowed_source_sha` policy restriction for a bounded live test.
- Current read-only cluster observations separate from immutable deployment
  history, with missing/drifted/unavailable states, freshness, numeric zero,
  bounded polling, newest release selection and reload restoration.
- Additive migration `91d2e0a7b534 → c3a71d9e4208`, reviewed offline SQL,
  isolated test runner and selectable `DF_BACKEND_IMAGE` in Compose.

Implementation entry points:
`app/integrations/github/source_updates.py`, `compatibility.py`, `verification.py`,
`app/api/github_webhook.py`, `app/deployments/{preparation,jobs,queue,execution,rollback,observations}.py`,
`app/api/delivery_routes.py`, `app/dispatcher.py`, and frontend
`src/features/deployments/ApplicationLifecyclePage.tsx`.

Compatibility is intentionally conservative for the supported Node fixture:
documentation/test edits and response-data changes such as a literal release
marker can pass. Changed approved files, dependencies, runtime/startup, ports,
health/configuration or ambiguous executable changes require reassessment.
Do not claim arbitrary application-code changes are supported. If broader
compatibility is required, review and extend the policy with meaningful tests;
never bypass it by accepting every green CI run. Phase 8 runtimes are out of scope.

## What is missing

| Remaining work | Last observed state | Required result |
| --- | --- | --- |
| Activate backend/schema changes | Completed; live DB `c3a71d9e4208`, runtime hashes verified | Retain evidence; do not repeat activation without need/approval |
| Reliable signed webhook delivery | Public endpoint failed; receiver secret missing | Reachable HTTPS receiver, matching private HMAC secret, correct event subscription, accepted durable inbox entry |
| Healthy durable baseline | Completed; durable `succeeded`, health passed, UI fresh 1/1 | Preserve this release as the rollback baseline |
| Explicit bounded authorization | Only app3213214 enabled, restricted to baseline SHA | Review final PR merge SHA and pending releases before authorizing the update |
| Real automatic update | Later image published but not deployed | Fresh source event → verified release → automatic execution → new visible version |
| Rollback and replay acceptance | Tests only | New rollback release restores healthy prior version; worker replay does not undo rollback |
| Ongoing availability | Local backend/tunnel and expiring credentials | Document endpoint uptime requirements and credential renewal procedure |

### 1. Reaudit and activate safely — approval required

Inspect selected images, effective flags, schema, policies and all pending jobs
before startup. Last audit found two enabled policies and two expired retry
execution jobs; simply starting corrected workers with execution enabled is unsafe.

Use the [existing exact non-deploying proposal](phase7-operational-closure.md#next-approval-proposal-local-activation-with-deployment-disabled):
disable only the two named application policies, disable execution/cleanup,
stop the eight specified services, take and verify a fresh private backup,
migrate, and recreate the eight services with the prepared image. Revalidate
the proposal and request explicit approval before doing any of those operations.
The old backup was restore-tested, but predates a final writer stop.

Prepared image was `deployflow-backend:phase7-source-lifecycle-20261021`, ID
`sha256:59fb4d9a76995c7d8b9cba6f185f1081ce5dfffcdef9bcb07d4bc12e16b72232`.
Confirm it still matches the intended source; changing source may require a new
tested image. Keep fallback execution disabled. Do not automatically downgrade
or restore the live database.

### 2. Repair GitHub delivery — separate approval required

App `4957845`, installation `162024583`, repository ID `1372078565`.
Configured endpoint: `https://wh.damibegud.cloud/webhooks/github`.
Last completed audited delivery returned 502; later DNS lookup returned NXDOMAIN.
The local webhook receiver lacked `DF_GITHUB_WEBHOOK_SECRET` and returned 503.

Determine ownership/configuration of DNS, TLS proxy and tunnel before proposing
changes. Choose either the restored stable endpoint or an explicitly approved
stable hosted tunnel. Route only `/webhooks/github` to receiver port 8002; keep
API, Keycloak and databases private. Use one matching high-entropy HMAC secret
in the App and receiver, private outside Git, with TLS validation enabled.
Verify App permissions/subscriptions for `workflow_run` and `pull_request`;
the receiver handles completed workflow notifications. The resulting CI run
must be a trusted **push** run on the configured branch.

Approve exact DNS/tunnel/App/secret/service changes before applying them. Report
availability implications: local-machine sleep, stopped workers or tunnel loss
prevent prompt automatic releases. Document recovery for missed notifications.

Manual fallback exists: authenticated, idempotent
`POST /api/v1/applications/{id}/reconcile-source`. It can cause a deployment
under enabled policy. Obtain the required scope first and identify it as manual
reconciliation; do not use it secretly during the automatic acceptance test.

### 3. Establish the baseline — separate recovery/deployment approval required

Select `node-express-service3213214` only:

- Application: `ad7c395b-836d-4874-a5a6-e35ba836f366`
- Deployment: `1a326ed7-c5e4-43ef-8443-fb4c1e36c7b0`
- Release: `2feab925-c237-4651-aebe-d495e580195b`
- Approved baseline: `5ee02c99f312269f95854b3c26d0301d6088df00`
- Image digest: `sha256:f1b3a237d1baa8e1391ccfa20f3e41859c3132c0af3dd57a0fa2797a47416fd9`

It was live 1/1 ready/available with passing internal `/healthz`, while its
stored rollout remained 0/1, monitoring, with an expired retry deadline.
Reinspect ownership, UIDs and resources. Recover through the intended authorized
state machine/API; never edit DB status/deadlines to manufacture success.
The older app31312 resources are absent and cannot be assumed a rollback baseline.
If this target is now absent, obtain approval for a new baseline deployment and
describe it honestly. Require durable success, health and matching UI.

### 4. Prove a new automatic release — bounded test approval required

Remote main was `43d762ecf74d86d331bcca4bb34f8395e30dc648` (`phase7-release-2`).
Its successful run `39499725243` / attempt 1, artifact `12298414767`, produced
digest `sha256:98b2579ed0f2409280a79e57e38d425407efbb97b3a05b95825547f8e4690d3e`.
That is published-image evidence, not deployment evidence.
An uncommitted marker-3 patch exists in fixture `src/app.js` and its tests;
`/healthz` is unchanged. Review the actual diff before selecting it for a PR.

Before push/merge or policy activation, present one bounded proposal naming the
application/environment, exact patch/commit/release where known, pending-release
disposition, expected resources/effect, interruption and reversal. Arrange the
approval scope and event timing so the test event arrives after its exact source
is authorized; do not race policy activation against event ingestion. If a merge
SHA cannot yet be known, obtain approval for the concrete push/merge first while
execution is disabled, then obtain exact-source authorization before a fresh
CI event. Report the steps; never infer permission for another SHA.

Use `allowed_source_sha` for the exact final source commit and enable only the
selected application's policy. Old marker-2 and unrelated pending releases must
not execute. Observe actual signed delivery, durable inbox/job, independent
verification, release/preflight/policy and executor progress. Record:

- Source SHA, infrastructure baseline, delivery ID, run/attempt, artifact ID,
  verified image digest, release/deployment IDs and previous-release lineage.
- A different image actually running, marker-3 returned by `/` and `/api/info`,
  observed generation, ready/available counts, internal health, durable success,
  and correct lifecycle page after reload.
- Unrelated resource ownership/UID/spec comparisons showing no unintended change.

For acceptance of the user's PR-merge flow, use a reviewed source PR into main;
direct-push coverage alone must not be reported as a demonstrated PR merge.
Do not change branch protection. Do not manually apply Kubernetes resources or
manually reconcile and call the result automatic.

After a successful bounded test, the SHA restriction still blocks later commits.
Obtain a separate explicit policy decision before removing it (`allowed_source_sha:
null`) to authorize future compatible releases. Do not silently turn a one-release
test into standing authorization. An omitted field preserves the existing bound.

### 5. Verify rollback/replay and record the result

Review the exact previous successful image/configuration and request rollback
approval. Roll back through DeployFlow into a **new** release; verify prior
version, readiness, health and preserved history. Then request approval to restart
only relevant workers and prove durable replay does not repeat unauthorized
operations or redeploy an old source over the intentional rollback.

Cleanup remains disabled, separately destructive and unproven. This internal-only
fixture needs no new application Ingress/TLS infrastructure.

## Validation and completion rules

Last results: 166 isolated backend tests, 9 desktop browser tests, 3 fixture tests,
frontend build/lint and diff checks passed. Full backend lint has one preexisting
`I001` in untouched `tests/test_local_oidc.py`; excluding it passes. These are
historical results, not evidence for future edits or live success.

After implementation changes, use `backend/scripts/test_phase7_isolated.sh`
with an explicitly selected tested image; it creates its own PostgreSQL/network
without live credentials. Never run tests that reset/truncate the live DB.
Run proportionate regressions plus the full backend suite after final combined
changes, frontend build/lint/desktop lifecycle checks, fixture tests and
`git diff --check`. Add meaningful coverage for any discovered defects.

Before declaring automatic releases operational, require signed real ingestion,
verified provenance, authorized real execution, new running version/digest,
readiness, health, durable success and correct UI. Record commands, IDs and
outcomes in the closure record; update backend README and Phase 7 checkpoint.
Keep passed, failed and unproven criteria distinct. Phase 7 also requires the
live rollback and worker replay evidence; do not close it based on tests or a Pod.

## Authority that a future agent must preserve

Read-only inspection and safe local edits/tests are authorized. Obtain explicit
approval before **any** k3s mutation or token request, live DB migration/restore,
interrupting existing services/tunnels, changing live policy/execution flags,
Git commit/push/merge, application deployment/recovery, live automatic test,
rollback or cleanup. For each boundary, name the exact app/environment/resources,
commit/release where known, command/API, effect, interruption and reversal; ask
one precise question and wait. Historical approvals and generic “continue” are
not permission for a new scope. This documentation request grants no such approval.

Never expose secrets or broaden RBAC. Reader, validator, deployer and cleaner
identities stay separate. Namespace `deployflow-test` UID was
`84a720fd-dc36-4592-a2bb-12c8f54ddc7c`; reverify. Reader/validator credentials
were due to expire 2026-10-21 around 18:50 UTC, deployer 19:54 UTC; recheck expiry
without printing token values and propose scoped renewal if needed.

## Suggested prompt for the next agent

> Finish automatic subsequent application releases using
> `backend/ops/automatic-releases-handoff.md` and the current top section of
> `backend/ops/phase7-operational-closure.md`. Inspect applicable instructions,
> current diffs and read-only runtime state first. Preserve existing work,
> validate/fix the prepared implementation, and finish safe local preparation.
> Then present the next exact operational proposal and wait for explicit approval.
> Prove PR merge → signed event → verified release → authorized automatic update
> → new healthy version and correct UI, followed by separately approved rollback
> and replay. Do not mistake a published image, mocked tests or manual
> reconciliation for automatic deployment. This prompt does not approve live
> migrations, restarts, policies, GitHub writes, deployments or cluster changes.
