# DeployFlow operational guide

This is the reusable runbook for the local DeployFlow stack deploying into k3s.
For dated outcomes, image identities, credential expiry, pending work, and evidence,
use the [operational status record](phase7-operational-closure.md).
Reinspect runtime state before acting; documentation is not live inventory.

## Approval boundaries

Obtain explicit approval before **any** Kubernetes mutation or token request,
live database migration/restore, interruption of existing services/tunnels,
live policy/execution-flag change, Git commit/push/merge, deployment/recovery,
automatic-deployment test, rollback, or cleanup.

For each proposal, name the application/environment, exact resources and
commit/release where known, commands/API operations, pending-job disposition,
expected effect/interruption, and reversal path. Ask one precise question and
wait. Historical approvals and a generic “continue” do not grant a new scope.
Read-only inspection and isolated tests do not grant mutation authority.

Never expose credentials, widen RBAC to avoid diagnosis, delete a namespace,
adopt another application's resources, reset live job/status fields, or run broad
Docker cleanup. Keep reader, validator, deployer, and cleaner identities separate.
Use private directories (0700) and credential/config/backup files (0600) outside
Git; do not print secret-bearing environment files or browser storage.

## Runtime inventory and profiles

Before changing an existing stack, record selected and actual container image
IDs, schema revision, effective per-process flags, auth mode, enabled application
policies/source bounds, pending/leased/blocked jobs, and resource ownership/UIDs.
Edited source or an image tag is not proof of the code running in a container.
Do not dump resolved Compose configuration: it can contain secrets.

From `backend/`, these are read-only local checks:

~~~bash
docker compose ps
curl -fsS http://127.0.0.1:8000/health/ready
~~~

Readiness checks database/migration availability, not broker delivery, worker
progress, Kubernetes connectivity, or application health. Read authenticated job/
application state through the API or narrowly selected non-secret database fields.

| Compose profile | Additional services | Prerequisites |
| --- | --- | --- |
| `local-oidc` | `keycloak` | Private bootstrap file; local-only identity provider |
| `phase5` | `kube-reader-tunnel`, `kube-reader`, `kube-validator-tunnel`, `kube-validator` | Separate credentials, CA, pinned SSH host, MCP URLs |
| `phase7-delivery` | `delivery-worker` | GitHub access, validator, compatible schema |
| `phase7-executor` | `kube-deployer-tunnel`, `kube-deployer`, `external-probe` | OIDC, deployer, checker, fresh admission, policy and flag |
| `phase7-cleanup` | `kube-cleaner-tunnel`, `kube-cleaner` | Separately approved cleaner identity and cleanup flag |

Profiles select processes; they do not grant authorization or apply Kubernetes
access manifests. Some depend on `migrate`, so even a Compose startup can run a
migration and activate pending jobs. Select exact services and reviewed images
in an approved activation proposal rather than copying a broad `up --build`.

## Local OIDC

The development-only Keycloak profile publishes loopback port 8180. The backend
permits its exact HTTP configuration only with `DF_ENVIRONMENT=local`.
Production requires HTTPS OIDC; never expose this Keycloak profile publicly.

For a **new local identity setup**, from `backend/`:

~~~bash
python3 scripts/setup_local_oidc.py
~~~

This creates or preserves `~/.config/deployflow/local-oidc/keycloak.env` with
private permissions. Set `DF_LOCAL_OIDC_ENV_FILE` in the ignored `backend/.env`
to that file's absolute path. In the same file, use these non-secret settings:

~~~dotenv
DF_ENVIRONMENT=local
DF_AUTH_MODE=oidc
DF_OIDC_ISSUER=http://localhost:8180/realms/deployflow-local
DF_OIDC_AUDIENCE=deployflow-api
DF_OIDC_JWKS_URL=http://keycloak:8080/realms/deployflow-local/protocol/openid-connect/certs
DF_OIDC_AUTHORIZATION_ENDPOINT=http://localhost:8180/realms/deployflow-local/protocol/openid-connect/auth
DF_OIDC_TOKEN_ENDPOINT=http://localhost:8180/realms/deployflow-local/protocol/openid-connect/token
DF_OIDC_CLIENT_ID=deployflow-local-ui
~~~

From `backend/`, start only a new Keycloak instance after configuration; modifying
an existing identity service or backend still requires scoped approval:

~~~bash
docker compose --profile local-oidc up -d keycloak
curl -fsS http://127.0.0.1:8180/realms/deployflow-local/.well-known/openid-configuration
~~~

Sign in as `deployflow-local` using the private file's
`DF_LOCAL_OIDC_USER_PASSWORD`. It has the local administrator role and
`organization_id=local-organization`. Do not print the file/password.
Realm import runs on first creation; editing the import does not replace
existing users/passwords. Switching an existing backend to OIDC is a separate
approved recreation; it does not itself enable deployment.

## Cluster access and credentials

The client remains restricted to `deployflow-test`. Its default local SSH mode
uses a CA-verified `https://127.0.0.1:<port>` API. Explicit in-cluster mode is part
of the [hosted preparation](../../deploy/k3s/README.md) and uses projected Pod
credentials without the copy bootstrap. Before requests, verify the host identity
and namespace UID against the dated status record.

Provide absolute private paths in ignored local configuration:
`DF_K3S_SSH_TARGET`, `DF_K3S_SSH_KEY_PATH`, `DF_K3S_KNOWN_HOSTS_PATH`,
`DF_KUBE_CA_PATH`, and distinct `DF_KUBE_READER_TOKEN_PATH`,
`DF_KUBE_VALIDATOR_TOKEN_PATH`, `DF_KUBE_DEPLOYER_TOKEN_PATH`.
Use `DF_KUBE_CLEANER_TOKEN_PATH` only for separately approved cleanup.
For Compose networking, the API/delivery processes use
`DF_KUBE_READER_MCP_URL=http://kube-reader-tunnel:8001/mcp` and
`DF_KUBE_VALIDATOR_MCP_URL=http://kube-validator-tunnel:8001/mcp`.

The [initial access manifest](k3s/p0-01-access.yaml),
[deployer access](k3s/phase7-deployer-access.yaml), and
[cleaner access](k3s/phase7-cleaner-access.yaml) are review artifacts.
The initial manifest includes a namespace; never blindly reapply it or delete it
as cleanup. The validator's create/patch verbs authorize dry-run-equivalent
writes, so only the stored-bundle service may use that credential with
`dryRun=All`. Analysis receives no Kubernetes token. Deployer has no delete or
Secret access; cleaner has no create/patch access.

### Token renewal

1. Inspect role-specific expiry and authenticated read access without logging
   bearer tokens. A tunnel connection alone proves no authentication.
2. Propose the exact existing ServiceAccount/namespace, requested duration,
   private destination, processes to recreate, interruption, verification, and
   fallback. Kubernetes may grant a different duration; check actual expiry.
3. After approval, capture each `kubectl create token` result privately into a
   new 0600 temporary file in a 0700 directory. Validate its expected identity
   and actual expiry without printing the token, then atomically replace only
   that role's configured file. Preserve private metadata/backup as appropriate.
4. Recreate only the approved corresponding service(s) so bind mounts and the
   bootstrap's private credential copy load the replacement. A process restart
   is insufficient if an atomically replaced host file leaves an old bind mount.
5. Verify role-specific authenticated reads as the real runtime identity,
   matching tunnel network namespaces, and Services/lifecycle reload.

There is no automatic renewal or admin-token fallback. Expired token backups
cannot restore access. Renewal approval is not permission to change RBAC or
deploy workloads. Current recorded expiries belong in the status record, not here.

### SSH tunnel recovery

The tunnel supervisor retries SSH every five seconds in the same container/
network namespace; pinned host keys, loopback-only forwarding, connect timeout,
and server-alive checks remain enforced. This does not renew credentials.

Reader/validator/deployer processes share their own tunnel's network namespace.
Explicit Compose dependency restart uses `depends_on.restart: true`. Arbitrary
Docker/daemon container replacement can still leave a dependent process attached
to an old namespace. Inspect both containers; after approval, recreate only the
affected tunnel/process pair if necessary. Do not substitute an administrator
credential or widen permissions.

The [readiness script](../scripts/check_k3s_readiness.sh) is read-only but does not
verify scoped backend credentials or image pull authorization. From the repository
root, after setting the intended SSH target/key:

~~~bash
DF_K3S_SSH_TARGET=user@host bash backend/scripts/check_k3s_readiness.sh
~~~

## Webhooks and automatic releases

Configuration and verification rules are in
[the backend guide](../README.md#github-and-build-verification).
For the local backend, choose an approved reachable HTTPS endpoint:

- Restore a stable hostname/proxy with explicit ownership of DNS/TLS/tunnel setup.
- Use an approved hosted tunnel. Temporary URLs require App configuration changes
  when they change and add a provider dependency.
- Until delivery works, use explicitly authorized manual reconciliation.

Forward only `/webhooks/github` to receiver port 8002. Keep API, Keycloak, Docker,
and databases private. Configure one matching high-entropy secret privately in
the receiver and GitHub App; retain TLS verification. Check `pull_request` and
completed `workflow_run` notifications, signed acceptance, and durable inbox/job
creation. Direct push notifications are ignored; successful push CI completion
drives later-build reconciliation. Laptop sleep, receiver downtime, worker/tunnel
loss, or expired cluster credentials interrupt the automatic path.

For a bounded test, review pending releases **before** enabling policy: only the
selected application/source may execute. Account for an older published build and
unrelated applications. The exact final merge SHA may require separate Git-write
approval while execution is disabled, followed by exact-source authorization and
a fresh CI completion event. Do not race policy changes against ingestion or
redeliver old events without reviewing their effects.

An exact `allowed_source_sha` remains a one-source bound after the test. Its
removal (`null`) is a separate decision authorizing future compatible commits.
Manual `POST /api/v1/applications/{id}/reconcile-source` and PR refresh schedule
source reconciliation and can ultimately deploy when gates permit. They require
normal authorization/idempotency; do not report them as automatic ingestion.

## Deployment, health, rollback, and cleanup

Before execution, verify the approved baseline, trusted CI artifact/image,
current source/head, release bytes/digest, admission, identity/namespace/ownership,
OIDC claims, application policy, flags, concurrency slot, and pending work.
Review the exact action; never silently adopt partial or foreign resources.

The executor journals each resource before its bounded request and checks UID/
ownership on retry without forcing apply conflicts. Fully journaled retries
observe without reapplying. Success requires observed Deployment generation,
real ready/available replicas, expected image/Service UID, and internal health.
The separate [checker](k3s/phase7-health-checker.yaml) has no Kubernetes token.
Build it from [Dockerfile.health-checker](k3s/Dockerfile.health-checker) for the
target architecture, publish/verify an immutable image, review its manifest and
approve provisioning separately. Its Service proxy name and RBAC must both use
`deployflow-health-checker:http`. A ready Pod alone is insufficient.

Ingress releases additionally use the credential-free `external-probe` for
HTTP/TLS checks. Do not add Ingress/TLS to an internal-only fixture just to expand
acceptance scope. Current lifecycle observation is a rollout read, not a fresh
health probe.

| Operation under `/api/v1` | Purpose / guard |
| --- | --- |
| `POST /pull-requests/{id}/release-preparation` | Schedule/retry preparation for the verified build |
| `POST /deployments/{id}/retry-preflight` | Retry admission after correcting its cause |
| `POST /deployments/{id}/execute` | Explicitly queue a prepared waiting release |
| `POST /deployments/{id}/retry-execution` | Reconcile an eligible blocked deployment, retaining UID journal |
| `POST /deployments/{id}/rollback` | New immutable release from the prior successful target |
| `POST /deployments/{id}/cleanup` | Separately authorized first-deployment inventory removal |

All these mutations require normal authorization and `Idempotency-Key`.
A reconciliation retry may apply resources; only a verified fully applied journal
takes the observation-only path. It gets a new bounded retry window through the
intended API. Never directly reset database status, deadlines, or journal rows.

Rollback requires the latest successful deployment, a previous successful release,
enabled policy, and no active conflicting deployment. Review target bytes/digest
and image; the new rollback release retains lineage and passes fresh admission/
health. It does not require the old source at current branch head and does not
restore application database data.

Failed first deployments have no rollback target. Cleanup is disabled by default,
needs separate cleaner provisioning/flag/profile, and requires review of release
digest, kinds, names, and exact recorded UIDs. It deletes only owned inventory with
UID preconditions and confirms absence. It is not namespace deletion or a general
recovery shortcut. Keep cleanup disabled unless separately requested and approved.

## Activation and database recovery

1. Inventory jobs, policies, flags, schema, and real images. Identify what could
   execute on startup; disable only approved policies while their authenticated
   API is available, then apply the reviewed execution/cleanup shutdown plan.
2. Prepare replacement/fallback images. Show exact migration range/SQL, affected
   services, expected downtime, pending-job behavior, and rollback limits.
   Review [stage-one SQL](phase7-stage1-migration.sql) and
   [source-lifecycle SQL](phase7-source-lifecycle-migration.sql) against the actual
   schema, not as instructions to repeat historical migrations.
3. After approval, stop the exact database writers. Make a unique private
   custom-format `pg_dump`, environment snapshot, schema/count inventory, archive
   index, and checksum. Do not overwrite an earlier backup.
4. Prove the dump by restoring into isolated PostgreSQL with
   `pg_restore --no-owner --no-acl --exit-on-error` and checking revision/counts.
   A readable archive index alone is not a restore test. Keep live credentials
   out of isolated test containers.
5. Run only the pinned approved migration and recreate only named services with
   reviewed image/configuration, normally with execution/cleanup disabled.
   Verify actual image/source hashes, schema/counts, OIDC, readiness, worker
   claims, and read-only UI before separately enabling execution.
6. Prefer a tested compatible fallback image with execution disabled and the
   additive schema retained. The source-provenance migration deliberately refuses
   downgrade. Do not silently downgrade or restore.

For a separately approved restore, stop writers, preserve the current database,
restore the reviewed dump into a **new explicitly named recovery database**, verify
schema/counts, then approve switching connections and reconciling jobs/cluster
inventory. Restore loses changes after the dump and does not undo Kubernetes
operations. Never run a copied destructive drop/recreate procedure on the live DB.

## Troubleshooting

| Symptom | Inspect before changing anything |
| --- | --- |
| `cluster_access_expired` / 401 | Actual role-specific token expiry, subject, private mount/bootstrap copy |
| `cluster_unavailable` / 503 | Tunnel logs, loopback listener, shared namespace match, MCP reachability |
| `cluster_access_denied` / 403 | Intended identity, exact denied resource/verb; do not widen RBAC reflexively |
| UI stays at old replica counts | Current observation timestamp vs persisted run, dispatcher claims, leases/deadlines, worker version |
| Pod Running but not ready | Deployment generation/conditions, Pod readiness, actual ready/available counts |
| Build exists but no deployment | Trusted push CI/artifact, webhook inbox/jobs, compatibility, preparation, policy/source bound, flags |
| `reconciling` / apply conflict | Resource owner/UID and journal; investigate before a bounded API retry |
| Missing/drifted observation | Live resource absence/UID/image against history; do not rewrite old releases |

For operational acceptance, capture real event → verified release → authorized
apply → readiness → health → durable success → correct UI; then separately approved
rollback and worker replay. Compare unrelated resource UIDs/spec hashes. Tests,
successful apply, or a running Pod cannot substitute for these observations.
