"""Render reviewable hosting resources; never contact Docker, GitHub, or Kubernetes.

Secrets are provisioned separately. Each phase is deliberately rendered alone so
database initialization, workload activation, and the existing-host switch can
receive separate operational approval.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "deployflow-system"
TARGET = "deployflow-test"
HOST = "deployflow.damibegud.cloud"
REVISION = "e4b92f7a6103"
ISSUER = f"https://{HOST}/identity/realms/deployflow-local"
IMAGE_REPOSITORIES = {
    "backend": "ghcr.io/damibegud/deployflow-backend",
    "frontend": "ghcr.io/damibegud/deployflow-frontend",
    "keycloak": "ghcr.io/damibegud/deployflow-keycloak",
}
PULL_SECRET = "deployflow-ghcr"
# Registry indexes verified read-only; these are the images rehearsed locally.
POSTGRES_IMAGE = "docker.io/library/postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
REDIS_IMAGE = "docker.io/library/redis@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
RUNTIME = ["api", "worker", "github-worker", "dispatcher", "webhook", "delivery-worker", "kube-deployer"]


def resource(kind, name, *, namespace=NAMESPACE, api_version="v1", **body):
    metadata = {"name": name, "labels": {"app.kubernetes.io/part-of": "deployflow-platform"}}
    if namespace:
        metadata["namespace"] = namespace
    return {"apiVersion": api_version, "kind": kind, "metadata": metadata, **body}


def secret(name, key):
    return {"valueFrom": {"secretKeyRef": {"name": name, "key": key}}}


def env(name, value=None, *, secret_name=None, key=None):
    return {"name": name, **(secret(secret_name, key) if secret_name else {"value": str(value)})}


def security(uid=10001, gid=None):
    return {"runAsNonRoot": True, "runAsUser": uid, "runAsGroup": gid or uid,
            "fsGroup": gid or uid, "seccompProfile": {"type": "RuntimeDefault"}}


def container(name, image, *, uid=10001, memory="256Mi", cpu="500m", port=None, command=None, environment=None):
    value = {
        "name": name, "image": image, "imagePullPolicy": "IfNotPresent",
        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]}},
        "resources": {"requests": {"cpu": "25m", "memory": "64Mi"},
                      "limits": {"cpu": cpu, "memory": memory}},
        "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
    }
    if port:
        value["ports"] = [{"name": "http", "containerPort": port}]
    if command:
        value["command"] = command
    if environment:
        value["env"] = environment
    return value


def pod(name, main, *, uid=10001, gid=None, identity=False):
    return {
        "metadata": {"labels": {"app.kubernetes.io/name": name, "app.kubernetes.io/part-of": "deployflow-platform"}},
        "spec": {"serviceAccountName": name if identity else "deployflow-runtime",
                 "imagePullSecrets": [{"name": PULL_SECRET}],
                 "automountServiceAccountToken": identity, "securityContext": security(uid, gid),
                 "terminationGracePeriodSeconds": 45,
                 "containers": [main], "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}}]},
    }


def deployment(name, template):
    return resource("Deployment", name, api_version="apps/v1", spec={
        "replicas": 1, "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app.kubernetes.io/name": name}}, "template": template,
    })


def service(name, port, target=None):
    return resource("Service", name, spec={"selector": {"app.kubernetes.io/name": name},
                    "ports": [{"name": "http" if port in {8000, 8001, 8080} else "tcp",
                               "port": port, "targetPort": target or port}]})


def postgres(name, secret_name, database, storage):
    main = container(name, POSTGRES_IMAGE, uid=70, memory="512Mi", port=5432, environment=[
        env("POSTGRES_DB", database), env("POSTGRES_USER", database),
        env("POSTGRES_PASSWORD", secret_name=secret_name, key="password"),
        env("PGDATA", "/var/lib/postgresql/data/pgdata"),
    ])
    main["ports"][0]["name"] = "postgres"
    main["volumeMounts"] += [{"name": "data", "mountPath": "/var/lib/postgresql/data"},
                             {"name": "run", "mountPath": "/var/run/postgresql"}]
    main["readinessProbe"] = {"exec": {"command": ["pg_isready", "-U", database, "-d", database]}, "periodSeconds": 5}
    template = pod(name, main, uid=70)
    template["spec"]["volumes"].append({"name": "run", "emptyDir": {"sizeLimit": "16Mi"}})
    return resource("StatefulSet", name, api_version="apps/v1", spec={
        "serviceName": name, "replicas": 1,
        "selector": {"matchLabels": {"app.kubernetes.io/name": name}}, "template": template,
        "volumeClaimTemplates": [resource("PersistentVolumeClaim", "data", namespace=None, spec={
            "accessModes": ["ReadWriteOnce"], "storageClassName": "local-path",
            "resources": {"requests": {"storage": storage}},
        })],
    })


def policy(name, callers, port):
    peers = [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": caller}}} for caller in callers]
    if name in {"frontend", "webhook", "keycloak"}:
        peers.append({"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}},
                      "podSelector": {"matchLabels": {"app.kubernetes.io/component": "controller"}}})
    return resource("NetworkPolicy", f"allow-{name}", api_version="networking.k8s.io/v1", spec={
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": name}}, "policyTypes": ["Ingress"],
        "ingress": [{"from": peers, "ports": [{"protocol": "TCP", "port": port}]}],
    })


def fresh_realm():
    # Reuse the reviewed client/claims configuration, never old user identities.
    realm = json.loads((ROOT / "backend/ops/local-oidc/deployflow-local-realm.json").read_text())
    realm["sslRequired"] = "external"
    browser = next(value for value in realm["clients"] if value["clientId"] == "deployflow-local-ui")
    browser["redirectUris"] = [f"https://{HOST}/auth/callback"]
    browser["webOrigins"] = [f"https://{HOST}"]
    credential = realm["users"][0]["credentials"][0]
    credential["value"] = "${DF_HOSTED_OIDC_USER_PASSWORD}"
    credential["temporary"] = True
    realm["users"][0]["requiredActions"] = ["UPDATE_PASSWORD"]
    return realm


def foundation():
    ns = resource("Namespace", NAMESPACE, namespace=None)
    ns["metadata"]["labels"].update({
        "pod-security.kubernetes.io/enforce": "restricted",
        "pod-security.kubernetes.io/enforce-version": "v1.36",
        "pod-security.kubernetes.io/audit": "restricted", "pod-security.kubernetes.io/warn": "restricted",
    })
    result = [ns]
    for name in ("deployflow-runtime", "kube-reader", "kube-validator", "kube-deployer"):
        result.append(resource("ServiceAccount", name, automountServiceAccountToken=False))
    # Bind existing narrow Roles; neither their rules nor existing subjects change.
    for role in ("reader", "validator", "deployer"):
        result.append(resource("RoleBinding", f"deployflow-platform-{role}", namespace=TARGET,
                               api_version="rbac.authorization.k8s.io/v1",
                               subjects=[{"kind": "ServiceAccount", "name": f"kube-{role}", "namespace": NAMESPACE}],
                               roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": f"deployflow-{role}"}))
    config = {
        "DF_ENVIRONMENT": "production", "DF_AUTH_MODE": "oidc", "DF_ANALYSIS_MODE": "deterministic",
        "DF_OIDC_ISSUER": ISSUER, "DF_OIDC_AUDIENCE": "deployflow-api", "DF_OIDC_CLIENT_ID": "deployflow-local-ui",
        "DF_OIDC_JWKS_URL": f"{ISSUER}/protocol/openid-connect/certs",
        "DF_OIDC_AUTHORIZATION_ENDPOINT": f"{ISSUER}/protocol/openid-connect/auth",
        "DF_OIDC_TOKEN_ENDPOINT": f"{ISSUER}/protocol/openid-connect/token",
        "DF_CORS_ORIGINS": json.dumps([f"https://{HOST}"]),
        "DF_KUBE_READER_MCP_URL": "http://kube-reader:8001/mcp",
        "DF_KUBE_VALIDATOR_MCP_URL": "http://kube-validator:8001/mcp",
        "DF_GHCR_REGISTRY": "ghcr.io", "DF_GHCR_IMAGE": "ghcr.io/damibegud/deployflow-node-express-fixture",
        "DF_GITHUB_APP_ID": "4957845", "DF_GITHUB_INSTALLATION_ID": "162024583",
        "DF_DEPLOYMENT_EXECUTION_ENABLED": "false", "DF_DEPLOYMENT_CLEANUP_ENABLED": "false",
    }
    result.append(resource("ConfigMap", "deployflow-config", data=config))
    result.append(resource("ConfigMap", "deployflow-realm", data={
        "deployflow-local-realm.json": json.dumps(fresh_realm(), indent=2),
    }))
    result += [service("postgres", 5432), service("identity-postgres", 5432), service("redis", 6379)]
    result += [service(name, port) for name, port in (("api", 8000), ("frontend", 8080), ("webhook", 8000),
               ("keycloak", 8080), ("kube-reader", 8001), ("kube-validator", 8001), ("external-probe", 8000))]
    result.append(resource("NetworkPolicy", "default-deny-ingress", api_version="networking.k8s.io/v1",
                           spec={"podSelector": {}, "policyTypes": ["Ingress"]}))
    for name, callers, port in (
        ("postgres", RUNTIME + ["migrate"], 5432),
        ("identity-postgres", ["keycloak", "identity-setup", "identity-import"], 5432),
        ("redis", RUNTIME, 6379), ("api", ["frontend"], 8000), ("frontend", [], 8080),
        ("webhook", [], 8000), ("keycloak", [], 8080), ("kube-reader", ["api"], 8001),
        ("kube-validator", ["api", "delivery-worker"], 8001), ("external-probe", ["kube-deployer"], 8000),
    ):
        result.append(policy(name, callers, port))
    result += [postgres("postgres", "deployflow-postgres", "deployflow", "5Gi"),
               postgres("identity-postgres", "deployflow-identity-postgres", "keycloak", "2Gi")]
    main = container("redis", REDIS_IMAGE, uid=999, memory="256Mi", port=6379,
                     command=["sh", "-ec", 'exec redis-server --appendonly yes --requirepass "$REDIS_PASSWORD"'],
                     environment=[env("REDIS_PASSWORD", secret_name="deployflow-redis", key="password")])
    main["ports"][0]["name"] = "redis"
    main["volumeMounts"].append({"name": "data", "mountPath": "/data"})
    main["readinessProbe"] = {"exec": {"command": ["sh", "-ec", 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping']}, "periodSeconds": 5}
    result.append(resource("StatefulSet", "redis", api_version="apps/v1", spec={
        "serviceName": "redis", "replicas": 1,
        "selector": {"matchLabels": {"app.kubernetes.io/name": "redis"}}, "template": pod("redis", main, uid=999, gid=1000),
        "volumeClaimTemplates": [resource("PersistentVolumeClaim", "data", namespace=None, spec={
            "accessModes": ["ReadWriteOnce"], "storageClassName": "local-path",
            "resources": {"requests": {"storage": "1Gi"}},
        })],
    }))
    return result


def runtime(name, image, command, *, port=None, identity=False, github=False, receiver=False):
    main = container(name, image, command=command, port=port, memory="512Mi")
    main["envFrom"] = [{"configMapRef": {"name": "deployflow-config"}}]
    if name not in {"kube-reader", "kube-validator"}:
        main["envFrom"].append({"secretRef": {"name": "deployflow-runtime"}})
    main["env"] = []
    template = pod(name, main, identity=identity)
    if identity:
        main["env"] += [env("DF_KUBE_ACCESS_MODE", "incluster"), env("DF_KUBE_MCP_ROLE", name.removeprefix("kube-"))]
    if github:
        main["env"].append(env("DF_GITHUB_PRIVATE_KEY_PATH", "/run/github/private.pem"))
        main["volumeMounts"].append({"name": "github", "mountPath": "/run/github", "readOnly": True})
        template["spec"]["volumes"].append({"name": "github", "secret": {"secretName": "deployflow-github-key", "defaultMode": 0o440}})
    elif name == "api":
        # The PR route checks worker configuration before enqueueing. It never
        # opens this path; only the workers above receive the actual private key.
        main["env"].append(env("DF_GITHUB_PRIVATE_KEY_PATH", "/run/github/private.pem"))
    if receiver:
        main["env"].append(env("DF_GITHUB_WEBHOOK_SECRET", secret_name="deployflow-webhook", key="secret"))
    # Pod init retries the read; it never migrates or mutates database state.
    check = ("import os; from sqlalchemy import create_engine,text; "
             "e=create_engine(os.environ['DF_DATABASE_URL']); "
             "c=e.connect(); assert c.scalar(text('SELECT version_num FROM alembic_version')) == '" + REVISION + "'")
    init = container("schema-ready", image, command=["python", "-c", check])
    init["envFrom"] = [{"secretRef": {"name": "deployflow-runtime"}}]
    if name not in {"kube-reader", "kube-validator"}:
        template["spec"]["initContainers"] = [init]
    if port:
        probe = {"tcpSocket": {"port": port}, "periodSeconds": 5}
        main["readinessProbe"] = probe
        main["livenessProbe"] = {**copy.deepcopy(probe), "initialDelaySeconds": 30}
    return deployment(name, template)


def workloads(images):
    backend = images["backend"]
    result = []
    for name, command, port, identity, github, receiver in (
        ("api", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"], 8000, False, False, True),
        ("webhook", ["uvicorn", "app.webhook_main:app", "--host", "0.0.0.0", "--port", "8000"], 8000, False, False, True),
        ("dispatcher", ["python", "-m", "app.dispatcher"], None, False, False, False),
        ("kube-reader", ["python", "-m", "mcp_servers.kubernetes_server"], 8001, True, False, False),
        ("kube-validator", ["python", "-m", "mcp_servers.kubernetes_server"], 8001, True, False, False),
        ("worker", ["dramatiq", "app.worker", "--processes", "1", "--threads", "4", "--queues", "analysis"], None, False, True, False),
        ("github-worker", ["dramatiq", "app.worker", "--processes", "1", "--threads", "2", "--queues", "github"], None, False, True, False),
        ("delivery-worker", ["dramatiq", "app.worker", "--processes", "1", "--threads", "2", "--queues", "delivery"], None, False, True, False),
        ("kube-deployer", ["dramatiq", "app.worker", "--processes", "1", "--threads", "1", "--queues", "execution"], None, True, True, False),
    ):
        result.append(runtime(name, backend, command, port=port, identity=identity, github=github, receiver=receiver))
    api = result[0]["spec"]["template"]["spec"]["containers"][0]
    api["readinessProbe"] = {"httpGet": {"path": "/health/ready", "port": 8000}, "periodSeconds": 5}
    api["livenessProbe"] = {"httpGet": {"path": "/health/live", "port": 8000}, "periodSeconds": 10}
    front = container("frontend", images["frontend"], uid=101, port=8080, memory="128Mi")
    front["readinessProbe"] = {"httpGet": {"path": "/healthz", "port": 8080}, "periodSeconds": 5}
    result.append(deployment("frontend", pod("frontend", front, uid=101)))
    probe = container("external-probe", backend, port=8000,
                      command=["uvicorn", "app.external_probe:app", "--host", "0.0.0.0", "--port", "8000"])
    result.append(deployment("external-probe", pod("external-probe", probe)))
    keycloak = container("keycloak", images["keycloak"], uid=1000, memory="2Gi", cpu="1", port=8080,
                         command=["/opt/keycloak/bin/kc.sh", "start", "--optimized"], environment=[
        env("KC_HOSTNAME", f"https://{HOST}/identity"), env("KC_HTTP_RELATIVE_PATH", "/identity"),
        env("KC_HTTP_ENABLED", "true"), env("KC_PROXY_HEADERS", "xforwarded"), env("KC_DB", "postgres"),
        env("KC_DB_URL", "jdbc:postgresql://identity-postgres:5432/keycloak"), env("KC_DB_USERNAME", "keycloak"),
        env("KC_DB_PASSWORD", secret_name="deployflow-identity-postgres", key="password"),
        env("KC_HEALTH_ENABLED", "true"), env("KC_CACHE", "local"),
        env("KC_HTTP_MANAGEMENT_RELATIVE_PATH", "/"),
    ])
    keycloak["resources"]["requests"]["memory"] = "768Mi"
    keycloak["volumeMounts"].append({"name": "keycloak-data", "mountPath": "/opt/keycloak/data"})
    keycloak["startupProbe"] = {"httpGet": {"path": "/health/started", "port": 9000}, "periodSeconds": 5, "failureThreshold": 60}
    keycloak["readinessProbe"] = {"httpGet": {"path": "/health/ready", "port": 9000}, "periodSeconds": 5}
    keycloak["livenessProbe"] = {"httpGet": {"path": "/health/live", "port": 9000}, "periodSeconds": 10}
    template = pod("keycloak", keycloak, uid=1000)
    template["spec"]["volumes"] += [
        {"name": "keycloak-data", "emptyDir": {"sizeLimit": "256Mi"}},
    ]
    result.append(deployment("keycloak", template))
    return result


def migration(images):
    main = container("migrate", images["backend"], command=["alembic", "upgrade", REVISION], memory="512Mi")
    main["envFrom"] = [{"secretRef": {"name": "deployflow-runtime"}}]
    template = pod("migrate", main)
    template["spec"]["restartPolicy"] = "Never"
    return [resource("Job", "deployflow-migrate-c3a71d9e4208", api_version="batch/v1",
                     spec={"backoffLimit": 3, "activeDeadlineSeconds": 300, "template": template})]


def identity_import(images):
    # One explicit offline import before ANY Keycloak server is started against
    # the fresh identity database. Do not auto-import an empty template on boot.
    main = container("identity-import", images["keycloak"], uid=1000, memory="2Gi", cpu="1",
                     command=["/opt/keycloak/bin/kc.sh", "import", "--optimized", "--dir", "/run/identity-import", "--override=false"],
                     environment=[env("KC_DB_URL", "jdbc:postgresql://identity-postgres:5432/keycloak"),
                                  env("KC_DB_USERNAME", "keycloak"),
                                  env("KC_DB_PASSWORD", secret_name="deployflow-identity-postgres", key="password")])
    main["resources"]["requests"]["memory"] = "768Mi"
    main["volumeMounts"] += [{"name": "identity-export", "mountPath": "/run/identity-import", "readOnly": True},
                              {"name": "keycloak-data", "mountPath": "/opt/keycloak/data"}]
    template = pod("identity-import", main, uid=1000)
    template["spec"]["restartPolicy"] = "Never"
    template["spec"]["volumes"] += [
        {"name": "identity-export", "secret": {"secretName": "deployflow-identity-import", "defaultMode": 0o440}},
        {"name": "keycloak-data", "emptyDir": {"sizeLimit": "256Mi"}},
    ]
    return [resource("Job", "deployflow-identity-import", api_version="batch/v1",
                     spec={"backoffLimit": 0, "activeDeadlineSeconds": 600, "template": template})]


def identity_setup(images):
    # Run exactly once against an empty identity database, before the server.
    database = [env("KC_DB_URL", "jdbc:postgresql://identity-postgres:5432/keycloak"),
                env("KC_DB_USERNAME", "keycloak"),
                env("KC_DB_PASSWORD", secret_name="deployflow-identity-postgres", key="password")]
    main = container("identity-setup", images["keycloak"], uid=1000, memory="2Gi", cpu="1",
                     command=["/opt/keycloak/bin/kc.sh", "import", "--optimized", "--dir", "/run/identity-setup", "--override=false"],
                     environment=database + [env("DF_HOSTED_OIDC_USER_PASSWORD", secret_name="deployflow-identity-bootstrap", key="user-password")])
    bootstrap = container("bootstrap-admin", images["keycloak"], uid=1000, memory="2Gi", cpu="1",
                          command=["/opt/keycloak/bin/kc.sh", "bootstrap-admin", "user", "--optimized",
                                   "--username", "deployflow-bootstrap-admin", "--password:env", "DF_HOSTED_OIDC_ADMIN_PASSWORD", "--no-prompt"],
                          environment=database + [env("DF_HOSTED_OIDC_ADMIN_PASSWORD", secret_name="deployflow-identity-bootstrap", key="admin-password")])
    # Keycloak can exit zero when an admin already exists or an import is skipped.
    # Refuse all preexisting user-schema objects before either CLI may mutate it.
    check = (
        "import os,psycopg; "
        "c=psycopg.connect(os.environ['KC_DB_URL'].removeprefix('jdbc:'), "
        "user=os.environ['KC_DB_USERNAME'], password=os.environ['KC_DB_PASSWORD'], "
        "connect_timeout=5, options='-c default_transaction_read_only=on'); "
        "objects=c.execute(\"SELECT count(*) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'\").fetchone()[0]; "
        "c.close(); "
        "raise SystemExit('Identity database is not empty; fresh setup refused.' if objects else 0)"
    )
    guard = container("identity-database-empty", images["backend"], command=["python", "-c", check], environment=database)
    for current in (main, bootstrap):
        current["resources"]["requests"]["memory"] = "768Mi"
        current["volumeMounts"].append({"name": "keycloak-data", "mountPath": "/opt/keycloak/data"})
    main["volumeMounts"].append({"name": "identity-template", "mountPath": "/run/identity-setup", "readOnly": True})
    template = pod("identity-setup", main, uid=1000)
    template["spec"].update({"restartPolicy": "Never", "initContainers": [guard, bootstrap]})
    template["spec"]["volumes"] += [
        {"name": "identity-template", "configMap": {"name": "deployflow-realm", "defaultMode": 0o440}},
        {"name": "keycloak-data", "emptyDir": {"sizeLimit": "256Mi"}},
    ]
    return [resource("Job", "deployflow-identity-setup", api_version="batch/v1",
                     spec={"backoffLimit": 0, "activeDeadlineSeconds": 600, "template": template})]


def ingress():
    paths = []
    for path, name, port, kind in (
        ("/webhooks/github", "webhook", 8000, "Exact"),
        ("/identity/realms/deployflow-local", "keycloak", 8080, "Prefix"),
        ("/identity/resources", "keycloak", 8080, "Prefix"),
        ("/", "frontend", 8080, "Prefix"),
    ):
        paths.append({"path": path, "pathType": kind,
                      "backend": {"service": {"name": name, "port": {"number": port}}}})
    value = resource("Ingress", "deployflow", api_version="networking.k8s.io/v1", spec={
        "ingressClassName": "nginx", "tls": [{"hosts": [HOST], "secretName": "deployflow-tls"}],
        "rules": [{"host": HOST, "http": {"paths": paths}}],
    })
    value["metadata"]["annotations"] = {
        "cert-manager.io/cluster-issuer": "letsencrypt-production",
        "nginx.ingress.kubernetes.io/ssl-redirect": "true",
        "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
        "nginx.ingress.kubernetes.io/proxy-buffering": "off",
        "nginx.ingress.kubernetes.io/proxy-body-size": "1m",
    }
    # The namespace's default-deny policy also selects cert-manager's temporary
    # HTTP-01 Pods. Permit the ingress controller to reach only those responders;
    # keep this rule for future renewals without exposing any platform Pod.
    solver = resource("NetworkPolicy", "allow-acme-http01", api_version="networking.k8s.io/v1", spec={
        "podSelector": {"matchLabels": {"acme.cert-manager.io/http01-solver": "true"}},
        "policyTypes": ["Ingress"],
        "ingress": [{"from": [{
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/component": "controller"}},
        }], "ports": [{"protocol": "TCP", "port": 8089}]}],
    })
    return [solver, value]


def render(stage, images):
    for role, repository in IMAGE_REPOSITORIES.items():
        if not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", images.get(role, "")):
            raise ValueError(f"{role} must use its approved GHCR repository and immutable digest")
    phases = {"foundation": foundation, "migration": lambda: migration(images),
              "identity-setup": lambda: identity_setup(images),
              "identity-import": lambda: identity_import(images),
              "workloads": lambda: workloads(images), "ingress": ingress}
    if stage not in phases:
        raise ValueError("Render one explicitly reviewed stage at a time")
    return {"apiVersion": "v1", "kind": "List", "items": phases[stage]()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=["foundation", "migration", "identity-setup", "identity-import", "workloads", "ingress"])
    for role in IMAGE_REPOSITORIES:
        parser.add_argument(f"--{role}-image", required=True)
    args = parser.parse_args()
    images = {role: getattr(args, f"{role}_image") for role in IMAGE_REPOSITORIES}
    try:
        document = render(args.stage, images)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(document, indent=2))


if __name__ == "__main__":
    main()
