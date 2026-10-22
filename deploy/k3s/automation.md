# DeployFlow platform installation

This guide deploys **DeployFlow itself**, using the topology and fresh accounts in
the [hosting guide](README.md). It is separate from DeployFlow's release pipeline
for registered applications. Public UI/API/OIDC endpoints and navigation to the
hosted login form are verified; full login, authenticated workflows and application
release acceptance remain pending. See [current evidence](../../backend/ops/evidence/hosting-public-20261022.json).
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
| `K3S_SSH_PORT` | `22` |
| `K3S_SSH_USER` | `connectops` |
| `K3S_SSH_PRIVATE_KEY` | Dedicated Actions private key, including its PEM/OpenSSH delimiters |
| `K3S_SSH_KNOWN_HOSTS` | Previously verified host entry for the selected address/port |

Use a dedicated key, not a personal SSH key. Store its private copy outside Git,
directory 0700/file 0600. Installing its public key on the server requires an exact
approval; adding a GitHub secret does not install the key. Forwarding and PTYs can
be disabled with the authorized-key `restrict` option, but that does not restrict
remote command/sudo access. Do not trust an unverified `ssh-keyscan` result.

The recorded server ED25519 fingerprint is
`SHA256:ZrsNCZuNUJizIAelRbYP5xDcY5qAl6sz4WiUzd2Yhnc`. Reverify through existing
trusted access if it changes; do not disable host-key checking.

The Actions jobs explicitly reference `environment: k3s`. No registry publisher
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
frontend lint/build, and frontend/Keycloak image builds. **Push does not publish
images or deploy the platform.** CI receives no runtime/Kubernetes/SSH credentials.

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
active policies/work and preexisting cleaner resources rather than adopting them.

The exact proposal creates `kube-cleaner` ServiceAccount and cleanup-only Deployment
in `deployflow-system`, with a separate projected rotating identity. It creates
`deployflow-test/deployflow-cleaner` Role (get/delete only ConfigMap, Service,
Deployment and Ingress) and `deployflow-platform-cleaner` RoleBinding. It appends
only that worker to PostgreSQL/Redis caller policies, patches only the two feature
flags to true, then restarts API, dispatcher, delivery-worker and kube-deployer
through UID/resourceVersion-fenced Pod-template patches. The cleaner uses the
existing immutable backend image, no GitHub PEM and no new manual token request.
The four existing Deployments use Recreate, so expect a brief API interruption
and worker pause. No migrations, application policy writes, execution requests or
cleanup requests are included. A waiting release still requires its own scoped
application authorization and normal execute API request.

Before restarting, preserve private current backups of **both** databases and
verify the archives. Keep their hashes, validation limits and restore procedure
outside Git. Capability reversal requires separate approval to disable both flags
and restart the four consumers plus cleaner, after inspecting any new queued work.
It does not undo application operations already performed. Do not restore a DB,
delete RBAC/data, or remove application resources as an implicit flag rollback.

### Current review and upgrade limits (2026-10-22)

At the capability audit there are two registered applications, no deployment
policies or execution/cleanup jobs, and one preflight-passed release for
`node-express-service2` waiting for its executor. Capability activation is
prepared, **not applied**; see [review evidence](../../backend/ops/evidence/hosting-capabilities-review-20261022.json).
Cleanup remains unproven until a separate eligible inventory/deletion test is approved.
Analysis currently uses deterministic mode; agent mode additionally needs a private
provider key and explicit provider configuration, not another boolean toggle.

GitHub App `deploy-flow-2` still points to
`https://wh.damibegud.cloud/webhooks/github`; its five latest deliveries failed
with 502. The hosted inbox contains zero deliveries and its 13 completed GitHub
jobs were manually queued. Separately approve changing that App's webhook URL to
`https://deployflow.damibegud.cloud/webhooks/github` and aligning the signing secret
with the existing private receiver Secret. Do not redeliver old events or enable
an application's policy as part of that settings change. The existing App signing
secret cannot be recovered from its masked API response, so retain any private
old value before a change if restoring the old receiver is required.

Pushing main runs checks; it neither publishes nor upgrades the running platform.
Publication remains a manual, protected workflow. The fresh-install operator
cannot upgrade a populated stack. Use the separate upgrade flow below; never
reuse foundation or identity setup to replace a running platform.

## 7. Upgrade the installed platform after merging main

The **Upgrade DeployFlow platform** workflow is manual and uses the same protected
`k3s` environment and five SSH secrets as installation. Configure environment
reviewers/main restrictions where supported. A preview digest plus typed source
confirmation is still required even if GitHub does not offer required reviewers.
It shares installation's concurrency group and also takes a private server lock.
No push, workflow completion, or artifact upload automatically deploys images.

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
   approved plan SHA256. The workflow reports success only after rollout, actual
   running image-ID checks and real HTTPS UI/auth/OIDC checks pass. Full signed-in
   workflows and application-CD acceptance are separate evidence.

### What this upgrade changes and preserves

Only existing platform Deployment **container/init-container images** and a source
annotation are patched, using tests on UID, generation, exact spec and annotations.
API defaults/restart annotations are normalized for compatibility; unknown or
changed workload fields require a separate review. The optional cleaner is upgraded
only when already installed; this flow never installs or enables it.
Live replicas, ConfigMaps/flags, policies, accounts, Secrets, Kubernetes identities,
RBAC, Services, routing/TLS, data StatefulSets and PVCs are preserved. Thus an image
upgrade does not enable the pending execution/cleanup capability proposal.
Ordinary upgrade also refuses a ConfigMap flag change that was not consistently
loaded by its existing consumers; capability activation needs its own review.

Apply refuses pending/running/blocked execution work and active analysis/GitHub/
delivery jobs. It requires identical application schema revision and Keycloak
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
   Successful rollback preserves all receipts and marks the original/failed
   recovery attempts as reverted. An approved no-change recovery can acknowledge
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
