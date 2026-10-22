# Hosting DeployFlow in k3s

**Public UI/API/OIDC endpoints verified; full login and application-release acceptance pending.** These resources host
the platform itself. Managed applications remain in `deployflow-test`.
All live actions require the [operational approval boundaries](../../backend/ops/README.md#approval-boundaries).

**Latest capability review (19:33 UTC):** two applications now exist and one
preflight-passed release waits for execution. Both feature gates remain false;
no application policy/execution job exists. The separate cleanup identity/worker
and exact activation patches are prepared offline, not applied. Webhooks still
target the old receiver and fail with 502; main pushes do not upgrade this
populated platform. Follow the [activation and upgrade limits](automation.md#6-enable-execution-and-cleanup-on-the-installed-platform)
and [review evidence](../../backend/ops/evidence/hosting-capabilities-review-20261022.json).

**Platform upgrade flow now prepared locally:** use the separate protected
**Upgrade DeployFlow platform** workflow after publication. It supports an exact
read-only preview, approved image-only apply and separately approved rollback
from private receipts. It preserves live flags/data/configuration; unsupported
schema/provider/template changes stop for review. Follow the
[merge-to-upgrade instructions](automation.md#7-upgrade-the-installed-platform-after-merging-main).
No live upgrade, feature activation or GitHub-runner deployment has been tested.

The installation observations below predate that capability review. The explicitly
approved 34-object foundation was applied on 2026-10-22.
Three new PVCs are Bound. After separate approval, seven runtime/bootstrap Secrets
were created; the three data-service Pods are Ready, authenticated SQL/Redis checks
pass, and the two empty database backups passed isolated restoration. Execution/
cleanup remain disabled. The separately approved migration Job completed and
PostgreSQL stores `c3a71d9e4208` (29 tables; zero application/job/policy work).
The separately approved identity setup Job completed; account/role/PKCE metadata
was verified without querying password hashes. After separate approval, all twelve
platform Deployments reached `1/1` Ready and available with zero restarts on
2026-10-22. API health, frontend API proxy, provider discovery, real MCP reader
observations and credential-denial checks passed. After separate approval, the
hostname switched to the real platform at **18:21 UTC**. Let's Encrypt certificate
`deployflow-tls` is Ready (expires 2027-01-20); public HTTPS UI/API/discovery/JWKS,
401 for unauthenticated application reads and 404 for identity admin/master paths
passed. Desktop Chromium rendered the UI and opened the actual S256 PKCE login
form without console/request errors or credentials submitted. The API Pod can
fetch public JWKS. Execution/cleanup remain false; no application/job/policy work
exists. Of 111 earlier unrelated resource records, 110 remain unchanged and the
approved old mockup Ingress was removed. Full first login, authenticated API/SSE,
signed webhook and application-release acceptance remain pending. Current results:
[foundation evidence](../../backend/ops/evidence/hosting-foundation-20261022.json) and
[data-service evidence](../../backend/ops/evidence/hosting-data-services-20261022.json) and
[migration evidence](../../backend/ops/evidence/hosting-migration-20261022.json) and
[identity evidence](../../backend/ops/evidence/hosting-identity-20261022.json) and
[workload evidence](../../backend/ops/evidence/hosting-workloads-20261022.json) and
[public hosting evidence](../../backend/ops/evidence/hosting-public-20261022.json).

For GitHub environment settings, CI/publication, stage-by-stage installation and
the hostname reversal procedure, follow the [installation automation guide](automation.md).
Pushes run checks; publication and cluster stages require manual runs and scoped
approval. Installation/publication workflows were recorded on main `54f0498`;
subsequent upgrade/operator work and evidence are prepared for a local reviewed
commit. No hosted-runner installation or upgrade has been verified.

## Selected installation

| Setting | Selection |
| --- | --- |
| Public origin | `https://deployflow.damibegud.cloud` |
| Platform namespace | New `deployflow-system` |
| Data | Fresh application PostgreSQL, identity PostgreSQL, and Redis |
| Identity | Existing Keycloak version/client/claims configuration; newly created accounts |
| Images | `ghcr.io/damibegud/deployflow-{backend,frontend,keycloak}`; amd64, immutable digests |
| Storage | New local-path claims: application PostgreSQL 5Gi, identity PostgreSQL 2Gi, Redis 1Gi |
| Execution / cleanup | Both disabled at installation; no policies imported |

The user subsequently selected **new accounts after the Docker factory reset**,
superseding the earlier account-preservation request. No identity backup is needed
for this fresh installation. Original account IDs, passwords, MFA, extra users,
and sessions are not restored. The existing browser client, role/organization
claims, and Keycloak version are reused with the hosted HTTPS URLs.

Public manifests contain only a realm template with a password placeholder.
Fresh passwords are generated privately; a separately approved one-time setup Job
creates the accounts before the normal Keycloak server starts. This choice does
not authorize a cluster mutation, image publication, or public-host switch.

A fresh application database has no prior release or rollback history and cannot
control existing application resources merely because their labels say DeployFlow.
Leave existing workloads untouched; a new registration/review must pass normal
ownership checks. Do not adopt or relabel old resources to reconstruct history.

## Architecture and boundaries

The frontend serves compiled assets and proxies `/api/` to `api:8000`, including
unbuffered SSE. The Ingress routes the exact webhook URL to the dedicated receiver
and the selected realm/resources paths to Keycloak. Keycloak administration and
management port 9000 are not public; use an explicitly approved private admin
connection for account configuration. OIDC is authorization-code + PKCE; issuer is
`https://deployflow.damibegud.cloud/identity/realms/deployflow-local`.

Reader, validator, and deployer are separate nonroot Pods with projected rotating
ServiceAccount credentials. `DF_KUBE_ACCESS_MODE=incluster` uses the official loader
on each operation and rejects mixed host credentials. The local SSH mode remains
the default. Hosted Pods do not invoke the credential-copy bootstrap or SSH.
Three new RoleBindings reuse the existing narrow target Roles without changing
their rules or existing subjects. No cleaner, cluster-wide grant, or Secret-read
permission is provisioned. API/analysis/GitHub/dispatcher/webhook Pods have no
Kubernetes API token. Reader/validator receive no database or GitHub private key.
The API receives the worker key path as configuration for its PR-enqueue gate;
only the four worker roles mount the actual GitHub PEM.

PostgreSQL and Redis have new persistent claims, separate identity/application
passwords, and internal Services. Default-deny ingress policies restrict internal
callers and the ingress controller; they do not implement an egress allowlist.
The separately approved hostname stage permits nginx ingress-controller
traffic to temporary HTTP-01 solver Pods on TCP 8089. That narrowly selected policy
is required for certificate issuance/renewal under default-deny and is retained
when routing is reversed; it grants no Kubernetes API permissions.
Storage is single-node local-path, not high availability or an off-host backup.
The platform's TLS terminates at ingress; internal service traffic is not mTLS.

## Local preparation tools

Working directory for all commands below: repository root. None applies resources,
publishes images, changes the existing stack, or creates Git commits.

With Docker already available and the existing stack's startup behavior reviewed:

~~~bash
docker build --platform linux/amd64 -t deployflow-backend:hosted-prep-20261021 backend
docker build --platform linux/amd64 -t deployflow-frontend:hosted-prep-20261021 frontend
docker build --platform linux/amd64 -f deploy/k3s/Dockerfile.keycloak -t deployflow-keycloak:hosted-prep-20261021 deploy/k3s
~~~

Obtain separate approval before publishing those images. Verify their exact
registry digests, architecture, and pull access. The renderer requires each exact
approved GHCR repository followed by `@sha256:<64 lowercase hex characters>`.
It does not verify registry existence; syntactically valid placeholder digests are
not deployable evidence. PostgreSQL and Redis now use the immutable registry
indexes rehearsed locally. Every Pod references `deployflow-ghcr`; privately
prepare and approve this read-only pull Secret before installation.

After setting `HOSTED_BACKEND_IMAGE`, `HOSTED_FRONTEND_IMAGE`, and
`HOSTED_KEYCLOAK_IMAGE` to the verified immutable references, render one stage:

~~~bash
python3 deploy/k3s/render.py --stage foundation \
  --backend-image "$HOSTED_BACKEND_IMAGE" \
  --frontend-image "$HOSTED_FRONTEND_IMAGE" \
  --keycloak-image "$HOSTED_KEYCLOAK_IMAGE"
~~~

The output is Kubernetes List JSON (also valid YAML), safe to review without
credentials. Choose `foundation`, `migration`, `identity-setup`, `workloads`, or `ingress`;
`identity-import` is an optional recovery alternative, not part of this installation.
There is no combined activation command. Review the entire output before any apply.

`prepare_secrets.py --output <new-private-directory> --github-key <private-PEM>`
prepares seven Secret JSON files and `initial-accounts.txt` **outside this checkout**. The parent must be
0700, the key 0600 or stricter; output directory is new/0700 and files are 0600.
It generates new database/Redis passwords, separate administrator/operator passwords,
and a webhook HMAC,
copies only the selected existing App key, prints no values, and refuses overwrite.
Do not put values on command lines, commit the output, or feed real credentials
to test runners. Copying a PEM does not prove GitHub authorization.

| Secret | Keys / recipients |
| --- | --- |
| `deployflow-postgres` | `password`; application PostgreSQL |
| `deployflow-identity-postgres` | `password`; identity PostgreSQL and Keycloak |
| `deployflow-redis` | `password`; Redis |
| `deployflow-runtime` | `DF_DATABASE_URL`, `DF_REDIS_URL`; database/broker callers |
| `deployflow-github-key` | `private.pem`; analysis, GitHub, delivery, deployer workers only |
| `deployflow-webhook` | `secret`; API/receiver only; requires separately approved App setting change |
| `deployflow-identity-bootstrap` | `admin-password`, `user-password`; one-time setup init/main containers respectively |
| `deployflow-identity-import` | Optional recovery only: full CLI realm/user exports; offline import Job |

## Fresh identity accounts

`identity-setup` renders `Job/deployflow-identity-setup`, with no Kubernetes API
token, no automatic Job retry, nonroot execution, and a read-only filesystem.
Run it only against the new empty identity database, before starting Keycloak:

1. A backend init container uses the identity database credentials for a read-only
   catalog check. It refuses any existing user-schema objects, including a replay
   after setup. This is necessary because Keycloak may exit successfully when a
   bootstrap user already exists or an import was skipped.
2. The next init container runs the official optimized `bootstrap-admin user` command
   with `--no-prompt` and an environment password reference. It creates temporary
   master administrator **`deployflow-bootstrap-admin`**; the password is never a
   command-line argument.
3. Its main container imports the hosted application realm with `--override=false`.
   The operator **`deployflow-local`** receives the `administrator` application role
   and must change the generated password at first login. New account IDs are
   assigned by Keycloak. Public self-registration and direct password grants for
   the browser client remain disabled; authorization code + PKCE is required.

The normal Keycloak Deployment receives neither bootstrap password, realm template,
nor automatic import/startup-admin flags. Reapplying a completed Job does not rerun
it; do not delete/recreate it or rerun the CLI to reset accounts. Investigate partial
setup or preexisting realms instead of accepting a skipped import as success.

Open the private `initial-accounts.txt` in a local editor for the initial passwords;
do not paste its contents into chat or logs. After first sign-in, store the changed
operator password securely. Through a separately approved private admin connection,
create permanent master administration access and remove the temporary bootstrap
administrator; the generated initial credentials are not a permanent recovery plan.
See [Keycloak bootstrap administration](https://www.keycloak.org/server/bootstrap-admin-recovery).
The public Ingress does not expose the master realm or admin console.

### Optional recovery alternative (not selected)

The existing `prepare_identity_import.py` and `identity-import` stage remain
available for a separately reviewed recovery from a real full CLI export. Include
`master` and `deployflow-local` with inline users (`--users realm_file`); the helper
requires private paths outside Git and preserves account IDs/credentials while
adapting only the hosted URLs/TLS settings. Partial Admin Console exports are not
backups. Rehearse restoration and verify credentials/roles before live use; follow
the [CLI export/import precautions](https://www.keycloak.org/server/importExport).
Choose either fresh setup **or** recovery, never both against the same identity DB.

## Activation sequence requiring separate review

1. Verify `homeserver1` and target namespace UID
   `84a720fd-dc36-4592-a2bb-12c8f54ddc7c`; recheck capacity, Roles, DNS/TLS, jobs,
   and the absence/ownership of proposed platform resources. Review real images
   and secrets privately. The reset Docker engine was confirmed empty; the old
   application jobs/policies are no longer running locally. Do not recreate the
   normal Compose stack from its surviving configuration: it can enable execution
   and is separate from this fresh hosted installation.
2. Approve the foundation (namespace, identities, three target RoleBindings,
   configuration, internal Services, ingress policies, three StatefulSets/PVCs)
   and exact private Secrets. Wait for the new databases/Redis; leave all existing
   application workloads and any private historical backups untouched.
3. Separately approve `Job/deployflow-migrate-c3a71d9e4208`, pinned to revision
   `c3a71d9e4208`, against **only the new** database. Workload init containers read
   the revision and never perform migrations.
4. Separately approve `Job/deployflow-identity-setup` and its bootstrap Secret,
   before any Keycloak server is started against the new identity DB. Verify the
   DB is empty; the first init container also checks this read-only and blocks
   replay. The second init container creates the temporary master administrator;
   the main container imports the new operator/client configuration without
   overwriting existing realms. No automatic Job retry is configured. If either
   step fails or realms already exist, stop and investigate rather than resetting
   accounts. The public realm template contains no real password.
5. Approve the twelve platform Deployments. Confirm actual images, projected
   identity/rotation, denied cross-namespace/Secret operations, worker recovery,
   database persistence, realm import/account setup, and disabled execution.
6. Separately approve the hostname switch: the current
   `deployflow-mockup/deployflow-mockup` Ingress already owns the same host/root
   (observed UID `8adb1de3-8d69-4c54-9e15-51b34d72fe67`). Capture a sanitized
   restore manifest and recheck UID before any removal. Replacing its route with
   `deployflow-system/deployflow` needs explicit approval; do not create competing
   root routes or delete the mockup Deployment/Service/namespace. Expect interruption
   while routing/TLS settles. New TLS secret `deployflow-tls` and cert-manager's
   certificate/ACME resources also belong in this approval. Reversal restores the
   captured old Ingress using its existing TLS secret; retain new PVCs.
7. Verify HTTPS UI, new operator first login/password change and PKCE, API/SSE,
   hosted JWKS reachability from Pods,
   reader/validator, then signed GitHub delivery to
   `https://deployflow.damibegud.cloud/webhooks/github`. Review App-setting changes
   and the local stack's role before switching notification delivery.
8. Authorize deployments only afterward, per application/exact source. Establish
   a new healthy baseline, then automatic update, rollback, and replay evidence.
   Choose/verify off-host backups for both databases; do not enable cleanup.

No transfer of local application data or interruption of unrelated services is
part of a fresh install. Do not run old/new workers against shared state during
cutover; this proposal uses new databases/Redis and no imported jobs/policies.

## Verification and current limits

From repository root, offline checks use synthetic credentials only:

~~~bash
python3 -m unittest discover -s deploy/k3s -p 'test_*.py' -v
git diff --check
~~~

The projected-identity tests can run in a disposable Python 3.12 environment with
the locked pytest/Kubernetes dependencies using
`pytest --noconftest -q backend/tests/test_hosted_kubernetes_access.py` and
`PYTHONPATH=backend`. `--noconftest` is deliberate for this database-free file;
do not use it to bypass database safety in the integration suite. The final full
backend suite must use the [isolated runner](../../backend/README.md#testing).

Recorded preparation checks: fifteen offline hosting/identity tests, eight projected/SSH
identity tests, changed-file Ruff, frontend build/lint, and desktop Chromium
against compiled assets/native nginx passed. The isolated browser used a fake
API; API path/bearer forwarding, SSE delivery, admin/webhook 404 boundaries, SPA
navigation/reload, and zero runtime/console errors were observed. This proves
neither real OIDC nor container/cluster operation.

Docker was reset and is now available with no previous containers/volumes/images.
After the reset, all three `linux/amd64` images built locally. The isolated full
backend suite passed **176 tests** (25.15s, one dependency deprecation warning):

Working directory: `backend/`.

~~~bash
DF_TEST_IMAGE=deployflow-backend:hosted-prep-20261021 sh scripts/test_phase7_isolated.sh
~~~

Disposable container checks passed with nonroot users, read-only roots, dropped
capabilities, and writable temporary/data mounts: PostgreSQL/Redis startup,
fresh application migration to `c3a71d9e4208`, and frontend SPA/API/auth-header,
SSE (first event 0.010s), and private-path boundaries with a fake API. A full CLI
export/adaptation/import into a separate empty identity database preserved two
synthetic realms/users, account IDs, credentials, and role mappings; imported
administrator password login and authenticated user lookup passed. This exposed
and corrected a Keycloak build-time management-path setting. Temporary harness
and log: `/tmp/deployflow-hosted-container-check.py` and
`/tmp/deployflow-hosted-container-check.log`; these are local preparation evidence,
not committed operational acceptance artifacts or real-account recovery evidence.
All disposable containers/networks were removed; only local images/build cache remain.
The import Job's exact command and minimal environment also passed separately.
[Preparation evidence](../../backend/ops/evidence/hosting-preparation-20261021.json)
records local image IDs, source hashes, and results without credentials.

After the new-account decision (2026-10-22 local date), the exact fresh-setup
commands passed against disposable PostgreSQL. The read-only guard rejected replay
before either mutating CLI command. Desktop Chromium (1440×1000) passed operator
login, required password change, authorization callback, and real PKCE token
exchange against the disposable provider, including the API audience, administrator
role, organization claim, and stable account ID. No console/runtime errors occurred.
Browser routing simulated the selected HTTPS origin; this does not verify real
DNS, TLS, Ingress, or the complete hosted platform. The harness is
`/tmp/deployflow-fresh-identity-check.py`, with log of the same name ending in `.log`;
screenshots `/tmp/deployflow-fresh-identity-{password-change,callback}.png` are
temporary synthetic evidence. The Browser plugin was unavailable, so existing
Playwright was used. Disposable containers/networks were removed.

Seven real installation Secret files and initial-account instructions are prepared
privately outside Git (directories 0700/files 0600), separate from synthetic tests.
The existing ignored App key's bytes were preserved, its permissions restricted
to 0600, and a hosted copy prepared outside the checkout. These files have not
been applied; no real account exists yet. Keep their location private and never
feed them to test runners.

### Publication checkpoint (2026-10-22)

The user pushed the preparation as `8d79ed5` (`deployment setup`). There is no
GitHub Actions workflow in that commit; a push does not build/deploy the platform.
All three platform images are now published after explicit scoped approvals.
Authenticated reads verified each registry index and its `linux/amd64` manifest:

| GHCR repository (`ghcr.io/damibegud/`) | Verified tag |
| --- | --- |
| `deployflow-backend` | `hosted-20261022-47b2787a01f9` |
| `deployflow-frontend` | `hosted-20261022-627f7561027e` |
| `deployflow-keycloak` | `hosted-20261022-91fbecdd9f5a` |

Full immutable index/platform digests are in the preparation evidence above.
Anonymous manifest access returned 401 for all three. The renderer now references
a namespace-scoped pull Secret, prepared separately by `prepare_registry_secret.py`;
no real pull credential has been created/applied. Before installation, approve
that read-only credential or explicitly select public visibility for the packages.
Do not place the publisher's write credential in application Pods.

Frontend and Keycloak pushes uploaded layers but were denied before successful
tag/index publication. Their inherited source labels pointed to upstream Nginx
and Keycloak repositories. The local Dockerfiles now override source/URL to
`DamiBeGud/deploy-flow` and clear the unrelated upstream revision, following
[GHCR repository-linking guidance](https://docs.github.com/en/packages/learn-github-packages/connecting-a-repository-to-a-package).
The rebuilt images have byte-identical RootFS layer chains and identical runtime
configuration to the tested originals; only image metadata changed. New image IDs
and comparisons are in the preparation evidence. After a second explicit approval,
both corrected images published successfully with the existing publisher identity.
No registry permission or visibility setting was changed. These metadata repairs
and the updated evidence remain local/uncommitted.

Real fresh-account setup/first login, cluster image pulls, RBAC/network policies/projected
identity operation, TLS/real browser login, signed webhook delivery, and live
operational acceptance remain unproven. No normal Compose stack was recreated,
or cluster resource changed. Registry publication is not deployment evidence.
