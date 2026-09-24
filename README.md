# DeployFlow

Agentic infrastructure provisioning and application deployment platform.

```text
deploy-flow/
  frontend/    React + TypeScript + Vite
  backend/     FastAPI, PostgreSQL, Redis, and Dramatiq
```

## Local development

Terminal 1:

```bash
cd backend
docker compose up --build -d
docker compose watch --no-up api worker dispatcher
```

Terminal 2:

```bash
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5174** and click **Connect to local backend**.
API documentation: **http://127.0.0.1:8000/docs**.

React changes hot reload through Vite. Python source edits are synced into Docker
and automatically restart the API, worker, and dispatcher. PostgreSQL and Redis
remain running. Dependency/lockfile changes trigger image rebuilds. Keep the
watch command running; stopping it leaves services running but stops code sync.

The frontend supports repository registration, durable GitHub repository analysis,
optional DeepSeek-assisted review, and Phase 4 delivery planning. After analysis,
DeployFlow produces a complete Kubernetes recommendation from repository evidence
and Kubernetes policy defaults. You may override a recommendation when needed, then
generate and approve a reviewable infrastructure bundle.
Approval is an audit record only. This project does not create GitHub pull requests,
run CI, push images, or access a Kubernetes cluster. Authentication uses a local
development identity.

See the [frontend guide](frontend/README.md) and [backend guide](backend/README.md).
