# DeployFlow frontend

React, TypeScript and Vite. The frontend connects to the local Docker backend
through a same-origin Vite proxy: browser `/api/v1/*` requests are forwarded to
`http://127.0.0.1:8000/api/v1/*`. This also proxies server-sent events.

## Run locally

Start the backend first (see [backend instructions](../backend/README.md)):

```bash
cd deploy-flow/backend
docker compose up --build -d
docker compose watch --no-up api worker dispatcher
```

Keep the watch command running. In another terminal:

```bash
cd deploy-flow/frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5174** and choose **Connect to local backend**. Vite binds
to loopback and uses port 5174 explicitly, so it does not silently select another
port if one is occupied. Edit React/CSS files for Vite hot reload.

For a different backend port, create a local `.env` using `.env.example` and set
`BACKEND_URL`. Restart Vite after changing it. This is a server-side proxy setting;
it is not a browser credential. The proxy means no additional backend CORS origin
is needed. A production static deployment needs its own `/api` reverse proxy;
the development proxy is not embedded in `npm run build` output.

## Connected features

| Endpoint under `/api/v1` | UI |
| --- | --- |
| `GET /me` | Connect/restore the development workspace and check roles |
| `POST /applications` | Register a repository, name, and revision |
| `GET /applications` | Display the latest 100 registered applications |
| `GET /applications/{id}` | Open an application and resume its latest analysis |
| `POST /applications/{id}/analyses` | Start a durable analysis job |
| `GET /analyses/{id}` | Load persisted status and assessment |
| `GET /analyses/{id}/events` | Live progress and replay after reconnect |
| `PATCH /analyses/{id}/assessment` | Save port/health-path corrections with expected version |
| `POST /analyses/{id}/reanalyze` | Create a new analysis without replacing the old run |
| `POST /analyses/{id}/cancel` | Cancel queued or running work |
| `GET /environment-profiles` | Load the available local-k3s preview profile |
| `GET /applications/{id}/kubernetes-recommendation` | Load the prefilled AI/policy Kubernetes recommendation |
| `GET` / `PUT /applications/{id}/deployment-intent` | Load and version connectivity and workflow settings |
| `POST /applications/{id}/infrastructure-plans` | Generate a review-only bundle |
| `POST /infrastructure-plans/{id}/approve` | Record approval of the generated bundle |

The backend can run deterministic GitHub repository analysis when
`DF_ANALYSIS_PROVIDER=github`, or simulated fixture analysis when it is `fake`.
The UI displays the backend's analysis mode and pinned commit when available.
Installation-token failures are retried by the worker and expose a sanitized diagnostic if exhausted.
The dashboard lists applications; it does not claim to show live cluster workloads.

The connection button uses the backend's configured development identity; it is
not email/password or GitHub authentication. Leaving the workspace clears only
the browser's local workspace marker. Real authentication is required before
exposing this system remotely.

Application and run IDs are in the URL. Refreshing resumes the run. Event IDs are
saved per run in session storage, and EventSource reconnects automatically.
Periodic status reads provide a fallback during SSE interruptions. Terminal runs
close their stream. Connectivity and workflow changes are saved only through their
explicit save actions; moving forward also saves first. The review step shows the
saved delivery settings and any bundle generated during the current session.

Mutations use idempotency keys scoped by actor, action, target, and request body.
An uncertain failed request keeps its key across retries/reloads until a successful
response arrives. Duplicate clicks are blocked while a command is in flight.
Concurrent assessment edits report `409` and preserve your unsaved inputs; use
**Reload latest assessment** to explicitly discard them and load the newer version.

The delivery flow starts with a complete Kubernetes recommendation, derived from
repository evidence and Kubernetes policy defaults. Users only edit values they
wish to override. It supports service port, health path, replicas, resource requests
and limits, external exposure, detected non-secret environment values, and build
commands. The local profile currently has no approved connection or secret catalogue,
so those controls are accurately unavailable. Bundle generation and approval are
review-only: no GitHub pull request, CI execution, image push, or deployment occurs.

## Verification

```bash
npm run build
npm run lint
npm run test:e2e
```

The Playwright tests expect the frontend at 127.0.0.1:5174 and the real Docker
backend running. Tests create applications prefixed `e2e-` or `retry-` in the
local development database; they do not delete existing data or access Kubernetes.
Coverage includes create/analyze/reload, SSE replay, persistent assessment edits,
reanalysis, cancellation, optimistic conflicts, idempotent retries after a lost
response, backend connection errors, and the fixed sidebar.

New code is grouped into `features/applications`, `features/analysis`, and a
small shared layer for HTTP contracts, request handling, and UI primitives.
