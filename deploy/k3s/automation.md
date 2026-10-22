# DeployFlow platform installation

This guide deploys **DeployFlow itself**, using the topology and fresh accounts in
the [hosting guide](README.md). It is separate from DeployFlow's release pipeline
for registered applications. Local tooling is prepared; the hosted platform is
not operationally accepted. Use the [approval boundaries](../../backend/ops/README.md#approval-boundaries)
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

The second command previews only. Its checksum binds both the new Ingress and saved
old route/UID/resourceVersion. Request approval for the exact removal, new Ingress,
TLS Secret and cert-manager resources, brief routing/TLS interruption, and reversal.
Only then repeat with `--approve-sha256 <reviewed-hash>`. A changed old UID/version
or another hostname claimant blocks deletion. No mockup Deployment/Service is deleted.
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
both TLS Secrets. It does not undo database/account setup or restore a database.

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
