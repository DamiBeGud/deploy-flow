"""Offline policy checks for the proposed hosting boundary; no cluster or credentials."""

import base64
import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path

from prepare_identity_import import adapted_realm, prepare_identity
from prepare_secrets import prepare
from render import (
    HOST,
    IMAGE_REPOSITORIES,
    NAMESPACE,
    REVISION,
    TARGET,
    fresh_realm,
    render,
)

IMAGES = {role: repository + "@sha256:" + str(index) * 64
          for index, (role, repository) in enumerate(IMAGE_REPOSITORIES.items(), 1)}


def by_name(stage, kind, name):
    return next(item for item in render(stage, IMAGES)["items"]
                if item["kind"] == kind and item["metadata"]["name"] == name)


class HostingPolicyTests(unittest.TestCase):
    def test_executor_can_revalidate_admission_with_only_the_validator_peer(self):
        rule = by_name("foundation", "NetworkPolicy", "allow-kube-validator")["spec"]
        self.assertEqual(rule["ingress"][0]["ports"], [{"protocol": "TCP", "port": 8001}])
        self.assertEqual(rule["ingress"][0]["from"], [
            {"podSelector": {"matchLabels": {"app.kubernetes.io/name": name}}}
            for name in ("api", "delivery-worker", "kube-deployer")
        ])

    def test_only_new_platform_resources_and_three_target_bindings(self):
        all_objects = [item for stage in ("foundation", "migration", "identity-setup", "identity-import", "workloads", "ingress") for item in render(stage, IMAGES)["items"]]
        identifiers = [(item["kind"], item["metadata"].get("namespace"), item["metadata"]["name"]) for item in all_objects]
        self.assertEqual(len(set(identifiers)), len(identifiers))
        target_objects = [item for item in all_objects if item["metadata"].get("namespace") == TARGET]
        self.assertEqual({item["metadata"]["name"] for item in target_objects},
                         {"deployflow-platform-reader", "deployflow-platform-validator", "deployflow-platform-deployer"})
        self.assertTrue(all(item["kind"] == "RoleBinding" for item in target_objects))
        self.assertFalse(any(item["kind"] in {"ClusterRole", "ClusterRoleBinding", "Role", "Secret"} for item in all_objects))
        for item in target_objects:
            role = item["metadata"]["name"].removeprefix("deployflow-platform-")
            self.assertEqual(item["roleRef"]["name"], f"deployflow-{role}")
            self.assertEqual(item["subjects"], [{"kind": "ServiceAccount", "name": f"kube-{role}", "namespace": NAMESPACE}])

    def test_only_identity_pods_get_tokens_and_no_bootstrap_copy(self):
        identities = set()
        key_holders = set()
        for item in render("workloads", IMAGES)["items"]:
            spec = item["spec"]["template"]["spec"]
            name = item["metadata"]["name"]
            if spec["automountServiceAccountToken"]:
                identities.add(name)
                self.assertEqual(spec["serviceAccountName"], name)
                env = {v["name"]: v.get("value") for v in spec["containers"][0].get("env", [])}
                self.assertEqual(env["DF_KUBE_ACCESS_MODE"], "incluster")
            for volume in spec["volumes"]:
                if volume.get("secret", {}).get("secretName") == "deployflow-github-key":
                    key_holders.add(name)
            if name in {"kube-reader", "kube-validator"}:
                self.assertEqual(spec["containers"][0]["envFrom"], [{"configMapRef": {"name": "deployflow-config"}}])
                self.assertNotIn("initContainers", spec)
            self.assertNotIn("credential_bootstrap", json.dumps(spec))
            self.assertNotIn("ssh", json.dumps(spec).lower())
            for main in spec["containers"] + spec.get("initContainers", []):
                self.assertFalse(main["securityContext"]["allowPrivilegeEscalation"])
                self.assertTrue(main["securityContext"]["readOnlyRootFilesystem"])
                self.assertEqual(main["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertEqual(identities, {"kube-reader", "kube-validator", "kube-deployer"})
        self.assertEqual(key_holders, {"worker", "github-worker", "delivery-worker", "kube-deployer"})
        # The API's existing PR gate requires the worker key location to be
        # configured, but the HTTP process must not receive the key itself.
        api = by_name("workloads", "Deployment", "api")["spec"]["template"]["spec"]
        worker = by_name("workloads", "Deployment", "github-worker")["spec"]["template"]["spec"]
        api_env = {item["name"]: item.get("value") for item in api["containers"][0]["env"]}
        worker_env = {item["name"]: item.get("value") for item in worker["containers"][0]["env"]}
        self.assertEqual(api_env["DF_GITHUB_PRIVATE_KEY_PATH"], worker_env["DF_GITHUB_PRIVATE_KEY_PATH"])
        self.assertNotIn("api", key_holders)

    def test_fresh_storage_does_not_mount_or_restore_old_data(self):
        states = [v for v in render("foundation", IMAGES)["items"] if v["kind"] == "StatefulSet"]
        self.assertEqual({v["metadata"]["name"] for v in states}, {"postgres", "identity-postgres", "redis"})
        self.assertEqual([v["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] for v in states], ["5Gi", "2Gi", "1Gi"])
        for state in states:
            volumes = state["spec"]["template"]["spec"]["volumes"]
            self.assertFalse(any("hostPath" in v or "persistentVolumeClaim" in v for v in volumes))
            self.assertNotIn("pg_restore", json.dumps(state))

    def test_execution_and_cleanup_start_disabled(self):
        config = by_name("foundation", "ConfigMap", "deployflow-config")["data"]
        self.assertEqual(config["DF_DEPLOYMENT_EXECUTION_ENABLED"], "false")
        self.assertEqual(config["DF_DEPLOYMENT_CLEANUP_ENABLED"], "false")
        self.assertEqual(config["DF_ENVIRONMENT"], "production")
        self.assertEqual(config["DF_AUTH_MODE"], "oidc")
        self.assertTrue(config["DF_OIDC_JWKS_URL"].startswith("https://"))
        self.assertNotIn("kube-cleaner", json.dumps(render("workloads", IMAGES)))

    def test_host_routes_do_not_publish_admin_or_management(self):
        routes = by_name("ingress", "Ingress", "deployflow")["spec"]
        self.assertEqual(routes["rules"][0]["host"], HOST)
        paths = routes["rules"][0]["http"]["paths"]
        self.assertEqual([(v["path"], v["pathType"]) for v in paths], [
            ("/webhooks/github", "Exact"), ("/identity/realms/deployflow-local", "Prefix"),
            ("/identity/resources", "Prefix"), ("/", "Prefix"),
        ])
        self.assertFalse(any(v["backend"]["service"]["port"]["number"] == 9000 for v in paths))
        nginx = (Path(__file__).resolve().parents[2] / "frontend/nginx.conf").read_text()
        self.assertIn("location /identity/ { return 404; }", nginx)
        self.assertIn("location /webhooks/ { return 404; }", nginx)
        self.assertIn("proxy_buffering off;", nginx)

    def test_acme_solver_is_reachable_only_from_ingress_controller_on_http01_port(self):
        rule = by_name("ingress", "NetworkPolicy", "allow-acme-http01")["spec"]
        self.assertEqual(rule, {
            "podSelector": {"matchLabels": {"acme.cert-manager.io/http01-solver": "true"}},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}},
                "podSelector": {"matchLabels": {"app.kubernetes.io/component": "controller"}},
            }], "ports": [{"protocol": "TCP", "port": 8089}]}],
        })
        for item in render("workloads", IMAGES)["items"]:
            labels = item["spec"]["template"]["metadata"]["labels"]
            self.assertNotEqual(labels.get("acme.cert-manager.io/http01-solver"), "true")

    def test_account_import_is_separate_private_and_never_bootstraps_empty_users(self):
        job = render("identity-import", IMAGES)["items"][0]
        spec = job["spec"]["template"]["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertIn("--override=false", spec["containers"][0]["command"])
        self.assertTrue(any(v.get("secret", {}).get("secretName") == "deployflow-identity-import" for v in spec["volumes"]))
        main = by_name("workloads", "Deployment", "keycloak")["spec"]["template"]["spec"]
        self.assertNotIn("--import-realm", main["containers"][0]["command"])
        self.assertNotIn("KC_BOOTSTRAP_ADMIN_PASSWORD", json.dumps(main))
        self.assertNotIn("deployflow-identity-import", json.dumps(main))

    def test_fresh_accounts_are_created_once_and_bootstrap_secrets_stay_out_of_server(self):
        job = render("identity-setup", IMAGES)["items"][0]
        spec = job["spec"]["template"]["spec"]
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(spec["restartPolicy"], "Never")
        self.assertFalse(spec["automountServiceAccountToken"])
        guard, bootstrap = spec["initContainers"]
        self.assertEqual(guard["image"], IMAGES["backend"])
        self.assertIn("default_transaction_read_only=on", guard["command"][-1])
        self.assertIn("fresh setup refused", guard["command"][-1])
        self.assertEqual([item["valueFrom"]["secretKeyRef"]["key"] for item in guard["env"] if "valueFrom" in item], ["password"])
        main = spec["containers"][0]
        self.assertEqual(bootstrap["command"][1:3], ["bootstrap-admin", "user"])
        self.assertIn("--no-prompt", bootstrap["command"])
        self.assertIn("--optimized", bootstrap["command"])
        self.assertIn("--override=false", main["command"])
        for current, field in ((bootstrap, "admin-password"), (main, "user-password")):
            keys = [item["valueFrom"]["secretKeyRef"]["key"] for item in current["env"] if "valueFrom" in item]
            self.assertEqual(keys, ["password", field])
            self.assertTrue(current["securityContext"]["readOnlyRootFilesystem"])
        server = by_name("workloads", "Deployment", "keycloak")["spec"]["template"]["spec"]
        self.assertNotIn("deployflow-identity-bootstrap", json.dumps(server))
        self.assertNotIn("deployflow-realm", json.dumps(server))
        self.assertNotIn("--import-realm", server["containers"][0]["command"])

    def test_fresh_realm_requires_first_login_password_change_and_pkce_at_hosted_origin(self):
        realm = fresh_realm()
        self.assertEqual(realm["sslRequired"], "external")
        self.assertFalse(realm["registrationAllowed"])
        self.assertNotIn("id", realm)
        self.assertEqual(len(realm["users"]), 1)
        user = realm["users"][0]
        self.assertNotIn("id", user)
        self.assertEqual(user["realmRoles"], ["administrator"])
        self.assertEqual(user["requiredActions"], ["UPDATE_PASSWORD"])
        self.assertEqual(user["credentials"], [{"type": "password", "value": "${DF_HOSTED_OIDC_USER_PASSWORD}", "temporary": True}])
        client = realm["clients"][0]
        self.assertTrue(client["publicClient"])
        self.assertTrue(client["standardFlowEnabled"])
        self.assertFalse(client["directAccessGrantsEnabled"])
        self.assertEqual(client["attributes"]["pkce.code.challenge.method"], "S256")
        self.assertEqual(client["redirectUris"], [f"https://{HOST}/auth/callback"])
        self.assertEqual(client["webOrigins"], [f"https://{HOST}"])
        self.assertEqual(json.loads(by_name("foundation", "ConfigMap", "deployflow-realm")["data"]["deployflow-local-realm.json"]), realm)

    def test_migration_is_separate_pinned_and_workloads_wait(self):
        job = render("migration", IMAGES)["items"][0]
        spec = job["spec"]["template"]["spec"]
        self.assertEqual(spec["containers"][0]["command"], ["alembic", "upgrade", REVISION])
        self.assertFalse(spec["automountServiceAccountToken"])
        for value in render("workloads", IMAGES)["items"]:
            name = value["metadata"]["name"]
            if name in {"frontend", "external-probe", "keycloak", "kube-reader", "kube-validator"}:
                continue
            init = value["spec"]["template"]["spec"]["initContainers"][0]
            self.assertIn(REVISION, init["command"][-1])
            self.assertNotIn("alembic upgrade", init["command"][-1])

    def test_internal_ingress_is_default_denied_and_mcp_is_caller_scoped(self):
        self.assertEqual(by_name("foundation", "NetworkPolicy", "default-deny-ingress")["spec"],
                         {"podSelector": {}, "policyTypes": ["Ingress"]})
        for target, callers in (("kube-reader", ["api"]), ("kube-validator", ["api", "delivery-worker", "kube-deployer"])):
            rule = by_name("foundation", "NetworkPolicy", f"allow-{target}")["spec"]["ingress"][0]
            self.assertEqual([p["podSelector"]["matchLabels"]["app.kubernetes.io/name"] for p in rule["from"]], callers)

    def test_mutable_foreign_or_unscoped_images_and_combined_activation_rejected(self):
        for role in IMAGES:
            for replacement in ("latest", IMAGE_REPOSITORIES[role] + ":latest", "ghcr.io/other/image@sha256:" + "a" * 64):
                with self.assertRaises(ValueError):
                    render("workloads", IMAGES | {role: replacement})
        with self.assertRaises(ValueError):
            render("all", IMAGES)

    def test_secret_preparation_is_private_fresh_and_connection_passwords_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            parent.chmod(0o700)
            key = parent / "app.pem"
            key.write_text("-----BEGIN PRIVATE KEY-----\nsynthetic-test-key\n-----END PRIVATE KEY-----\n")
            key.chmod(0o600)
            output = parent / "hosted"
            names = prepare(output, key)
            self.assertEqual(len(names), 7)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            documents = {}
            for path in output.glob("*.json"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                document = json.loads(path.read_text())
                self.assertEqual(document["metadata"]["namespace"], NAMESPACE)
                documents[path.stem] = {k: base64.b64decode(v).decode() for k, v in document["data"].items()}
            runtime = documents["deployflow-runtime"]
            self.assertIn(documents["deployflow-postgres"]["password"], runtime["DF_DATABASE_URL"])
            self.assertIn(documents["deployflow-redis"]["password"], runtime["DF_REDIS_URL"])
            self.assertNotEqual(documents["deployflow-identity-postgres"]["password"], documents["deployflow-postgres"]["password"])
            bootstrap = documents["deployflow-identity-bootstrap"]
            self.assertEqual(set(bootstrap), {"admin-password", "user-password"})
            self.assertNotEqual(bootstrap["admin-password"], bootstrap["user-password"])
            self.assertGreaterEqual(len(bootstrap["user-password"]), 40)
            for password in bootstrap.values():
                self.assertNotIn(password, json.dumps(render("foundation", IMAGES)))
            account_file = output / "initial-accounts.txt"
            self.assertEqual(stat.S_IMODE(account_file.stat().st_mode), 0o600)
            for password in bootstrap.values():
                self.assertIn(password, account_file.read_text())
            with self.assertRaises(FileExistsError):
                prepare(output, key)
            key.chmod(0o644)
            with self.assertRaises(ValueError):
                prepare(parent / "denied", key)


def synthetic_realm(name):
    return {
        "id": f"realm-{name}", "realm": name, "sslRequired": "none",
        "attributes": {"frontendUrl": "http://localhost:8180", "untouched": "same"},
        "users": [{"id": f"user-{name}", "username": "synthetic-operator", "enabled": True,
                   "realmRoles": ["administrator"], "groups": ["operators"],
                   "credentials": [{"id": "credential-id", "type": "password",
                                    "secretData": '{"value":"synthetic-hash","salt":"synthetic-salt"}',
                                    "credentialData": '{"hashIterations":27500}'}]}],
        "clients": [{"id": "client-id", "clientId": "deployflow-local-ui", "publicClient": True,
                     "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
                     "attributes": {"pkce.code.challenge.method": "S256"},
                     "redirectUris": ["http://localhost:5174/auth/callback"], "webOrigins": ["http://localhost:5174"]}],
        "roles": {"realm": [{"id": "role-id", "name": "administrator"}]},
    }


class IdentityPreservationTests(unittest.TestCase):
    def test_only_host_settings_change_and_all_account_credentials_are_preserved(self):
        for name in ("master", "deployflow-local"):
            original = synthetic_realm(name)
            before = copy.deepcopy(original)
            after = adapted_realm(original)
            expected = copy.deepcopy(original)
            expected["sslRequired"] = "external"
            expected["attributes"]["frontendUrl"] = f"https://{HOST}/identity"
            if name == "deployflow-local":
                expected["clients"][0]["redirectUris"] = [f"https://{HOST}/auth/callback"]
                expected["clients"][0]["webOrigins"] = [f"https://{HOST}"]
            self.assertEqual(after, expected)
            self.assertEqual(original, before)

    def test_partial_exports_missing_users_credentials_or_secure_client_are_rejected(self):
        for change in ("users", "credentials", "identity", "client"):
            original = synthetic_realm("deployflow-local")
            if change == "users": original.pop("users")
            if change == "credentials": original["users"][0].pop("credentials")
            if change == "identity": original.pop("id")
            if change == "client": original["clients"][0]["publicClient"] = False
            with self.assertRaises(ValueError):
                adapted_realm(original)

    def test_private_import_includes_master_and_application_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp); parent.chmod(0o700)
            source = parent / "export"; source.mkdir(mode=0o700)
            originals = {}
            for name in ("master", "deployflow-local"):
                path = source / f"{name}-realm.json"
                path.write_text(json.dumps(synthetic_realm(name))); path.chmod(0o600)
                originals[path.name] = path.read_bytes()
            output = parent / "identity-secret.json"
            self.assertEqual(prepare_identity(source, output), {"realms": 2, "users": 2})
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            document = json.loads(output.read_text())
            self.assertEqual(document["kind"], "Secret")
            self.assertEqual(set(document["data"]), set(originals))
            for name, encoded in document["data"].items():
                exported = json.loads(base64.b64decode(encoded))
                self.assertEqual(exported["users"], json.loads(originals[name])["users"])
                self.assertEqual((source / name).read_bytes(), originals[name])
            with self.assertRaises(ValueError):
                prepare_identity(source, output)
            (source / "master-realm.json").unlink()
            with self.assertRaises(ValueError):
                prepare_identity(source, parent / "incomplete.json")
            self.assertFalse((parent / "incomplete.json").exists())


if __name__ == "__main__":
    unittest.main()
