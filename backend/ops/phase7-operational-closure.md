# Phase 7 operational status and evidence

## Current acceptance — 2026-10-23, 09:30 UTC

**Core Phase 7 operational acceptance passed for the selected hosted internal
Node fixture.** Automatic DeployFlow self-upgrade, platform rollback and replay
also passed. This conclusion uses real events, registry digests, persisted
success, independent HTTP probes and signed-in browser checks. It does not follow
from code/tests, resource apply or a running Pod alone.

Canonical [machine-readable evidence](evidence/automatic-deployment-acceptance-20261023.json)
contains full application/deployment/release/image/run identifiers, rollback
lineage, failures and recovery outcomes. Screenshots show the
[automatic update](evidence/automatic-release5-20261023.png) and
[rollback](evidence/automatic-rollback-20261023.png), including distinct current
observations and immutable historical results. Recheck live state before operating;
this is a dated checkpoint, not continuous monitoring or standing authorization.

Use the [operational guide](README.md) for procedures and authority boundaries,
[backend guide](../README.md) for release rules, and
[platform automation guide](../../deploy/k3s/automation.md) for hosting.

### Acceptance matrix

| Criterion | Result | Evidence / scope |
| --- | --- | --- |
| Reviewed infrastructure PR → merge → exact successful CI → immutable baseline | Passed | Fixture PR #15, baseline `6a31e8a`, CI `39835670930`, artifact `12450247871` |
| Authorized baseline → rollout → internal health → durable succeeded | Passed | Deployment `607c97a6`; ready/available 1/1; independent `/healthz` 200 |
| Real signed-in UI, accurate replicas/freshness/history, reload | Passed | Normal public OIDC PKCE, desktop Chromium, zero page errors; screenshots above |
| PR merge → signed notification → verified release → automatic healthy update | Passed | Fixture PR #17, `58852104`, CI `39926381824`, deployment `741a73e7`; no manual reconciliation/execute |
| Direct main push → automatic healthy update | Passed | `e580de3f`, CI `39929481800`, deployment `5fb36ce3`; visible `phase7-release-5` |
| Manual rollback → new release → previous healthy version | Passed | New release `0503c2d1`, restores release `14bf1f82` / visible `phase7-release-3`; history retained |
| Relevant worker restart and broker/webhook replay after rollback | Passed | Four workers restarted; six completed jobs replayed; queues drained; 4 deployments remained 4; duplicate webhook 202; rollback held |
| Main merge → automatic DeployFlow publication/upgrade/public verification/durable success | Passed | Platform PR #5, `c7c9eb26`, publication `39929851070`, automatic run `39930290883` attempt 1, all four jobs green |
| Platform manual image rollback and old-source replay | Passed | Rollback run `39930989454`; replay `39930290883` attempt 2 returned `already_admitted`; all twelve workloads stayed at rollback source |
| Ownership, namespace, credential separation | Passed for recorded checks | Ten live authorization checks; no RBAC expansion; reader/validator/deployer separate; cleaner absent |
| Unrelated resources unchanged | Passed for recorded inventory | 250 identity/spec/data fingerprints unchanged; only three selected app resources added; excludes Pods/transient status/Secret values/unrecorded kinds |
| First-deployment inventory cleanup | Unproven / disabled | Separate destructive acceptance test; no cleaner identity deployed |
| External managed-application DNS/HTTP/TLS | Not applicable | Fixture is internal-only; platform HTTPS independently verified |
| Current private backup full restore / off-host recovery | Unproven | Archive catalog/full SQL reads passed; no live restore or current full restore rehearsal |

### Environment and authorization

- Public UI: **https://deployflow.damibegud.cloud**; host `homeserver1`.
- Platform namespace `deployflow-system`, UID `d611539e-df7d-4826-b76d-2bd0c4d97225`.
  Application namespace `deployflow-test`, UID `84a720fd-dc36-4592-a2bb-12c8f54ddc7c`.
- Hosted schema `e4b92f7a6103`; execution **enabled**, cleanup **disabled**.
  The additive migration ran after private backups and reviewed maintenance.
- Only `node-express-service2` (application `2202caf2-759b-4fd2-84c2-58f8fac226ba`)
  authorizes future compatible commits on `main`, using
  `node-source-compatibility-v1` with no exact-SHA restriction. Other applications
  remain disabled. Changed infrastructure/deployment requirements still block.
- GitHub App `4957845`, installation `162024583`, delivers signed completed-workflow
  notifications to the hosted `/webhooks/github`; successful ingestion is recorded.
- Automatic platform publication/upgrade are enabled. Root-owned controller/baseline
  `a4ec32c6664158aa10696de9eb3055004e326bb4` and policy
  `0dc56523ffe92244cb106af97e797cf05cde4cea4e3968ac7fc545626e7b2acf`
  authorize future compatible sources until **2026-12-22 08:00 UTC**. Protected
  controller/workflow/infrastructure/dependency/schema changes require fresh review.
- The automatic SSH key accepts only the pinned controller protocol; arbitrary
  shell execution was rejected. The separate manual environment requires review.
  SSH port 22 is an environment variable; no repository secrets were added.
- Reader, validator and deployer use separate projected rotating tokens. No manual
  TokenRequest was needed. Existing read-only GHCR credential expiry is not reported.
  Keys/passwords/PEMs and database archives remain private, outside Git.

### Release and rollback lineage

Full IDs and immutable image digests are in the JSON evidence. Abbreviations here
identify the same records, not mutable tags.

| Step | Source | Deployment | Release | Image digest prefix | Outcome |
| --- | --- | --- | --- | --- | --- |
| New hosted baseline | `6a31e8a0` | `607c97a6` | `4a949ede` | `d6b121b8` | Manual authorized first deployment; succeeded |
| Initial source update | `fc51858d` | `fe2e20f1` | `14bf1f82` | `925c2265` | Initially blocked; explicit recovery after admission fix; succeeded |
| Automatic PR #17 merge | `58852104` | `741a73e7` | `e5d4c298` | `03ada7fc` | Genuine automatic update; marker 4; succeeded |
| Product manual rollback | `fc51858d` | `2d091feb` | **new** `0503c2d1` | `925c2265` | Restores release `14bf1f82`; marker 3; succeeded |
| Automatic direct main push | `e580de3f` | `5fb36ce3` | `f5f74251` | `d5907185` | Genuine new forward commit after rollback; marker 5; succeeded |

All five releases remain stored. Each recorded rollout reached desired/updated/
ready/available 1 with matching observed generation and a separate passed health
check. Independent internal HTTP responses confirmed the expected marker.
No manual database edits or kubectl application apply manufactured these outcomes.

Platform source `c7c9eb26` genuinely deployed through the automatic workflow to
receipt `7d3c858e74b6e2d3724b4227bbaf7724f821279acf94962326228d4f3d84cfd4`.
Manual image rollback then created succeeded receipt
`4a2f7033a94733ad5c5f463b25277d7bca220ed6a87dda5150779e869ff62be3`,
restoring source `a4ec32c6` without schema/data restoration. Replaying the old
source returned its original admission receipt and left that rollback intact.
At this snapshot the platform is deliberately at `a4ec32c6`; a new forward
**documentation-only** main commit is planned to restore latest main using the
same automatic path. Later Actions receipts establish newer running state.

### Failures retained and corrections verified

1. The first hosted upgrade's server-to-own-origin HTTPS check timed out. Original
   failed receipt `616c5c08…` remains immutable; verification-only child `e0a864f1…`
   succeeded using independent runner HTTPS evidence. No reapply or success override.
2. The executor could not reach the validator. Only `allow-kube-validator` gained
   the executor Pod selector on TCP 8001; no RBAC widening. Real baseline retries
   then progressed from monitoring 0/1 to durable succeeded 1/1.
3. The first later build hit Kubernetes Update-versus-Apply ownership conflict.
   The fix validates unchanged non-image intent/ownership, dry-runs under the
   separate validator, and uses an image-only UID/resourceVersion-fenced patch.
   The first update needed the explicit preparation-retry API and is **not** counted
   as automatic acceptance. The later PR merge and direct push needed no such call.
4. A short SSH port secret made GitHub suppress receipt-valued job outputs. Port
   configuration moved to an environment variable; finalization now reads exact
   same-run/attempt checksum-verified metadata. The setup run required manual use
   of the intended finalization protocol; corrected and genuine automatic runs
   later passed all four jobs. The failed Actions run was not relabelled successful.

### Checks run for the final implementation

Commands use isolated owned infrastructure, never the live database test-reset
fixtures. Detailed commands/results are in the JSON evidence.

| Working directory | Command / check | Result |
| --- | --- | --- |
| `backend` | `DF_TEST_IMAGE=sha256:47b2787a01f93371114ccbd5426ea1b4628e8b50446bb6735fe23a55ebbceb24 sh scripts/test_phase7_isolated.sh` | 199 passed; one existing anyio warning |
| Repository root | `DF_UPGRADE_TEST_POSTGRES=1 python3 -m unittest discover -s deploy/k3s -p 'test_*.py' -q` | 103 passed, including four real isolated PostgreSQL cases |
| `frontend` | `npm run lint` and `npm run build` | Passed |
| `frontend` | `PLAYWRIGHT_BASE_URL=http://127.0.0.1:5176 npm run test:e2e -- tests/phase7-lifecycle.spec.ts tests/oidc-callback.spec.ts` | 12 passed; mocked API/OIDC, distinct from live browser evidence |
| Isolated fixture worktree | `npm test` | 3 passed for each marker update |
| Repository root | Ruff, actionlint (`-shellcheck=`), offline migration SQL, `git diff --check` | Passed; ShellCheck unavailable |

### Limits and follow-up work

- Cleanup stays disabled/unproven; no namespace, unrelated workload or orphan
  resource was removed. External fixture exposure was not added.
- Compatibility is a conservative Node deployment-contract check, not arbitrary
  runtime support or proof that every green build is safe to deploy.
- Single-node storage and same-server backups are not HA or off-host disaster
  recovery. Both current archives were fully read to SQL, not fully restored.
- Renew/review the expiring platform policy before December 22. Future incompatible
  schema/dependency/infrastructure changes need their own reviewed maintenance.
- Two existing high development/build npm advisories (source-map-js and Vite)
  need a separate dependency update. Dependencies were not silently changed.
- A private temporary acceptance account was used for cross-owner browser/API QA;
  disable it after final verification. Original operator access remains unchanged.
- The original fixture checkout's uncommitted source/test edits were preserved;
  new live-test commits used an isolated worktree. Older PRs #14 and #16 were not
  merged, closed or used as this acceptance proof.
- The sibling planning checkout was read-only. Its older checkpoint is historical;
  this repository provides the current hosted acceptance record.

## Historical evidence retained

These snapshots explain earlier constraints and must not be read as current setup
instructions or fresh test results:

- [2026-10-23 pre-activation implementation](evidence/automatic-deployment-implementation-20261023.json):
  earlier 195/94 test counts and then-unapplied migration; superseded by live acceptance above.
- [2026-10-22 first upgrade failure](evidence/hosting-upgrade-20261022.json),
  [upgrade implementation](evidence/hosting-upgrade-implementation-20261022.json),
  [capability review](evidence/hosting-capabilities-review-20261022.json).
- Fresh hosted installation: [preparation](evidence/hosting-preparation-20261021.json),
  [automation](evidence/hosting-automation-20261022.json),
  [foundation](evidence/hosting-foundation-20261022.json),
  [data services](evidence/hosting-data-services-20261022.json),
  [migration](evidence/hosting-migration-20261022.json),
  [identity](evidence/hosting-identity-20261022.json),
  [workloads](evidence/hosting-workloads-20261022.json),
  [public hosting](evidence/hosting-public-20261022.json).
- [Credential audit and coverage limits](evidence/credential-audit-20261022.json):
  historical known synthetic-key/hash false positives; no generated credentials in Git.
- Earlier **local-stack** acceptance: [baseline JSON](evidence/phase7-20261021/baseline.json),
  [screenshot](evidence/phase7-20261021/baseline-succeeded.png),
  [before inventory](evidence/phase7-20261021/namespace-before-recovery.json),
  [after inventory](evidence/phase7-20261021/namespace-after-recovery.json).
  Its old application IDs/history were not imported into the fresh hosted database.
- [P0 readiness record](k3s/P0-01-readiness.md) is historical server/access evidence.

No historical approval here authorizes a new Git write, deployment, migration,
credential request, service interruption, rollback or cleanup.
