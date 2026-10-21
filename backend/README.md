# DeployFlow backend

FastAPI serves the API; PostgreSQL stores applications, assessments, immutable
releases, resource journals, jobs, and events. Redis/Dramatiq transports work.
The database dispatcher and worker leases provide durable retry and recovery.

## Components

| Component | Responsibility |
| --- | --- |
| `api` | Authenticated application, analysis, planning, and deployment commands/reads |
| `worker` | Repository analysis; optional bounded DeepSeek assistance |
| `github-worker` | Reviewed infrastructure PR creation and independent CI/source verification |
| `dispatcher` | Publish committed jobs, recover expired leases, dispatch due retries |
| `webhook` | Dedicated signed GitHub notification receiver |
| `delivery-worker` | Release preparation and Kubernetes admission through the validator |
| `kube-reader` / `kube-validator` | Separate read-only observation and stored-bundle dry-run services |
| `kube-deployer` / `kube-cleaner` | Separate execution and optional deletion workers |
| `external-probe` | Credential-free external health checks for releases with Ingress |

A command commits its state, events, and pending work together. Workers claim
jobs under database locks and fence updates with lease tokens. Due delivery/
execution retries become `queued` only after successful publication, before
publication throttling advances. Future backoff and publication failures retain
retry eligibility; monitoring retries remain deadline-bounded. Duplicate broker
delivery does not authorize another mutation. Analysis SSE events are persisted
and replayable after reconnect; deployment events are ordered API reads.

Implementation: [dispatcher](app/dispatcher.py),
[GitHub integration](app/integrations/github/), [deployment state machines](app/deployments/).
The files in [skills/](skills/) are versioned runtime inputs loaded and hashed by
[SkillRegistry](app/skills/registry.py), not removable documentation.

## Development and configuration

For the separately prepared hosted installation, see the
[k3s hosting guide](../deploy/k3s/README.md). Hosted reader/validator/deployer Pods
use explicit `DF_KUBE_ACCESS_MODE=incluster` and rotating projected identities;
the local SSH client remains the default. Hosting is not operationally accepted.

Use the [root quickstart](../README.md#fresh-local-development) for a new stack.
Containers use Python 3.12 and `uv sync --frozen` with [uv.lock](uv.lock).
The `migrate` service upgrades the database before backend processes start.
The API binds to loopback port 8000; PostgreSQL and Redis have no published host
ports. Their named volumes survive service recreation.

Use [.env.example](.env.example) for names and [compose.yaml](compose.yaml) for
what actually reaches each process. Compose does not forward arbitrary host
`DF_*` settings. Preserve an existing ignored `.env`; keep private key/token files
outside Git and outside the build context.

| Configuration | Behavior |
| --- | --- |
| `DF_ANALYSIS_MODE` | `fake` for simulation, `deterministic` by default, `agent` for explicitly enabled model calls |
| `DF_GITHUB_APP_ID`, `DF_GITHUB_INSTALLATION_ID`, `DF_GITHUB_PRIVATE_KEY_PATH` | Installed GitHub App; key path must be absolute and private |
| `GHCR_REGISTRY`, `GHCR_IMAGE` | Compose maps these to backend `DF_GHCR_*` settings; image names, not credentials |
| `DF_DEEPSEEK_API_KEY`, `DF_DEEPSEEK_MODEL`, `DF_DEEPSEEK_REASONING_EFFORT` | Agent-only credentials/options; defaults are `deepseek-flash` and `high` |
| `DF_BACKEND_IMAGE` | Select prepared backend image; default `deployflow-backend:local` |
| `DF_K3S_TUNNEL_IMAGE` | Select tunnel image; default `deployflow-k3s-tunnel:local` |
| `DF_DEPLOYMENT_EXECUTION_ENABLED` / `DF_DEPLOYMENT_CLEANUP_ENABLED` | Both false by default; changing them can activate work |

Deterministic and agent modes need the installed App's repository access.
Before generating bundles, replace the example `GHCR_IMAGE` placeholders with
the intended lowercase image repository, such as `ghcr.io/example/fixture`.
Live CI and pulls require the real approved repository, not that example.
Agent mode sends selected repository excerpts to DeepSeek, can incur charges,
and uses only reviewed skills and bounded read-only tools. It records provenance
and usage and may return `needs_input`. `DF_ANALYSIS_PROVIDER` is a legacy bridge;
new configuration should use `DF_ANALYSIS_MODE`.

For a disposable development stack only, from `backend/`:

~~~bash
docker compose watch --no-up api worker dispatcher
~~~

Watch syncs/restarts those processes and rebuilds on Dockerfile/dependency-lock
changes. Stopping Watch leaves containers running. It does not activate migration
edits. Do not use Watch or rebuild/recreate an existing operational stack without
the [activation review](ops/README.md#activation-and-database-recovery).

## Authentication and API contracts

Development identity is local-only, performs no login/token verification, and
ignores caller-supplied role/actor headers. It cannot deploy. OIDC validates signed
access tokens and is required for execution and production mode. Organization,
ownership, and role checks still apply. The public browser client uses
authorization-code flow with S256 PKCE.

Configure `DF_AUTH_MODE=oidc` and `DF_OIDC_ISSUER`, `DF_OIDC_AUDIENCE`,
`DF_OIDC_JWKS_URL`, `DF_OIDC_AUTHORIZATION_ENDPOINT`, `DF_OIDC_TOKEN_ENDPOINT`,
and `DF_OIDC_CLIENT_ID`. Register `<frontend-origin>/auth/callback`, token-endpoint
CORS for that origin, and claims `organization_id` and `roles`. Deployment actions
require `deployer` or `administrator`. See
[local Keycloak setup](ops/README.md#local-oidc) for the development-only HTTP exception.

The running [OpenAPI reference](http://127.0.0.1:8000/docs) and
[API routes](app/api/) define request/response schemas. Mutating API commands
require an `Idempotency-Key` scoped to actor, organization, action/target, and
request body. Errors use problem JSON with a correlation ID. The webhook has
its own signature/delivery-ID contract.

Vite proxies browser `/api` traffic, including analysis SSE. Direct clients need
appropriate `DF_CORS_ORIGINS`. The example `.env` lists port 5174; without that
override, the current backend/Compose defaults still list 5173. The Vite proxy
avoids this mismatch for same-origin UI requests.

## GitHub and build verification

Install a GitHub App on the intended repository. Analysis requests a
repository-scoped contents-read token. PR creation by `github-worker` also needs
contents-write, pull-requests-write, workflows-write, and actions-read permissions.
Approve installation permission updates explicitly; there is no personal-token
fallback.

Subscribe to `pull_request` and `workflow_run` events. The receiver processes
completed workflow notifications; direct `push` webhooks are currently ignored.
A direct push or PR merge is eligible through its completed **push workflow run**.
Configure matching private `DF_GITHUB_WEBHOOK_SECRET` and GitHub App secret.
Expose only HTTPS `/webhooks/github` to the dedicated loopback receiver on port
8002 (`WEBHOOK_PORT`), never the development API. See
[webhook operation](ops/README.md#webhooks-and-automatic-releases).

Notifications trigger independent GitHub/registry queries, not trusted build
proof. Bootstrap verification checks the reviewed PR, merged source, approved
file bytes/digest, expected workflow, and successful CI. The generated PR job
tests/builds without deployment; the push job publishes to GHCR and uploads
`deployflow-build.json` in artifact `deployflow-build`. Verification binds
repository, branch, workflow path, push event, run/attempt, source SHA, artifact
identity, allowed image repository, and immutable registry digest. CI has no
Kubernetes credential. The implemented anonymous registry/pull path requires
public images; a private-registry authentication flow is not supplied here.

## Releases and compatible source updates

The approved infrastructure merge and the application build revision are separate:
`InfrastructurePullRequest.merged_sha` / `VerifiedBuild.merged_sha` retain the
baseline; `source_sha` identifies the actual source used for the build/release.
Provenance includes run/attempt, artifact, digest, assessment/approval, compatibility
policy, and changed-file hashes. Existing historical source columns are backfilled
from their verified merge SHA.

The generator produces one editable Kubernetes recommendation from repository
evidence and policy defaults. Saved intent, approved bundle, and admission results
are versioned; changed intent, template, profile, or approval can invalidate reuse.
The current Node template `node-web-v4` uses numeric `USER 1000:1000` with a
base-image UID/GID check and Kubernetes nonroot enforcement. An old incompatible
image needs regenerated/reviewed infrastructure and a new verified release.

Preparation binds only the verified image into reviewed manifests, then stores
immutable final bytes/digest after fresh admission. The delivery worker has no
deployer credential. It may prepare releases while execution/policy is disabled.
Generated ConfigMap data has a digest on the Pod template so configuration-only
changes roll new Pods. The executor rejects a changed configuration without the
corresponding template change (`configuration_rollout_missing`); approved old
release bytes are never rewritten to add it.

Source reuse uses [node-source-compatibility-v1](app/integrations/github/compatibility.py):
documentation/tests and narrowly checked JavaScript JSON-response data changes
under `src/` can qualify. Approved Dockerfile/workflow/manifests must stay identical.
Dependency/lockfile, runtime/startup, import, port, health, environment/configuration,
unknown-file, and ambiguous executable changes require reassessment/review.
This is not generic JavaScript equivalence or multi-runtime support.
Useful errors include `approved_infrastructure_changed` and
`deployment_requirements_need_review`.

Ordering is current branch head and forward ancestry, serialized per application.
Each admitted source is used once. New verified builds supersede older unstarted
work; the head is rechecked before apply. Active apply or unresolved reconciliation
blocks a later deployment. Fully journaled runs can finish read-only observations
after branch movement. Replayed notifications/reruns do not redeploy an already
admitted source after rollback; a genuinely new forward commit remains eligible.

## Authorization, execution, and current observations

Execution requires OIDC, the execution flag, a passing preflight, an enabled
application/environment policy, and the separate executor. Policy is checked again
immediately before mutation. Enabling a flag alone does not execute existing
`waiting_for_executor` runs; an operator explicitly selects a prepared run.
Future eligible releases can queue automatically after preflight.

The policy's optional `allowed_source_sha` bounds standard releases to one exact
commit. Omitting the field preserves the bound; explicit `null` removes it and
authorizes future compatible commits. Removing a bound needs a separate policy
decision. Rollback is separately authorized against its stored target.
[Operational procedures](ops/README.md) cover review, recovery, rollback, and cleanup.

Current `GET /api/v1/applications/{id}/observation` reads through the reader
identity, separately from immutable deployment history. It checks Deployment/
Service ownership and UIDs, image, generation, and rollout counts; it does not
inspect ConfigMap data or perform a new health probe. Missing, drifted, unavailable,
and pending remain distinct. Unavailable observations never become a confident zero.
Snapshot reads are coalesced, cached for five seconds (errors fifteen), and
bounded to twelve seconds.

The lifecycle page combines release status, current observation, persisted rollout/
health, ordered events, and history. Numeric zero remains valid; old untimestamped
results are marked historical/stale. See the [frontend guide](../frontend/README.md)
for polling and reload behavior.

## Testing

Prefer the isolated runner: it creates a disposable PostgreSQL container/network,
mounts source/test directories, passes no normal Compose environment or live
GitHub/Kubernetes credentials, and removes only its own resources. Database-reset
tests refuse databases other than `deployflow_test`. Select a reviewed local image
built from the current dependency lock; do not use an image containing credentials.

From `backend/`; these build/test commands do not recreate existing services:

~~~bash
docker build -t deployflow-backend:docs-test .
DF_TEST_IMAGE=deployflow-backend:docs-test sh scripts/test_phase7_isolated.sh
DF_TEST_IMAGE=deployflow-backend:docs-test sh scripts/test_phase7_isolated.sh   ruff check app tests scripts migrations
~~~

The historical test results and known lint issue are in the
[acceptance record](ops/phase7-operational-closure.md#recorded-tests).
The `tests` Compose profile uses the normal project's PostgreSQL server with a
separate test database; it is not the same isolation as this runner.

`scripts/smoke.py` writes applications/runs to its configured backend.
`--agent` can spend model credit; `--recovery` SIGKILLs a worker and restarts Redis.
These need a disposable stack or separately scoped operational approval.
Frontend test requirements are documented in the [frontend guide](../frontend/README.md#verification).
