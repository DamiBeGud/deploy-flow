"""Offline permission and workload contract tests for Rails activation."""

import unittest

from render import IMAGE_REPOSITORIES, NAMESPACE, TARGET
from render_managed_rails import CATALOG, READERS, SLOTS, WORKER, render_managed_rails

IMAGE = IMAGE_REPOSITORIES["backend"] + "@sha256:" + "1" * 64
PROFILE = {
    "owner_id": "synthetic-user", "organization_id": "synthetic-org",
    "repository": "example/rails", "revision": "pilot", "source_sha": "a" * 40,
}


class ManagedRailsHostingTests(unittest.TestCase):
    def setUp(self):
        self.overlay = render_managed_rails(IMAGE, PROFILE)
        self.resources = {
            (item["kind"], item["metadata"].get("namespace"), item["metadata"]["name"]): item
            for item in self.overlay["bootstrap_resources"]["items"]
        }

    def test_readers_have_only_named_catalog_access(self):
        catalog = self.resources["Role", NAMESPACE, CATALOG]
        self.assertEqual(catalog["rules"], [{"apiGroups": [""], "resources": ["configmaps"],
                                            "resourceNames": [CATALOG], "verbs": ["get"]}])
        self.assertEqual(
            {subject["name"] for subject in self.resources["RoleBinding", NAMESPACE, CATALOG]["subjects"]},
            set(READERS),
        )
        self.assertEqual(set(self.overlay["deployment_patches"]), {"api", "delivery-worker"})

    def test_worker_has_only_the_declared_database_reference_and_test_secret_scope(self):
        worker = self.overlay["provisioner"]["spec"]["template"]["spec"]
        secret_rules = self.resources["Role", TARGET, WORKER]["rules"]
        self.assertEqual(secret_rules, [
            {"apiGroups": [""], "resources": ["secrets"], "resourceNames": SLOTS, "verbs": ["get"]},
            {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]},
        ])
        self.assertEqual(worker["serviceAccountName"], WORKER)
        self.assertTrue(worker["automountServiceAccountToken"])
        for container in worker["containers"] + worker["initContainers"]:
            self.assertNotIn("envFrom", container)
            all_secret_keys = [
                item["valueFrom"]["secretKeyRef"]["key"] for item in container["env"] if "valueFrom" in item
            ]
            self.assertEqual(all_secret_keys, ["DF_DATABASE_URL"])

    def test_fail_closed_admission_covers_worker_and_reserved_names(self):
        policy = next(value for key, value in self.resources.items() if key[0] == "ValidatingAdmissionPolicy")
        spec = policy["spec"]
        self.assertEqual(spec["failurePolicy"], "Fail")
        match = spec["matchConditions"][0]["expression"]
        for scope in [TARGET, WORKER, *SLOTS]:
            self.assertIn(scope, match)
        self.assertIn("request.operation == 'CREATE'", spec["validations"][0]["expression"])
        self.assertIn("size(object.data) == 2", spec["validations"][1]["expression"])
        self.assertEqual(next(value for key, value in self.resources.items()
                              if key[0] == "ValidatingAdmissionPolicyBinding")["spec"]["validationActions"], ["Deny"])

    def test_invalid_profile_or_mutable_image_is_denied(self):
        with self.assertRaises(ValueError):
            render_managed_rails(IMAGE, PROFILE | {"source_sha": "main"})
        with self.assertRaises(ValueError):
            render_managed_rails(IMAGE_REPOSITORIES["backend"] + ":latest", PROFILE)
