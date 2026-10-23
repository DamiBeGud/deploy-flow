# DeployFlow platform installation

This guide deploys **DeployFlow itself**, using the topology and fresh accounts in
the [hosting guide](README.md). It is separate from DeployFlow's release pipeline
for registered applications. Hosted acceptance on 2026-10-23 passed real login,
automatic platform and managed-application updates, rollback and replay. See the
[current evidence and limits](../../backend/ops/phase7-operational-closure.md).
Use the [approval boundaries](../../backend/ops/README.md#approval-boundaries)
before every live operation. A checksum confirms reviewed bytes; it does not grant
permission to execute them.

## 1. Configure GitHub access

In `DamiBeGud/deploy-flow` → Settings → Environments, create **k3s**. Allow only
the **main branch** through Selected branches and tags. Configure required reviewers
where the repository's GitHub plan supports them. Protect changes to workflows and
deployment tooling through repository review. The selected `connectops` SSH account
has passwordless sudo; anyone able to execute trusted workflows with its key can
administer the server. The three in-cluster application identities remain narrow
and separate; SSH is not the application deployer identity.

Add environment secrets (not environment variables):

| Secret | Value |
| --- | --- |
| `K3S_SSH_HOST` | `91.89.185.146` |
| `K3S_SSH_USER` | `connectops` |
| `K3S_SSH_PRIVATE_KEY` | Dedicated Actions private key, including its PEM/OpenSSH delimiters |
| `K3S_SSH_KNOWN_HOSTS` | Previously verified host entry for the selected address/port |

Set environment **variable** `K3S_SSH_PORT=22` (the workflows default to 22), and
remove any old secret with that name. The port is not a credential. GitHub can
suppress job outputs whose public hashes happen to contain a short secret such
as `22`. Finalization therefore reads the receipt from the checksum-verified
upgrade artifact for its exact controller run/attempt, independently of job
outputs. Missing or invalid metadata produces a safe error artifact before SSH;
inspect the prior receipt before recovery.

Use a dedicated key, not a personal SSH key. Store its private copy outside Git,
directory 0700/file 0600. Installing its public key on the server requires an exact
approval; adding a GitHub secret does not install the key. Forwarding and PTYs can
be disabled with the authorized-key `restrict` option, but that does not restrict
remote command/sudo access. Do not trust an unverified `ssh-keyscan` result.

The recorded server ED25519 fingerprint is
`SHA256:ZrsNCZuNUJizIAelRbYP5xDcY5qAl6sz4WiUzd2Yhnc`. Reverify through existing
trusted access if it changes; do not disable host-key checking.

Installation/manual recovery jobs reference `environment: k3s`. Publication uses
`ghcr-platform-publication`; optional automatic upgrades use
`k3s-platform-automatic`. Keep their secrets and authorization separate. No registry publisher
PAT is needed in Actions: publication uses the job's short-lived `GITHUB_TOKEN`
with `packages: write`. For **each** existing package `deployflow-backend`,
`deployflow-frontend`, and `deployflow-keycloak`, check Package settings → Manage
Actions access and explicitly authorize this repository for publication if needed.
Existing CLI-published packages may not inherit repository access. This is a
separately approved GitHub setting change; do not make packages public implicitly.

References: [environment setup](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments),
[GHCR authentication and repository linkage](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## 2. Prepare private runtime credentials and image pull access

The seven installation Secrets described in the hosting guide are prepared locally;
they do not belong in workflow artifacts or GitHub environment variables. Keep the
GitHub App PEM, webhook HMAC, database passwords and initial account passwords in
the private installation directory. Reuse that prepared directory for this fresh
installation; do not regenerate passwords after database initialization.

### Reuse an existing server-side GHCR credential

Read-only metadata inspection on 2026-10-22 found
`connect-app/ghcr-pull-secret`, referenced by the Connect workloads. Its contents,
scope, expiry and access to DeployFlow packages have **not** been inspected by the
agent. No token needs to be copied to the developer laptop. A subsequent user-run
check confirmed read-only scope and access to all three published images; expiry
was not reported. Its first apply created the platform namespace, then stopped
before Secret creation because plain-text create output was parsed as JSON. That
local helper bug is fixed. The user's subsequent retry successfully created the
pull Secret while retaining the source Connect Secret; read-only inspection
confirmed the namespace and Secret before foundation activation. Foundation,
runtime Secrets, schema/account setup and twelve internal Deployments were then
separately approved and installed; the [hosting guide](README.md) records current
status. Do not rerun fresh-install preparation against that running stack. No
namespace deletion/reset is part of recovery.

Working directory: repository root on the developer Mac. This sends only helper
code over SSH; the credential stays in server memory. The default command checks
the selected server and namespace UID, requires exactly `read:packages` scope,
and verifies the three published platform indexes and Linux amd64 manifests:

~~~bash
ssh -T -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -i "$HOME/.ssh/id_ed25519" connectops@91.89.185.146 \
  'python3 -' < deploy/k3s/reuse_registry_secret.py
~~~

After reviewing the exact scope and obtaining approval, repeat with remote command
`python3 - --apply`. It creates only the labeled/PSA-restricted Namespace
`deployflow-system` and Secret `deployflow-ghcr` if absent. The source Secret stays
unchanged. It refuses another owner's namespace, existing workloads that might be
activated, a different destination credential, publishing scopes, unknown scopes,
or failed registry access. It never prints credentials or writes private files.
No database, account, application workload, route or service is created. Reversal
removes only the new pull Secret after a separate review; retain the namespace.

The foundation stage can subsequently add the remaining resources to that namespace.
For the later seven runtime Secrets, pass `--registry-secret-on-cluster` to
`operate.py --stage secrets`; the remote operator requires the owned namespace pull
Secret to exist. There is no need to manufacture a local copy of it.

### Prepare a new read-only credential instead

Keeping the platform images private additionally requires a GHCR credential with
**read:packages only**, authorized to read all three packages. Creating/requesting
this credential requires approval. A registry write token must not be used in Pods.
Save the selected pull token using a local editor into a private file outside Git.
Never pass its value on the command line. Track its expiry and separately approve
renewal before it expires; cached images do not prove fresh pulls will work.

Working directory: repository root. Set `HOSTED_SECRETS_DIR` to the existing
private Secret directory and `HOSTED_PULL_TOKEN_FILE` to the 0600 token file.
This prepares one file; it does not contact GitHub or Kubernetes:

~~~bash
python3 deploy/k3s/prepare_registry_secret.py \
  --username damibegud --token-file "$HOSTED_PULL_TOKEN_FILE" \
  --output "$HOSTED_SECRETS_DIR/deployflow-ghcr.json"
~~~

Every hosted Pod references this namespace-only pull Secret. PostgreSQL and Redis
are also pinned to the immutable registry indexes rehearsed during local testing.
The pull helper cannot verify the token's server-side permissions; verify actual
registry access privately before approving installation.

## 3. Publish a tested release candidate

After reviewing and authorizing the Git commit/push, the workflows become available
on main. A push or PR runs offline hosting tests, the isolated backend suite,
frontend lint/build, and frontend/Keycloak image builds. By default a push does not
publish or deploy. Explicit automatic activation is documented in section 8.
The checks job receives no runtime/Kubernetes/SSH credentials.

For separately approved publication, run **Platform checks and images** on main
with `publish=true`. Checks must pass before its protected publication job builds
and publishes all three `linux/amd64` images. Tags include source SHA, run ID and
attempt; deployment uses digests. The run produces public artifact
`platform-release-<attempt>`, containing:

- `release.json`: source SHA, three immutable image references, schema revision,
  exact stage resources and SHA256 hashes;
- one reviewable JSON manifest for each stage.

No credentials are included. Download and review the complete artifact and summary;
retain them with acceptance evidence before the 30-day artifact retention expires.
Publication can partially succeed; a failed run is not deployment-eligible. Reruns
have separate attempt-tagged images/artifacts. This workflow is not an image- or
database-downgrade mechanism.

## 4. Inspect, approve, then install one stage

Run **Platform k3s operator** with `stage=inspect` first. This only reads server,
namespace, hostname ownership and, once installed, resource/job status. Validate
GitHub-hosted-runner SSH reachability; successful SSH from a developer laptop does
not prove runner connectivity. Do not open ports or weaken host verification to
bypass an error.

For a stage preview, select its publication run ID and attempt, leaving the approval
digest empty. The workflow verifies the successful manual publication's repository,
workflow, branch, source SHA and attempt, then validates the artifact against its
own renderer. The publication and operator must use the **same main commit**.
The preview prints resource names, immutable images and stage SHA256, without any
SSH mutation. Compare the exact JSON with the requested operational scope.

After explicit approval, rerun that one stage with its exact `approve_stage_sha256`
and confirmation `APPLY <stage> <full source SHA>`. Environment protection still
applies. No step implicitly approves another stage. Order:

| Stage | Effect / prerequisite |
| --- | --- |
| `foundation` | New namespace, SAs, three narrow target RoleBindings, configuration, policies, Services, three StatefulSets and new PVCs. Pods may wait for Secrets. No old data restored. |
| `secrets` (local only) | Create the eight exact private Secrets. Existing equal values are retained; differing values stop rather than rotate credentials. |
| `migration` | Wait for new data services, then run the pinned application schema Job. No existing platform Deployments permitted. |
| `identity-setup` | Separate empty-DB guard, temporary master administrator and new application operator. Does not overwrite/import existing accounts. |
| `workloads` | Twelve Deployments; requires both setup Jobs completed, disabled execution/cleanup and empty application/work state. Waits for rollout readiness. |
| `ingress` (local only) | Separately approved, UID/version-fenced mockup Ingress removal and new platform Ingress/TLS/certificate activation. |

GitHub serializes operator jobs without cancelling a running operation. Do not run
local operations concurrently. There is no force/prune/namespace-delete, resource
adoption, token request, automatic migration on Pod startup, or cleanup action.
An interrupted workloads apply can retry only the identical manifest, with no
application state added. Failed Jobs are not deleted/recreated automatically.
An existing platform with different workloads/state requires a separately reviewed
upgrade, including pending jobs, usable backups of **both** databases, target
schema and fallback-image compatibility. See [backup and recovery](../../backend/ops/README.md#activation-and-database-recovery).

### Local operations and private Secret transfer

Working directory: repository root. Set `HOSTED_SSH_KEY` and `HOSTED_KNOWN_HOSTS`
to private local paths, and `HOSTED_BUNDLE` to the downloaded `release.json`.
The existing default host/user/port are verified again by the remote operator.
No real credential values are printed or copied to remote files by this tool.

~~~bash
python3 deploy/k3s/operate.py --stage inspect \
  --key "$HOSTED_SSH_KEY" --known-hosts "$HOSTED_KNOWN_HOSTS"
python3 deploy/k3s/operate.py --stage secrets --secrets-dir "$HOSTED_SECRETS_DIR" \
  --key "$HOSTED_SSH_KEY" --known-hosts "$HOSTED_KNOWN_HOSTS"
~~~

When reusing the server-side pull Secret, add `--registry-secret-on-cluster` to the
second command; it then reads only the seven local runtime Secret files. Otherwise,
the private directory must contain all eight files.

The second command only lists the Secret names and checksum. After obtaining scoped
approval for those eight Secrets, repeat it with `--approve-sha256 <reviewed-hash>`.
Credential JSON travels over SSH stdin; Kubernetes errors are suppressed because
they can quote submitted Secret values. Inspect failures privately on the server.

### Hostname switch and reversal

Working directory: repository root. Set `HOSTED_HOST_BACKUP` to a new file in a
private directory outside Git. Capture the current mockup route **read-only**:

~~~bash
python3 deploy/k3s/operate.py --stage capture-host --host-backup "$HOSTED_HOST_BACKUP" \
  --key "$HOSTED_SSH_KEY" --known-hosts "$HOSTED_KNOWN_HOSTS"
python3 deploy/k3s/operate.py --stage ingress --bundle "$HOSTED_BUNDLE" \
  --host-backup "$HOSTED_HOST_BACKUP" \
  --key "$HOSTED_SSH_KEY" --known-hosts "$HOSTED_KNOWN_HOSTS"
~~~

The second command previews only. Its checksum binds the new HTTP-01 NetworkPolicy,
Ingress and saved old route/UID/resourceVersion. The policy selects only Pods labeled
`acme.cert-manager.io/http01-solver=true` in the platform namespace and permits TCP
8089 only from nginx controller Pods in `ingress-nginx`. Default-deny otherwise
blocks certificate issuance; see [cert-manager's network requirements](https://cert-manager.io/docs/configuration/acme/http01/#network-policy-considerations).
Request approval for that policy, the exact old-route removal, new Ingress,
TLS Secret and cert-manager resources, brief routing/TLS interruption, and reversal.
Only then repeat with `--approve-sha256 <reviewed-hash>`. A changed old UID/version
or another hostname claimant blocks deletion. The solver policy is applied before
the old route is removed; a policy failure leaves the old route intact. No mockup
Deployment/Service is deleted. No shared ClusterIssuer configuration changes.
Cutover is not atomic: if it stops after removal, inspect before retry/reversal.

For a separately approved reversal, obtain the current platform Ingress UID from
`inspect` (or verify it is absent). Working directory: repository root:

~~~bash
python3 deploy/k3s/operate.py --stage restore-ingress --host-backup "$HOSTED_HOST_BACKUP" \
  --platform-ingress-uid "$HOSTED_PLATFORM_INGRESS_UID" \
  --key "$HOSTED_SSH_KEY" --known-hosts "$HOSTED_KNOWN_HOSTS"
~~~

This previews restoration; use literal `absent` only when that absence was verified.
After approval, repeat with its exact digest. It fences removal of only the new
Ingress, recreates the saved mockup route, and retains platform data/workloads and
both TLS Secrets and the narrowly scoped solver policy. It does not undo
database/account setup or restore a database. Policy/resource cleanup requires
its own review and approval.

## 5. Verify the actual hosted product

Working directory: repository root. After approved cutover:

~~~bash
python3 deploy/k3s/verify_public.py
~~~

This checks valid HTTPS, HTML UI, **JSON** auth configuration and the exact OIDC
issuer. The old mockup's HTML fallbacks must fail these checks. Then record real
operator first-login/password change and PKCE, authenticated API/SSE, reader and
validator access, projected-identity behavior and denied cross-namespace/Secret
operations. Ready Pods alone do not establish these capabilities.

Separately approve the GitHub App webhook URL
`https://deployflow.damibegud.cloud/webhooks/github` and matching privately prepared
HMAC secret; verify real signed deliveries. Old local delivery settings are not
changed by adding SSH secrets. Establish permanent private Keycloak administration
and remove its temporary bootstrap administrator after separate approval.

Execution/cleanup remain disabled and no application policies/history are imported.
Registered-application deployment, automatic upgrade, rollback and replay acceptance
still require the [operational acceptance sequence](README.md#activation-sequence-requiring-separate-review).
Choose an off-host backup destination before storing valuable data. Single-node
local-path storage and image publication are not availability or recovery evidence.

## 6. Enable execution and cleanup on the installed platform

Fresh-install manifests deliberately keep both gates false. Do not change those
defaults to activate an existing installation: the fresh-install operator refuses
populated application/work state. The only boolean product gates are
`DF_DEPLOYMENT_EXECUTION_ENABLED` and `DF_DEPLOYMENT_CLEANUP_ENABLED`. Application
policies, webhook delivery and analysis-provider configuration are separate.

The offline `capabilities.py` planner prepares a reviewed activation from a private
read-only snapshot. Working directory: repository root; set both paths to files
in the private review directory outside Git:

~~~bash
python3 deploy/k3s/capabilities.py --snapshot "$HOSTED_CAPABILITY_SNAPSHOT" \
  --output "$HOSTED_CAPABILITY_PLAN"
~~~

This command never connects to the cluster. Its snapshot contains public
Deployment/ConfigMap/NetworkPolicy specifications, current ownership/UID/version,
job/policy counts, release identities and cleaner-Role absence, not Secret values.
Recheck the server and both namespace UIDs, schema, resource versions, absence of
new resource names, no enabled policies and no pending analysis/release/execution
work immediately before any separately approved mutation. The planner refuses
active policies/work and, when cleanup is requested, preexisting cleaner resources
rather than adopting them.

By default the proposal enables **execution only**, keeps cleanup false, and
restarts only API, dispatcher, delivery-worker and kube-deployer using
UID/resourceVersion-fenced patches. It creates no worker/identity/RBAC resources
and changes no network permissions. The four Deployments use Recreate, so expect
a brief API interruption and worker pause. No migration, application policy write,
execution request or cleanup request is included. Waiting releases still require
explicit application authorization and normal execute selection.

Only a separately requested `--include-cleanup` proposal additionally creates the
`kube-cleaner` ServiceAccount and cleanup-only Deployment, a separate rotating
identity, `deployflow-cleaner` Role (get/delete ConfigMap, Service, Deployment and
Ingress) and `deployflow-platform-cleaner` RoleBinding in `deployflow-test`.
It appends that worker to PostgreSQL/Redis caller policies and enables cleanup.
It uses the existing immutable backend image and no GitHub PEM or manual token.
The planner rejects preexisting cleaner resources for that scope rather than
adopting them. Both variants require both flags initially false; later capability
changes need a fresh reviewed proposal, not replay of a first-activation plan.
The earlier combined activation proposal is historical and must not be applied
as the current execution-only proposal.

Before restarting, preserve private current backups of **both** databases and
verify the archives. Keep their hashes, validation limits and restore procedure
outside Git. Capability reversal requires separate approval to disable only the
activated gates and restart their affected consumers after inspecting new work.
It does not undo application operations already performed. Do not restore a DB,
delete RBAC/data, or remove application resources as an implicit flag rollback.

### Historical capability review (2026-10-22)

The [capability audit](../../backend/ops/evidence/hosting-capabilities-review-20261022.json)
recorded disabled execution/cleanup, no policies and an old webhook URL returning
502. Those execution/webhook blockers were resolved during the
[2026-10-23 activation and acceptance](../../backend/ops/phase7-operational-closure.md);
cleanup remains disabled. Do not repeat completed activation to recover a stack.
Agent-based analysis separately needs private provider credentials/configuration;
it is not enabled by an execution flag. The fresh-install operator cannot upgrade
a populated stack; never reuse foundation or identity setup for an upgrade.

## 7. Upgrade the installed platform after merging main

The **Upgrade DeployFlow platform** workflow is manual and uses the same protected
`k3s` environment and five SSH secrets as installation. Configure environment
reviewers/main restrictions where supported. A preview digest plus typed source
confirmation is still required even if GitHub does not offer required reviewers.
It shares installation's concurrency group and also takes a private server lock.
This manual workflow never starts from pushes; optional automatic operation has
its own independently installed policy and restricted key (section 8).

### Publish → preview → approve → apply

1. Merge the reviewed changes into `DamiBeGud/deploy-flow` main. Wait for checks.
2. In Actions, run **Platform checks and images** on **main**, with `publish=true`.
   Wait for the whole run to succeed. Record its run ID, attempt and full source
   SHA. Private packages must allow this repository's Actions access. The artifact
   `platform-release-<attempt>` binds immutable images, schema, Keycloak version
   and manifests; no credentials are included. Version-2 bundles are required by
   this checkout, so do not reuse an earlier installation artifact.
3. Run **Upgrade DeployFlow platform** on **main** with:

   | Input | Value |
   | --- | --- |
   | `operation` | `preview` |
   | `release_run_id` | Successful publication run ID |
   | `release_attempt` | Its exact attempt, usually `1` |
   | `receipt_id`, `approve_plan_sha256`, `confirmation` | Leave empty |

   The workflow authenticates GitHub's exact repository/workflow/main source/run
   attempt/success, downloads that attempt's artifact and reconstructs it from the
   checked-out renderer. Immediately before SSH it also verifies main has not
   moved, preventing an old workflow rerun from selecting stale main. If main
   advances, publish and preview the new source instead of bypassing the check.
4. Review the job summary and `platform-upgrade-<run>-<attempt>` artifact. Its
   `result.json` names exact Deployment UIDs, before/after images, preserved
   replicas, live feature flags/policies, interruption and reversal. Preview is
   read-only: it creates no server files, backups, locks, tokens or resources.
   Obtain scoped operational approval for that exact plan, including restarts,
   the temporary DB write pause, private backups and resumed existing policies.
5. Run the same workflow on main with the **same** publication run/attempt and:

   | Input | Value |
   | --- | --- |
   | `operation` | `apply` |
   | `approve_plan_sha256` | Full SHA256 printed by preview |
   | `confirmation` | `UPGRADE <full publication source SHA>` |

   It recomputes the plan from current live state; a changed workload, resource,
   schema or authorization context invalidates approval. It does not trust a
   downloaded executable patch. Keep the resulting receipt ID, which is the
   approved plan SHA256. The cluster phase records `awaiting_public_verification` after rollout and running
   image-ID verification. A separate GitHub runner job checks real HTTPS
   UI/auth/OIDC with bounded retries and normal certificate verification. The
   finalizer queries that exact completed job through GitHub, requires fresh checks
   after cluster verification, rechecks live state and only then persists success.
   The server never needs to connect to its own public address. Full signed-in
   workflows and application-CD acceptance are separate evidence.

### What this upgrade changes and preserves

Only existing platform Deployment **container/init-container images** and a source
annotation are patched, using tests on UID, generation, exact spec and annotations.
API defaults/restart annotations are normalized for compatibility; unknown or
changed workload fields require a separate review. The optional cleaner is upgraded
only when already installed; this flow never installs or enables it.
Live replicas, ConfigMaps/flags, policies, accounts, Secrets, Kubernetes identities,
RBAC, Services, routing/TLS, data StatefulSets and PVCs are preserved. Thus an image
upgrade does not change execution or cleanup authorization.
Ordinary upgrade also refuses a ConfigMap flag change that was not consistently
loaded by its existing consumers; capability activation needs its own review.

Apply refuses pending/running/blocked execution work and active analysis/GitHub/
delivery jobs. An unleased blocked **prepare** job with no DeploymentRun is a retained
non-executing result, listed in preview; it remains blocked through worker restart
and requires the product's explicit preparation-retry API. Blocked preflights,
partial deployments, leased jobs and unknown states still prevent upgrade. No
job/status field is rewritten to clear a maintenance guard. It requires identical application schema revision and Keycloak
version. No automatic migration, database restore, identity import or account reset
is implemented. Publish-time checks verify the identity image's actual version;
registry checks bind allowed private repositories, exact index bytes and amd64
manifests before patching. The existing read-only pull credential stays in server
memory; it and all backup contents stay out of Actions logs/artifacts.

During apply, bounded PostgreSQL SHARE locks pause application-table writes while
reads/readiness remain available. This prevents new durable jobs and policy writes
racing the maintenance window. The operator acquires the fence before backups and
image changes, and releases it on normal success/failure. Recreate rollouts interrupt
API/login/workers; incoming writes/webhooks may fail or time out. Inspect failed
GitHub deliveries and separately approve any redelivery/reconciliation afterward.
Image changes stop after ten minutes; if a killed SSH process cannot close its
database session normally, the idle fence expires within twelve minutes.

Both current databases receive private custom-format backups under
`$HOME/.deployflow-upgrades/<receipt-id>/` on `homeserver1` (directory 0700, files
0600). Catalog listing and complete archive-to-SQL reads must pass before image
patches. These checks are not a full restore rehearsal; backups are on the same
server, not off-host disaster recovery. Receipts record validation, applied
resources, previous images and partial failures. No archive is uploaded to GitHub.

### Failure recovery and platform image rollback

A failure stops further patches and retains the private receipt; it can leave a
partly upgraded platform running. **There is no automatic rollback or flag change.**
An unfinished receipt blocks a new upgrade until reviewed recovery. If SSH fails
before returning a receipt, inspect the server using the approved plan SHA256 as
the possible receipt ID; do not rerun a fresh installation or delete the journal.

1. Inspect current readiness, jobs, flags and private receipt. Set `operation` to
   `rollback-preview`, `receipt_id` to the **original upgrade** receipt, and leave
   publication inputs and approval/confirmation empty. Failed/unready workloads
   are allowed for this recovery preview. A broken Keycloak CLI may use the
   explicitly labeled recorded version, only with unchanged DB structure.
2. Review the exact previous images/current identities and obtain a new rollback
   approval. Run `rollback-apply` with the original receipt, the new preview digest
   and `confirmation=ROLLBACK <original receipt ID>`.
3. Rollback patches recorded image targets only, with the same backups/fencing/
   verification. It does not downgrade schema or restore data. It refuses changed
   UIDs, database structure or an intervening image/source upgrade. On the first
   hosted upgrade an old source annotation may be unknown; exact old image digests
   are still retained and no source provenance is invented.
4. A partial rollback can be previewed again using the original upgrade receipt.
   Successful rollback preserves all receipts; resolution of the original/failed
   recovery attempts is derived from the successful receipt's ancestry. An approved no-change recovery can acknowledge
   a failure that stopped before any image patch. Replaying old approval hashes
   cannot silently undo a later state.

Application release rollback remains the separate DeployFlow product operation;
this workflow only rolls back platform images. Destructive cleanup, removal of
backups/RBAC/PVCs and database recovery each need their own scope and approval.

### Local verification of upgrade guardrails

Working directory: repository root. These use no live credentials or database:

~~~bash
python3 -m unittest discover -s deploy/k3s -p 'test_*.py' -q
DF_UPGRADE_TEST_POSTGRES=1 python3 -m unittest discover -s deploy/k3s -p 'test_upgrade_fence.py' -v
~~~

The second command creates/removes only its own disposable PostgreSQL container
with no network, host ports or live credentials. It checks real lock exclusion,
exception release and schema fingerprints. Synthetic orchestration tests cover
durable partial failure, exact rollback and rejection of replayed approval.
These tests do not establish that a GitHub runner has deployed to this cluster.


## 8. Automatic platform and managed-application deployment

These are separate authorization domains. All activation below needs a concrete
review and explicit approval; implementation or historical approvals grant none.
Current recorded activation, receipts and live acceptance are in the
[operational status](../../backend/ops/phase7-operational-closure.md). The
[first-upgrade failure](../../backend/ops/evidence/hosting-upgrade-20261022.json)
is preserved as historical evidence; its verification-only recovery completed.

### Repair verification before activation

The first upgrade applied all images, but the server timed out reaching its own
public origin. The new protocol verifies cluster state on the server and HTTPS
from an independent GitHub job. A passed Pod rollout alone stays
`awaiting_public_verification`. Failure leaves images and receipts intact and
blocks later automatic upgrades; no automatic rollback is implemented.

For the existing failed receipt, use the **manual** upgrade workflow:

1. After separately approved code publication, choose `operation=verify-preview`
   and the original receipt ID. Leave publication and mutation inputs empty.
2. Review the exact verification plan. It rejects changed images, UIDs, source,
   schema, protected configuration or authority and performs no writes.
3. Obtain scoped approval, then select `verify-apply`, its new plan digest and
   `confirmation=VERIFY <original receipt ID>`.
4. This creates a new recovery receipt, verifies exact running images without
   resource patches, backups, DB locks or restarts, and waits for independent HTTPS.
   Finalization queries the exact controller run/attempt's successful
   `public-verification` job. Checks must follow cluster verification and be at most
   30 minutes old. It then rechecks cluster state and marks the **new** receipt
   succeeded. Resolution is derived from its `origin_receipt` ancestry; the
   original failed receipt is not rewritten. This avoids a crash leaving half a
   multi-file success update. `operation=status` exposes the recorded status and
   resolving receipt separately; it does not claim current live health.

Run this **before** migrating the old schema: recovery intentionally refuses schema
drift. Never edit receipt JSON or database fields to assert success. A failed
public check or interrupted finalizer can itself be recovered through a fresh
verification plan using that pending recovery receipt as the next origin. Rollback remains a separately approved operation.

### One-time platform setup

1. Review the code/CI and merge only after separately approving Git writes.
2. Recover the existing failed receipt using the process above.
3. Review private backups and a compatible fallback, then separately approve the
   additive migration `c3a71d9e4208` → `e4b92f7a6103`, compatible images and the
   existing `schema-ready` init commands' expected revision on `api`, `webhook`,
   `dispatcher`, `worker`, `github-worker`, `delivery-worker` and `kube-deployer`. That command change
   is a reviewed Deployment-template update, outside the image-only operator.
   Existing policies retain v1 and their enabled/source-bound values. Automatic
   image upgrades perform no migration or restore and reject a schema mismatch.
   Existing Deployment init commands require the old revision; image rollback after this
   migration needs a tested compatible fallback, not an assumed downgrade.
4. Configure GitHub environments after review:

   | Environment | Purpose / credentials |
   | --- | --- |
   | `ghcr-platform-publication` | Repository's short-lived `GITHUB_TOKEN`, packages write; no SSH/runtime credentials |
   | `k3s-platform-automatic` | Dedicated `K3S_PLATFORM_OPERATOR_KEY` and host/port/user/known-host settings; no general SSH key |
   | `k3s` | Manual installation/recovery key; require reviewer protection before enabling automation |

   The automatic environment needs secrets `K3S_SSH_HOST`, `K3S_SSH_USER`,
   `K3S_SSH_KNOWN_HOSTS`, and the dedicated operator key, plus non-secret variable
   `K3S_SSH_PORT`. It does not
   need `K3S_SSH_PRIVATE_KEY`. Limit deployment branches to main. Existing package
   Actions access must grant this repository Write for all three platform images.
   If reviewer protection cannot protect the manual environment, remove its broad
   SSH secret from repository workflows and use separately controlled manual
   operations before enabling automation. Merely separating environments does not
   protect a broad key from a main workflow that can select its environment.
5. After exact approval, install a root-owned controller checkout at
   `/opt/deployflow-platform/controller/`, preserving repository-relative paths.
   Include all files in `automatic.PROTECTED_FILES` and the public `deploy/k3s`
   modules it imports; retain the referenced public realm template under
   `backend/ops/local-oidc/`. `automatic.py` imports the
   trusted modules from that checkout. Directories must be root-owned and not
   group/world writable; runtime Python files are root-owned 0644. The SSH account
   must not be able to replace the checkout. Do not install candidate scripts
   from an automatic request or widen Kubernetes RBAC. Copy only the reviewed
   public controller files and their listed dependencies; exclude `.env`, `.config`,
   credentials, backups, `.git` and `node_modules`.
6. Generate a **dedicated** SSH key privately (0700 directory, 0600 private key),
   then separately approve its installation. Its authorized-key entry must use:

   Working directory: server account's SSH configuration directory; this is a
   review template, not an instruction to apply without approval.

   ~~~text
   restrict,command="/usr/bin/python3 -E -s /opt/deployflow-platform/controller/deploy/k3s/automatic.py" <approved public key>
   ~~~

   This accepts only `deployflow-platform` and bounded JSON. It cannot execute
   caller-supplied shell/Python, manual apply, migration, rollback or cleanup.
   Retain separate reviewed administrative recovery access. Test rejected shell
   commands before accepting this credential boundary.
7. Generate a disabled policy proposal offline from a real reviewed bundle and
   preview. Working directory: repository root; paths are private files outside Git:

   ~~~bash
   python3 deploy/k3s/prepare_automatic_policy.py \
     --bundle "$PLATFORM_RELEASE_FILE" --preview "$PLATFORM_PREVIEW_FILE" \
     --baseline-sha "$PLATFORM_APPROVED_SHA" --expires-at "$PLATFORM_POLICY_EXPIRY" \
     --output "$PLATFORM_POLICY_PROPOSAL"
   ~~~

   The expiry must be explicit and within 90 days. The proposal binds namespace
   UIDs, Deployment UIDs/inventory, infrastructure fingerprints, flags,
   schema/provider versions and hashes of controller/workflows,
   authorization/execution modules, Dockerfiles, dependency declarations/locks,
   routing and migration files. New or
   changed migrations require review. The proposal is **disabled**; generating it
   does not install or enable anything. By default it authorizes only that exact
   source SHA. First use a concrete bounded test candidate; inspect all current
   publications before enabling either policy or workflow gate.
8. Review and explicitly authorize the exact source for the bounded automatic test. After
   that approval only, install the reviewed policy with `enabled=true` as
   `/opt/deployflow-platform/policy.json`, root-owned and not group/world writable.
   Record its digest, source scope and expiry. Policy replacement/revocation is a
   scoped live configuration change.
9. Separately approve setting repository variables:
   `DF_PLATFORM_PUBLICATION_ENABLED=true`,
   `DF_PLATFORM_CONTROLLER_SHA=<full reviewed controller commit>`, and
   `DF_PLATFORM_AUTO_UPGRADE_ENABLED=true`.
   The pinned runner controller must match the protected installed controller;
   source candidates cannot supply privileged runner code. The gates default off
   and the fixed server policy is checked immediately before resource patches.
   Enabling a gate can immediately deploy the eligible current publication; the
   exact-source policy must already exclude all other commits/pending builds.
   Deploying an already-published candidate through the schedule proves bounded
   reconciliation, not that the next merge triggers the completion-event path.
10. After the bounded test passes, separately approve a fresh disabled policy
    proposal with `--allow-future-compatible`, review its new digest and expiry,
    then install/enable that exact proposal. It permits forward compatible commits
    after the approved baseline. It does not authorize migrations, schema/provider
    changes or protected-controller changes. Renew policy after approved feature
    flag/infrastructure changes; adding a cleaner or replacing a Deployment UID
    cannot silently inherit its scope.


### Automatic platform behavior

A main push runs CI, then publishes digest-bound candidates when publication is
opted in. `Automatic DeployFlow upgrade` reconciles on workflow completion and
every ten minutes while enabled. It selects **current main's latest push run**;
failed/running CI never falls back to an older success. Fork/PR builds do not
publish or deploy. Manual publication remains available separately.

The fixed server controller independently queries GitHub repository/main/source,
workflow, run/attempt, artifact checksum/content, ancestry, protected file hashes
and the registry. Requests carry only a short-lived read-scoped Actions token in
SSH stdin; tokens are never arguments, receipts or artifacts. The existing GHCR
pull-only credential remains server-side. Kubernetes images are always immutable.

An exact plan records its policy admission. Unknown inventory, changed namespace,
schema/provider/configuration or requirements fail closed. Pending jobs produce
`waiting` without a receipt or patch and are reconsidered by scheduled reconciliation.
Unresolved receipts produce `blocked`. Job/server locks serialize operations;
an active upgrade is not canceled when a newer source arrives. Before the first
patch it must still be current main; remaining patches finish that admitted release.
Already admitted sources are never redeployed by reruns, schedules or replay after
manual rollback. A genuinely newer forward commit can qualify. No historical
approval is interpreted as authorization for a different change class.

The cluster, public-verification and finalization jobs expose source, exact
images, waiting/blocking errors, receipt lineage and final state in Actions
summaries/artifacts. Retain metadata before its 30-day retention expires. Preserve
private archives separately; same-server archive reads are not a disaster-recovery
restore rehearsal. Maintenance can interrupt writes/webhooks; review failed GitHub
deliveries and separately approve reconciliation/redelivery rather than silently
claiming event continuity.

### Managed application activation

Reuse the product's existing signed inbox → GitHub verification → compatibility →
immutable release → admission → per-application policy → executor → rollout →
application-health path. Direct pushes and PR merges both qualify through successful
**push CI completion**, not the raw push notification.

1. Review/correct the App receiver URL and signing secret as described in section 6.
   Verify authentic completion events enter the durable inbox and workers claim
   them. Manual source reconciliation is a fallback, not automatic-ingestion proof.
2. Separately approve execution activation, relevant service restarts and pending
   work. Keep cleanup off unless separately requested. Reader, validator and
   deployer identities remain separate; projected tokens rotate in-cluster.
   Deployment approval must also cover its fixed short-lived internal health
   checker resources; those checks do not authorize first-deployment inventory
   cleanup.
3. Select one application's healthy baseline. If its resources are absent, the
   first authorized deployment is a new baseline, not an upgrade.
4. In its lifecycle page review **one source SHA** for a bounded test, then choose
   v1 response-data rules or explicitly opt into v2 existing-handler changes.
   Dependency, import/runtime/startup/port/health/configuration changes, new routes
   and code outside existing handlers still require reassessment/review. V2 also
   rejects imported-helper/free-module references and dynamic property access; it
   permits local request/response handler logic rather than arbitrary Node code.
5. Review pending/in-flight preparation before approval. Enabling a policy does
   not enqueue old `waiting_for_executor` releases, but a future preflight can
   enqueue an eligible release. Only an explicitly authorized application/source
   may execute in the bounded test; unrelated application policies stay disabled.
   A source already verified/prepared while policy was off is not re-admitted by
   replaying its webhook or rerunning CI. For an exact-SHA test, authorize a known
   commit before its first ingestion and separately approve its fast-forward push.
   A PR whose final merge SHA is not yet known needs a separately approved future
   source scope or another concrete bounded event-delivery arrangement. Manually
   executing an old waiting release does not demonstrate an automatic update.
6. Demonstrate a new signed CI completion event, verified different image and
   visible marker, durable succeeded state, ready/available replicas, fresh UI and
   application health without hidden manual reconciliation or kubectl apply.
7. Separately authorize clearing `allowed_source_sha` to null for future compatible
   branch commits. Omitted fields preserve existing scope/rules; explicit null
   clears only the source bound. The compatibility selector cannot be null.
8. Review/approve product rollback and worker restart/replay separately. Rollback
   creates a new release, preserves prior history and prevents old notification
   replay from re-admitting its source. First-deployment cleanup is a separate,
   destructive acceptance test and remains unproven until approved and executed.

### Acceptance before declaring completion

Local tests cover provenance/policy guards, real isolated PostgreSQL fencing and
migration, signed event → dispatcher → worker → persisted release transitions,
synthetic rollout/health/rollback/replay and mocked desktop lifecycle review.
They establish no live automatic-deployment success. The selected hosted fixture
passed the following sequence on 2026-10-23; repeat it when accepting a new
installation or a materially changed contract. Prove one real automatic platform
update, one managed application update, their independent health/UI results, separately approved rollback and worker replay, and unchanged
unrelated resources. Record exact source/image/run/receipt/release identifiers.
No Phase 7 completion follows from publication, apply or a ready Pod alone.
