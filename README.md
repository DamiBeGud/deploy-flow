# DeployFlow

DeployFlow analyzes a GitHub application repository, recommends editable deployment
configuration, and turns reviewed infrastructure into verified Kubernetes releases.
**Kubernetes is the only deployment target.** The supported generator and automatic
source-compatibility policy currently focus on Node web applications.

## Application lifecycle

1. Register a repository and branch, then analyze its source.
2. Review the prefilled configuration and override values where needed.
3. Generate, validate, and approve the bundle to open an infrastructure PR;
   review and merge that PR on GitHub.
4. After merge, independently verify CI and the immutable published image.
5. Prepare a release and deploy under explicit application policy and execution gates.
6. Observe resource application, rollout readiness, and application health separately.
7. Release compatible later commits using the approved infrastructure; manually
   roll back through a new release when a previous healthy release exists.

A published image is not a deployment. Automatic updates require working signed
webhooks, eligible CI, compatible source changes, and deployment authorization.

DeployFlow itself has a separate [platform upgrade flow](deploy/k3s/automation.md#7-upgrade-the-installed-platform-after-merging-main).
An optional [automatic main-to-cluster path](deploy/k3s/automation.md#8-automatic-platform-and-managed-application-deployment)
now supports publication, policy admission, image rollout, independent public
verification and durable completion. Its activation gates default off for new
installations. The selected hosted installation has automatic platform upgrades
enabled; each managed application's deployment policy remains separate.

## Fresh local development

Requires Docker with Compose and Node.js/npm compatible with the
[frontend lockfile](frontend/package-lock.json). Compose Watch needs Compose 2.32+.
These commands are for a **new development stack**, not an existing operational
installation: startup runs migrations and workers. For an existing stack, use the
[operational guide](backend/ops/README.md) before changing services or configuration.

From the repository root, start a simulated-analysis backend without GitHub or
Kubernetes credentials:

~~~bash
cd backend
test -e .env || cp .env.example .env
DF_ANALYSIS_MODE=fake docker compose up --build -d
~~~

From the repository root in a second terminal:

~~~bash
cd frontend
npm ci
npm run dev
~~~

Open http://127.0.0.1:5174 and choose **Connect to local backend**.
The API reference is http://127.0.0.1:8000/docs. Fake mode exercises analysis;
bundle planning also needs a valid image repository in the backend configuration.
Neither establishes GitHub verification or cluster deployment.
Keep the same analysis-mode override on subsequent backend recreations, or
deliberately configure the desired mode in the ignored `backend/.env`.

## Repository and documentation

| Location | Purpose |
| --- | --- |
| [Backend guide](backend/README.md) | Architecture, configuration, release rules, isolated tests |
| [Frontend guide](frontend/README.md) | UI development, authentication, lifecycle, browser tests |
| [Operational guide](backend/ops/README.md) | Access, activation, backups, recovery, approval boundaries |
| [Operational status and evidence](backend/ops/phase7-operational-closure.md) | Dated acceptance results, identifiers, blockers, next steps |
| [k3s hosting](deploy/k3s/README.md) | Hosted topology, installation tools, current evidence and operating gates |
| `backend/skills/` | Reviewed runtime instruction bundles; these are application assets |

## Maturity

On **2026-10-23**, hosted acceptance passed for automatic platform upgrades and
one internal Node fixture: reviewed baseline, automatic PR-merge and direct-push
updates, healthy durable completion, real signed-in UI, rollback, and safe replay.
See the [acceptance record](backend/ops/phase7-operational-closure.md) for exact
sources, images, policies and limits. Cleanup is disabled and unproven. Conservative
compatibility rules, single-node storage and policy expiry still apply; this is
not a general production-readiness claim.
The Compose stack and local Keycloak profile are development infrastructure;
production hosting is not supplied by the frontend dev server.
