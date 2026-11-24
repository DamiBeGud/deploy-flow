"""Render the opt-in managed Rails capability and its deployment patches offline."""

from __future__ import annotations

import argparse
import json
import re

from render import IMAGE_REPOSITORIES, NAMESPACE, REVISION, TARGET, env, policy, resource, runtime

WORKER = "rails-provisioner"
CATALOG = "deployflow-rails-bindings"
AGENT_SECRET = "deployflow-rails-deepseek"
AGENT_SECRET_KEY = "api-key"
SLOTS = [f"deployflow-rails-slot-{index}" for index in range(1, 5)]
GROUP = "rbac.authorization.k8s.io"
READERS = ("api", "delivery-worker", "kube-validator", "kube-deployer", "kube-cleaner")


def role(name, namespace, subjects, rules):
    return [
        resource("Role", name, namespace=namespace, api_version=f"{GROUP}/v1", rules=rules),
        resource(
            "RoleBinding", name, namespace=namespace, api_version=f"{GROUP}/v1",
            subjects=[{"kind": "ServiceAccount", "name": service, "namespace": NAMESPACE} for service in subjects],
            roleRef={"kind": "Role", "name": name, "apiGroup": GROUP},
        ),
    ]


def admission():
    name = "deployflow-managed-rails-secrets"
    version = "admissionregistration.k8s.io/v1"
    writer = f"request.userInfo.username == 'system:serviceaccount:{NAMESPACE}:{WORKER}'"
    slots = f"object.metadata.name in {json.dumps(SLOTS)}"
    annotations = "object.metadata.annotations"
    return [
        resource("ValidatingAdmissionPolicy", name, namespace=None, api_version=version, spec={
            "failurePolicy": "Fail",
            "matchConstraints": {"resourceRules": [{
                "apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE", "UPDATE"],
                "resources": ["secrets"], "scope": "Namespaced",
            }]},
            "matchConditions": [{
                "name": "managed-rails-only",
                "expression": f"request.namespace == '{TARGET}' && (({writer}) || (has(object.metadata.name) && ({slots})))",
            }],
            "validations": [
                {"expression": f"({writer}) && ({slots}) && request.operation == 'CREATE'",
                 "message": "Only the Rails provisioner can create a managed credential slot"},
                {"expression": "object.type == 'Opaque' && has(object.immutable) && object.immutable"
                 " && has(object.data) && size(object.data) == 2 && 'password' in object.data"
                 " && 'SECRET_KEY_BASE' in object.data && size(object.data['password']) in [43, 60]"
                 " && size(object.data['SECRET_KEY_BASE']) in [128, 172]",
                 "message": "Managed Rails credentials must have exactly two generated keys"},
                {"expression": f"has({annotations}) && size({annotations}) == 3 && "
                 + " && ".join(
                     f"'{key}' in {annotations} && {annotations}[{key!r}].matches('{pattern}')"
                     for key, pattern in (
                         ("deployflow.io/application-id", "^[a-f0-9-]{36}$"),
                         ("deployflow.io/rails-reservation", "^[a-f0-9-]{36}$"),
                         ("deployflow.io/source-sha", "^[a-f0-9]{40}$"),
                     )
                 ),
                 "message": "Managed Rails credentials require an application, source and reservation"},
            ],
        }),
        resource("ValidatingAdmissionPolicyBinding", name, namespace=None, api_version=version, spec={
            "policyName": name, "validationActions": ["Deny"],
        }),
    ]


def render_managed_rails(image, profile, *, scoped_agent=False):
    if not re.fullmatch(re.escape(IMAGE_REPOSITORIES["backend"]) + r"@sha256:[a-f0-9]{64}", image):
        raise ValueError("Use an immutable DeployFlow backend image")
    if (
        set(profile) != {"owner_id", "organization_id", "repository", "revision", "source_sha"}
        or not all(isinstance(value, str) and value and len(value) <= 255 for value in profile.values())
        or not all(len(profile[key]) <= 128 for key in ("owner_id", "organization_id"))
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9._-]*", profile["repository"])
        or not re.fullmatch(r"[a-f0-9]{40}", profile["source_sha"])
    ):
        raise ValueError("Provide the approved owner, organization, repository, branch and source SHA")

    config_rule = {"apiGroups": [""], "resources": ["configmaps"], "resourceNames": [CATALOG]}
    # NetworkPolicies are additive. Leave the existing PostgreSQL policy in place.
    database_policy = policy("postgres", [WORKER], 5432)
    database_policy["metadata"]["name"] = "allow-rails-provisioner-postgres"
    resources = [
        resource("ConfigMap", CATALOG),
        resource("ServiceAccount", WORKER, automountServiceAccountToken=False),
        *(resource("ServiceAccount", reader, automountServiceAccountToken=False) for reader in READERS[:2]),
        *role(CATALOG, NAMESPACE, READERS, [{**config_rule, "verbs": ["get"]}]),
        *role(WORKER, NAMESPACE, [WORKER], [{**config_rule, "verbs": ["get", "patch"]}]),
        *role(WORKER, TARGET, [WORKER], [
            {"apiGroups": [""], "resources": ["secrets"], "resourceNames": SLOTS, "verbs": ["get"]},
            {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]},
        ]),
        database_policy,
        *admission(),
    ]
    profile_env = env("DF_MANAGED_RAILS_PROFILE", json.dumps(profile, sort_keys=True))
    worker = runtime(WORKER, image, ["python", "-m", "app.bootstrap.rails_jobs"], identity=True)
    pod = worker["spec"]["template"]["spec"]
    database_env = env("DF_DATABASE_URL", secret_name="deployflow-runtime", key="DF_DATABASE_URL")
    main = pod["containers"][0]
    main.pop("envFrom")
    main["env"] = [env("DF_KUBE_ACCESS_MODE", "incluster"), profile_env, database_env]
    init = pod["initContainers"][0]
    init.pop("envFrom")
    init["env"] = [database_env]
    worker["metadata"]["labels"]["app.kubernetes.io/part-of"] = "deployflow-managed-rails"
    worker["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/part-of"] = "deployflow-managed-rails"

    # Strategic-merge patches keep existing environment, credential volumes and
    # workload settings. Validator and deployer already use projected identities.
    patches = {
        name: {"spec": {"template": {"spec": {
            "serviceAccountName": name, "automountServiceAccountToken": True,
            "containers": [{"name": name, "env": [
                env("DF_KUBE_ACCESS_MODE", "incluster"), *([profile_env] if name == "api" else []),
            ]}],
        }}}}
        for name in READERS[:2]
    }
    if scoped_agent:
        enabled = env("DF_MANAGED_RAILS_AGENT_ENABLED", "true")
        patches["api"]["spec"]["template"]["spec"]["containers"][0]["env"].append(enabled)
        patches["worker"] = {"spec": {"template": {"spec": {"containers": [{
            "name": "worker",
            "env": [
                profile_env,
                enabled,
                env("DF_AGENT_MAX_TOOL_CALLS", "20"),
                env("DF_DEEPSEEK_API_KEY", secret_name=AGENT_SECRET, key=AGENT_SECRET_KEY),
            ],
        }]}}}}
    return {
        "schema_revision": REVISION,
        "bootstrap_resources": {"apiVersion": "v1", "kind": "List", "items": resources},
        "provisioner": worker,
        "deployment_patches": patches,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--profile-file", type=argparse.FileType("r"), required=True)
    parser.add_argument("--scoped-agent", action="store_true")
    args = parser.parse_args()
    print(json.dumps(render_managed_rails(args.backend_image, json.load(args.profile_file),
                                         scoped_agent=args.scoped_agent), indent=2))
