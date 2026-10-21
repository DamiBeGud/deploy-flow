# DeployFlow frontend

React, TypeScript, React Router, and Vite. Start with the
[root quickstart](../README.md#fresh-local-development); backend configuration and
release rules live in the [backend guide](../backend/README.md).

## Development and proxy

Requires Node.js `^20.19.0 || >=22.12.0` as declared by the locked Vite toolchain,
and npm. From `frontend/`:

~~~bash
npm ci
npm run dev
~~~

Vite binds to `127.0.0.1:5174` with a strict port and hot reload.
It proxies `/api`, including analysis SSE, to `http://127.0.0.1:8000`.
For another backend, set `BACKEND_URL` in an ignored `frontend/.env` using
[.env.example](.env.example), then restart Vite. This setting belongs to the
server-side proxy, not browser credentials.

The proxy avoids additional backend CORS configuration for same-origin requests.
`npm run build` produces static assets in `dist/`; the dev proxy is not included.
A hosted frontend needs its own API reverse proxy and configured OIDC callback.
This repository does not supply a complete hosted platform deployment.

## Authentication

The UI reads the backend authentication configuration:

- **Development:** Connect to local backend uses the configured local identity,
  without password or token verification. Never expose this API publicly.
- **OIDC:** Sign-in uses authorization code with S256 PKCE and `/auth/callback`.
  The backend verifies bearer tokens; deployment controls require authorized
  OIDC roles and execution availability. See
  [identity setup](../backend/ops/README.md#local-oidc).

Leaving the workspace clears its local session state. Browser authentication
state and credentials must not be copied into documentation, logs, or tests.

## User workflows

- Register an application, follow durable repository analysis, edit the prefilled
  Kubernetes recommendation, and review the generated infrastructure PR.
- Use the existing application's lifecycle page for later releases; another
  application registration or infrastructure PR is unnecessary for compatible
  source changes.
- Inspect source/baseline provenance, build verification/errors, pending release,
  image digest, policy, resource journal, rollout, health, and deployment history.
- Select a historical run explicitly; otherwise newer releases become the
  default. Historical rollout/health remains separate from the current cluster.
- Review manual rollback to a previous successful release. First-deployment
  cleanup controls require a separate enabled capability and exact inventory review.

The Applications dashboard combines registered applications and a separate
read-only Kubernetes workload table in the configured namespace. Applied resources
do not mean rollout-ready or application-healthy. Replica counts preserve zero;
unavailable, missing, drifted, and stale observations remain visible.
Current observation does not run a fresh application health probe.

Lifecycle reads do not overlap: active runs poll about every five seconds,
settled state every thirty seconds, and failed refresh requests back off up to sixty.
Hidden tabs pause observation polling. Analysis uses persisted SSE replay with
periodic status fallback. URL identifiers, persisted API state, and session replay
positions restore progress after reload. Conflicting assessment edits preserve
unsaved input until the user explicitly reloads; mutation retries retain their
idempotency keys.

Code is organized under `src/features/` with shared HTTP/contracts/UI helpers.
See [API schemas](http://127.0.0.1:8000/docs) while the backend is running.

## Verification

From `frontend/`:

~~~bash
npm run build
npm run lint
npm run test:e2e -- tests/phase7-lifecycle.spec.ts tests/oidc-callback.spec.ts
~~~

Playwright expects an already running frontend on `127.0.0.1:5174`; it does not
start servers. Those two browser suites mock backend APIs/token exchange, so they
need no real backend, Keycloak, or Kubernetes credentials. They cover lifecycle
transitions, failed/unavailable/stale/missing/zero observations, reload/history,
rollback/cleanup review, and OIDC callback exchange. Mock success is not live
deployment evidence.

`npm run test:e2e` also includes `tests/deployflow.spec.ts`. That suite requires
a dedicated backend with development identity, fake analysis, and a configured
planning image. It creates applications/runs and saves assessments/plans in its
database; some admission/PR interactions are mocked. Do not point it at the user's
operational stack. It is not a live-CD acceptance test. See the
[backend testing precautions](../backend/README.md#testing).
