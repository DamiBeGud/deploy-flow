"""Synthetic snapshots only; no Kubernetes, database or SSH credentials."""

import unittest

from capabilities import FLAGS, RESTART, prepare
from render import IMAGE_REPOSITORIES, render


def snapshot():
    images = {role: repo + "@sha256:" + str(i) * 64
              for i, (role, repo) in enumerate(IMAGE_REPOSITORIES.items(), 1)}
    objects = {"deployments": render("workloads", images)["items"],
               "configmaps": [], "networkpolicies": [], "serviceaccounts": []}
    for item in render("foundation", images)["items"]:
        kind = {"ConfigMap": "configmaps", "NetworkPolicy": "networkpolicies", "ServiceAccount": "serviceaccounts"}.get(item["kind"])
        if kind:
            objects[kind].append(item)
    for rows in objects.values():
        for item in rows:
            item["metadata"].update(uid="synthetic-" + item["metadata"]["name"], resourceVersion="42")
    return {"observed_at": "2026-10-22T00:00:00Z", "objects": objects, "policies": [], "jobs": [],
            "target_roles": [{"metadata": {"name": "deployflow-cleaner"}, "absent": True}],
            "runs": [{"id": "waiting-deployment", "release_id": "waiting-release", "status": "waiting_for_executor"}]}


class CapabilitiesTests(unittest.TestCase):
    def test_default_activation_enables_execution_without_cleanup_identity_or_permissions(self):
        plan = prepare(snapshot())
        self.assertEqual(plan["operation"], "enable-execution-capability")
        self.assertEqual(len(plan["steps"]), 2)
        self.assertTrue(all(s["operation"] == "json-patch" for s in plan["steps"]))
        patch = plan["steps"][0]["resources"][0]["json_patch"]
        self.assertEqual([p["path"] for p in patch if p["op"] == "replace"], ["/data/DF_DEPLOYMENT_EXECUTION_ENABLED"])
        self.assertEqual([r["name"] for r in plan["steps"][1]["resources"]], list(RESTART))
        self.assertFalse(plan["application_policy_changes"])
        self.assertFalse(plan["application_execution_requests"])
        self.assertFalse(plan["cleanup_requests"])

    def test_cleanup_consumes_only_cleanup_queue_with_separate_projected_identity(self):
        plan = prepare(snapshot(), include_cleanup=True)
        cleaner = plan["steps"][3]["resources"][0]["spec"]["template"]["spec"]
        self.assertEqual(cleaner["serviceAccountName"], "kube-cleaner")
        self.assertTrue(cleaner["automountServiceAccountToken"])
        main = cleaner["containers"][0]
        self.assertEqual(main["command"][-2:], ["--queues", "cleanup"])
        self.assertIn({"name": "DF_KUBE_ACCESS_MODE", "value": "incluster"}, main["env"])
        self.assertFalse(any(v.get("secret", {}).get("secretName") == "deployflow-github-key" for v in cleaner["volumes"]))
        role = plan["steps"][0]["resources"][1]
        self.assertEqual({v for rule in role["rules"] for v in rule["verbs"]}, {"get", "delete"})
        self.assertFalse(any("secrets" in rule["resources"] for rule in role["rules"]))
        self.assertFalse(plan["application_policy_changes"])
        self.assertFalse(plan["application_execution_requests"])
        self.assertFalse(plan["cleanup_requests"])

    def test_optimistic_patches_preserve_other_permissions_and_configuration(self):
        plan = prepare(snapshot(), include_cleanup=True)
        for step in plan["steps"]:
            if step["operation"] == "json-patch":
                for patch in step["resources"]:
                    self.assertEqual([x["path"] for x in patch["json_patch"][:2]],
                                     ["/metadata/uid", "/metadata/resourceVersion"])
        for patch in plan["steps"][1]["resources"]:
            self.assertEqual(patch["json_patch"][-1]["path"], "/spec/ingress/0/from/-")
        enabled = plan["steps"][2]["resources"][0]["json_patch"]
        self.assertEqual({x["path"] for x in enabled if x["op"] == "replace"}, {"/data/" + flag for flag in FLAGS})
        self.assertEqual([x["name"] for x in plan["steps"][4]["resources"]], list(RESTART))

    def test_pending_work_or_enabled_policy_cannot_be_activated_by_generic_plan(self):
        for mutate in (
            lambda s: s["policies"].append({"enabled": True}),
            lambda s: s["jobs"].append({"kind": "execution", "count": 1}),
            lambda s: s["jobs"].append({"kind": "delivery", "status": "queued", "count": 1}),
            lambda s: s["jobs"].append({"kind": "analysis_pending", "count": 1}),
        ):
            state = snapshot()
            mutate(state)
            with self.assertRaises(ValueError):
                prepare(state)

    def test_existing_cleaner_or_changed_flags_and_unowned_resources_require_review(self):
        for mutate in (
            lambda s: s["target_roles"][0].update(absent=False),
            lambda s: s["objects"]["serviceaccounts"].append({"metadata": {"name": "kube-cleaner"}}),
            lambda s: s["objects"]["configmaps"][0]["data"].update(DF_DEPLOYMENT_EXECUTION_ENABLED="true"),
            lambda s: s["objects"]["deployments"][0]["metadata"].update(labels={}),
            lambda s: s["objects"]["deployments"][8]["spec"]["template"]["spec"]["containers"][0].update(image="untrusted:latest"),
        ):
            state = snapshot()
            mutate(state)
            with self.assertRaises(ValueError):
                prepare(state, include_cleanup=True)


if __name__ == "__main__":
    unittest.main()
