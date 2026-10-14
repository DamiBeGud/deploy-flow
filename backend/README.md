# DeployFlow backend — analysis, reviewed PRs, and CI verification

FastAPI API, PostgreSQL state and event history, Redis/Dramatiq workers, and a
database-backed job dispatcher. Analysis resolves a GitHub revision through a
configured GitHub App and runs deterministic, read-only repository scanners.
An explicitly enabled agent mode can use DeepSeek with reviewed skills and a
bounded, read-only repository MCP server. Phase 4 stores versioned delivery
intent and generates a reviewable Dockerfile/workflow/Kubernetes bundle. Phase 5
checks Kubernetes admission with server-side dry-run. Phase 6 queues one GitHub
infrastructure PR after review and verifies the merged CI image digest. None of
these phases applies resources to Kubernetes; deployment is Phase 7.

The Kubernetes recommendation endpoint combines repository evidence with the fixed
local Kubernetes policy to return one complete editable intent. It does not ask the
user deployment questions or query a live cluster; resource and exposure defaults
are explicitly labelled as policy defaults.

## k3s readiness (pre-Phase 5)

The local profile now matches the existing cluster's `nginx` IngressClass and
`deployflow-test` namespace. See the [sanitized P0-01 readiness record](ops/k3s/P0-01-readiness.md)
for verified access boundaries and outstanding work. To repeat its read-only
checks from the repository root:

```bash
DF_K3S_SSH_TARGET=user@host bash backend/scripts/check_k3s_readiness.sh
```

For local development, `backend/scripts/k3s_tunnel.sh` can forward the API to
`https://127.0.0.1:16443` over SSH. Set `DF_K3S_SSH_TARGET`; optionally set
`DF_K3S_SSH_KEY` and `DF_K3S_LOCAL_PORT`. The tunnel alone is not an authenticated
Kubernetes client: separate short-lived reader and validator tokens and a
trusted CA are required, and must stay outside Git. Phase 5 adds opt-in reader
and validator MCP processes; the analysis worker receives neither credential.
The dashboard is limited to `deployflow-test`. Manifest validation accepts
only a stored bundle and uses server-side dry-run; it cannot deploy resources.
Regenerate old plans after the template/profile change; older plans are stale
and cannot be approved or sent to GitHub.

The `phase5` Compose profile is off by default. After separately approving the
exact server actions, provide absolute paths outside this repository for a
dedicated SSH key, pinned `known_hosts`, Kubernetes CA, and separate short-lived
ServiceAccount token files. Set `DF_K3S_SSH_TARGET`, `DF_K3S_SSH_KEY_PATH`,
`DF_K3S_KNOWN_HOSTS_PATH`, `DF_KUBE_CA_PATH`, `DF_KUBE_READER_TOKEN_PATH`, and
`DF_KUBE_VALIDATOR_TOKEN_PATH`. Set
`DF_KUBE_READER_MCP_URL=http://kube-reader-tunnel:8001/mcp` and
`DF_KUBE_VALIDATOR_MCP_URL=http://kube-validator-tunnel:8001/mcp`, then start
with `docker compose --profile phase5 up -d --build`. No Kubernetes credential
is mounted into the API, dispatcher, or analysis worker. Expired tokens must
be replaced and the respective MCP process restarted; there is no admin-token
fallback. The tunnel and MCP entrypoints copy `0600` host credentials into
private container tmpfs and drop to UID 10001 before serving requests. Do not
start this profile with `/dev/null` placeholder paths.

Installation-token requests retry transient GitHub `422`, `429`, and `5xx` responses with bounded
backoff. Permanent failures retain a short sanitized GitHub diagnostic in the failed event; tokens,
private keys, and source contents are never returned.

## GitHub App configuration

Set `DF_GITHUB_APP_ID`, `DF_GITHUB_INSTALLATION_ID`, and
`DF_GITHUB_PRIVATE_KEY_PATH` for an installed App. Keep the PEM outside this
repository. Analysis requests only a repository-scoped contents-read token;
the separate `github-worker` requests contents-write, pull-requests-write,
workflows-write, and actions-read for its repository. Update the App's
permissions and approve the installation update before trying the Phase 6 PR
action. Configure signed `pull_request` and `workflow_run` webhook events and
set the same high-entropy secret as `DF_GITHUB_WEBHOOK_SECRET` in the ignored
`.env` and GitHub App settings. Missing permissions or credentials block PR
creation; they do not fall back to a personal token.

The `webhook` Compose service serves **only** `/webhooks/github` on local port
`8002` (override with `WEBHOOK_PORT`). GitHub needs an HTTPS-reachable endpoint
that forwards only to this service. Never expose port `8000` or the development-
identity API through a public tunnel. The receiver verifies HMAC-SHA256 over
the raw request body and stores delivery IDs before queuing reconciliation.
Its process has no GitHub App private key, model key, or Kubernetes credential.

The generated workflow tests pull requests with read-only repository access.
After merge, its push run logs into GHCR using the repository's `GITHUB_TOKEN`,
publishes the image, and uploads `deployflow-build.json`. The backend accepts a
digest only when GitHub reports the expected merged PR, expected workflow and
successful run, exact approved merged files, matching artifact identifiers, and
an existing GHCR manifest at that digest. GHCR packages may initially be
private; make the fixture package public for anonymous verification and later
cluster pulls. No CI job has a Kubernetes credential. Phase 6 stops at a
verified build record and never deploys.

## Phase 7 release preparation (local foundation)

The internal release-preparation operation binds the verified CI image digest
to the reviewed Deployment, changing no other manifest field. It requires a
still-approved plan, a verified PR/build, and the merge commit to remain the
target branch head. It then runs a fresh server-side dry-run and stores the
exact final Kubernetes manifest bytes and digest. A reverted merge or failed
admission cannot produce a release candidate. This operation is not exposed as
a user action or invoked automatically yet. It has no deployer credential and
does not apply resources. Deployment, rollout, rollback, and their UI/API
controls remain future Phase 7 work.

## Optional DeepSeek agent mode

`DF_ANALYSIS_MODE=deterministic` is the default and does not call a model. To
explicitly opt in to agent analysis, add the following to the ignored
`backend/.env`, then recreate the worker:

```bash
DF_ANALYSIS_MODE=agent
DF_DEEPSEEK_API_KEY=your-key
DF_DEEPSEEK_MODEL=deepseek-flash
DF_DEEPSEEK_REASONING_EFFORT=high
docker compose up -d --build worker
```

The agent is stateless, uses only the project-owned read-only repository tools,
and records selected skill versions, evidence hashes, tool metadata, model
identity, and aggregate usage. It can finish as `needs_input` when a deployment
decision cannot be derived from the repository. Do not put a real key in
`.env.example`, source code, or Git.

## Start locally

Requirements: Docker with Docker Compose. Host Python is optional; the containers
use Python 3.12 and the committed `uv.lock`.

```bash
cd deploy-flow/backend
docker compose up --build -d
docker compose ps
```

Open [API documentation](http://127.0.0.1:8000/docs).
The API is published on **127.0.0.1:8000**. PostgreSQL and Redis have no host ports.
The migration service runs before the API, dispatcher, and worker start.

The existing `.env` can remain in place. It is ignored by Git and excluded from
the image. `.env.example` documents optional local overrides; don't overwrite
existing settings. Compose passes only its listed `DF_*` variables to the backend.
GitHub App credentials are required for deterministic/agent repository analysis; a
DeepSeek key is required only for explicitly selected agent mode. Phase 4's local
preview profile also needs `GHCR_REGISTRY` and `GHCR_IMAGE`; these are image names,
not registry credentials. The opt-in Phase 5 MCP processes use scoped Kubernetes
credentials; public GHCR images require no image-pull secret.

```bash
docker compose logs -f api dispatcher worker
docker compose stop
docker compose up -d
```

Stopping/restarting preserves the named PostgreSQL and Redis volumes. The local
database password in Compose is for this loopback-only development stack.

## Try one analysis

Create an application (returns `201`):

```bash
curl -s http://127.0.0.1:8000/api/v1/applications \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: fixture-create-1' \
  -d '{"name":"node-express-service","repository_url":"https://github.com/DamiBeGud/deployflow-node-express-fixture","revision":"main"}'
```

Replace `APPLICATION_ID` with the returned `id`. Queue an analysis (returns `202`):

```bash
curl -s http://127.0.0.1:8000/api/v1/applications/APPLICATION_ID/analyses \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: fixture-analysis-1' \
  -d '{}'
```

Repeat that exact command to get the same logical run. Reusing the same key for
a different revision returns `409`. Use a new key to intentionally create a new run.

Replace `RUN_ID` with the returned `run_id`:

```bash
curl -N http://127.0.0.1:8000/api/v1/analyses/RUN_ID/events
curl -s http://127.0.0.1:8000/api/v1/analyses/RUN_ID

# Reconnect after event 3, even after the run has completed:
curl -N http://127.0.0.1:8000/api/v1/analyses/RUN_ID/events \
  -H 'Last-Event-ID: 3'

# Cancel a queued/running analysis:
curl -s -X POST http://127.0.0.1:8000/api/v1/analyses/RUN_ID/cancel \
  -H 'Idempotency-Key: cancel-1'

# Correct the result without overwriting the original assessment:
curl -s -X PATCH http://127.0.0.1:8000/api/v1/analyses/RUN_ID/assessment \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: review-1' \
  -d '{"expected_version":1,"port":8080}'
```

Available application, analysis, and delivery-plan routes:

| Method | Path under `/api/v1` | Behavior |
| --- | --- | --- |
| GET | `/me` | Configured development identity |
| POST / GET | `/applications` | Create / list owned applications |
| GET | `/applications/{id}` | Application and latest run |
| POST | `/applications/{id}/analyses` | Queue repository analysis |
| GET | `/analyses/{id}` | Durable status and current assessment |
| GET | `/analyses/{id}/events` | SSE replay/live progress |
| PATCH | `/analyses/{id}/assessment` | New assessment version; optimistic concurrency |
| POST | `/analyses/{id}/reanalyze` | New independent run |
| POST | `/analyses/{id}/cancel` | Cancel queued/running work |
| GET | `/environment-profiles` | Available safe target profiles (local-k3s when configured) |
| GET | `/connections` | Approved connection catalogue (empty by default) |
| GET | `/applications/{id}/kubernetes-recommendation` | Complete evidence-first Kubernetes recommendation |
| GET / PUT | `/applications/{id}/deployment-intent` | Read / version saved connectivity and workflow settings |
| POST | `/applications/{id}/infrastructure-plans` | Generate a review-only infrastructure bundle |
| GET | `/infrastructure-plans/{id}` | Read a generated bundle and validation result |
| POST | `/infrastructure-plans/{id}/approve` | Record approval for the exact bundle digest without a GitHub write |
| POST | `/infrastructure-plans/{id}/validations` | Policy and Kubernetes dry-run for the stored bundle |
| POST | `/infrastructure-plans/{id}/pull-requests` | Approve a validated bundle and queue one GitHub PR |
| GET | `/pull-requests/{id}` | PR, CI, and verified image status |
| POST | `/pull-requests/{id}/refresh` | Queue GitHub reconciliation after a missed webhook |
| POST | `/pull-requests/{id}/retry` | Retry a failed PR creation after configuration is fixed |
| GET | `/services`, `/services/{id}` | Observed workloads in `deployflow-test` |
| GET | `/clusters/local-k3s/capabilities` | Configured target capabilities |

The dedicated webhook service exposes `POST /webhooks/github` (without the
`/api/v1` prefix or frontend identity). It requires GitHub's signature and
delivery ID, not the frontend `Idempotency-Key` header.

All mutations require `Idempotency-Key`. Keys are scoped to organization, actor,
and action (including target ID), with a canonical hash of validated request data.
Responses carry `X-Correlation-ID`; callers may supply a safe ID. Errors use
`application/problem+json` and never echo request values or exception details.

## Frontend contract

The frontend in `../frontend` now consumes all Phase 1 application/analysis routes.
Run `npm run dev` there and open `http://127.0.0.1:5174`. Vite proxies `/api` to this
backend on port 8000, including SSE. The browser uses the same origin, so CORS
changes are unnecessary. For clients that call the backend directly, CORS permits
`http://localhost:5174` and `http://127.0.0.1:5174` by default; override
`DF_CORS_ORIGINS` with a JSON array if required.

## Editor changes and Docker hot reload

```bash
docker compose up --build -d
docker compose watch --no-up api worker dispatcher
```

Keep the second command running. Compose Watch syncs `app/` edits into all three
Python services and restarts their processes. This requires Compose 2.32+ for
`initial_sync`; the local Docker Compose version is compatible. The application
source in the image is owned by its non-root runtime user so synchronization works.
PostgreSQL/Redis and their volumes are unaffected. An in-progress job may finish
during graceful shutdown or be recovered by its lease after restart; SSE clients
reconnect and replay saved progress.

`pyproject.toml`, `uv.lock`, or `Dockerfile` edits rebuild images automatically.
Update `uv.lock` along with dependency changes; builds use `uv sync --frozen`.
Compose configuration/environment changes require `docker compose up -d` again.
Migration edits are intentionally not applied by the watcher: rebuild the image,
then explicitly run `docker compose run --rm migrate` before restarting services.
Stopping Watch stops synchronization, not the containers. Use `docker compose stop`
to stop the local stack while preserving data.

Verified locally by changing a Python file and observing synchronization plus new
start times for API, worker, and dispatcher. [Compose Watch documentation](https://docs.docker.com/compose/how-tos/file-watch/)

## Progress stream contract

After creating an application, POST an empty object to its `/analyses` route,
save `run_id`, and open the returned `events_url` with `EventSource`. Listen for
named events (`analysis.queued`, `analysis.started`, `analysis.scanning`,
`analysis.agent_running`, `analysis.validating`, `analysis.retrying`,
`analysis.completed`, `analysis.needs_input`, `analysis.failed`, `analysis.cancelled`). On a terminal
event call `EventSource.close()` and GET the run for its final result.

Event IDs are positive integers **scoped to a run**, serialized under a run row
lock. EventSource automatically sends `Last-Event-ID` when it reconnects;
`?after=N` also supports a fresh connection after a page reload. SSE sends
heartbeats while idle and closes after all available terminal-run events have
been replayed. No database connection is held while waiting for the next poll.
Completed events remain available after Redis or API restarts.

## Durability and recovery

```mermaid
flowchart LR
    API[API transaction] --> DB[(PostgreSQL: run + event + pending job)]
    DB --> D[Dispatcher]
    D --> R[Redis / Dramatiq]
    R --> W[Worker claims run]
    W --> DB
    DB --> SSE[SSE replay to browser]
```

- State changes and pending jobs commit in one transaction. A uniqueness
  constraint serializes concurrent idempotency keys in PostgreSQL.
- The dispatcher republishes unclaimed runs every 10 seconds. Publishing before
  committing can deliver duplicates; workers atomically claim the run by ID.
- A worker owns a random lease token for up to five minutes. Every checkpoint and
  completion checks that token, status, and deadline. An expired worker cannot
  write over a recovered run. Future external operations also need their own
  idempotency/fencing; this foundation currently performs database-only work.
- The dispatcher recovers expired leases, with up to three total attempts and
  bounded backoff. Runs also have a one-hour overall queue deadline. Dramatiq
  retries are disabled because retry decisions live in PostgreSQL.
- Cancellation immediately records a terminal state and invalidates the lease.
  The fake worker notices it at the next checkpoint (normally within one second).
- Assessment versions and progress events are append-only through the API.
  Idempotency records and event history are retained without automatic expiry.

The dispatcher must be running for job recovery. `GET /health/ready` checks the
database/migration table; it deliberately remains ready when Redis is unavailable,
so queued work can still be accepted durably. Monitor dispatcher and worker logs
when queued runs are not progressing.

## Identity boundary

`IdentityProvider` is the interface for future authentication. The current provider
uses a single server-configured development identity. It ignores caller-supplied
actor/role headers. All resource reads and mutations enforce organization and
ownership; administrators may access other owners in their own organization.
Viewer identities can read, while developer/deployer/administrator roles can
mutate. This local provider has **no login or token verification**. Configuring
`DF_ENVIRONMENT=production` refuses startup until real authentication is implemented.
Do not publish or tunnel this development API.

## Verification

```bash
# PostgreSQL-backed tests, with an isolated deployflow_test database:
docker compose run --rm tests

# Real API -> dispatcher -> broker -> worker -> SSE journey:
python3 scripts/smoke.py

# Explicitly opt in first with DF_ANALYSIS_MODE=agent and a DeepSeek key, then
# verify the model-backed path (this may consume API credit):
python3 scripts/smoke.py --agent

# Also SIGKILL this local Compose worker mid-run, restart it, wait for
# lease recovery, restart this local Redis, and verify SSE replay:
python3 scripts/smoke.py --recovery

# Formatting / static checks in the built image:
docker compose run --rm --no-deps api ruff check app tests scripts migrations
docker compose run --rm --no-deps api ruff format --check app tests scripts migrations
docker compose run --rm --no-deps migrate alembic check
```

The recovery smoke test restarts only the `worker` and `redis` services in this
directory's Compose project and leaves its generated smoke application/run in the
development database. It requires the default nonzero fake analysis delay.
The pytest suite refuses database cleanup unless the database is exactly
`deployflow_test`. Tests cover duplicate requests/deliveries, rollback, lease
fencing, bounded retries/timeouts, Redis delivery loss, cancellation, immutable
assessment revisions, ownership/roles, and ordered SSE replay.

Implementation references: [Dramatiq guide](https://dramatiq.io/guide.html) and
[Dramatiq Redis broker reference](https://dramatiq.io/reference.html).
