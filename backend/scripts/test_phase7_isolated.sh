#!/bin/sh
# No Compose .env, live database network, GitHub key, or Kubernetes credential.
set -eu
phase7_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
phase7_image=${DF_TEST_IMAGE:?Set DF_TEST_IMAGE to the reviewed backend image}
phase7_network="deployflow-phase7-test-$$"
phase7_postgres="$phase7_network-postgres"
cleanup() {
  docker rm -fv "$phase7_postgres" >/dev/null 2>&1 || true
  docker network rm "$phase7_network" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM
docker image inspect "$phase7_image" >/dev/null
docker network create "$phase7_network" >/dev/null
docker run -d --name "$phase7_postgres" --network "$phase7_network" \
  -e POSTGRES_HOST_AUTH_METHOD=trust postgres:17-alpine >/dev/null
phase7_attempt=0
until docker exec "$phase7_postgres" pg_isready -U postgres >/dev/null 2>&1; do
  phase7_attempt=$((phase7_attempt + 1))
  [ "$phase7_attempt" -lt 30 ] || exit 1
  sleep 1
done
if [ "$#" -eq 0 ]; then set -- pytest -q; fi
docker run --rm --network "$phase7_network" \
  -e "DF_DATABASE_URL=postgresql+psycopg://postgres@$phase7_postgres:5432/deployflow_test" \
  -e DF_AUTH_MODE=development -e DF_ANALYSIS_MODE=fake -e DF_FAKE_STEP_SECONDS=0 \
  -e DF_DEPLOYMENT_EXECUTION_ENABLED=false -e DF_DEPLOYMENT_CLEANUP_ENABLED=false \
  -e DF_GHCR_REGISTRY=ghcr.io -e DF_GHCR_IMAGE=ghcr.io/example/fixture \
  -v "$phase7_root/app:/srv/backend/app:ro" -v "$phase7_root/tests:/srv/backend/tests:ro" \
  -v "$phase7_root/migrations:/srv/backend/migrations:ro" -v "$phase7_root/scripts:/srv/backend/scripts:ro" \
  -v "$phase7_root/mcp_servers:/srv/backend/mcp_servers:ro" "$phase7_image" "$@"
