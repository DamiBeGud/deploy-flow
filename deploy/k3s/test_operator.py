"""Offline safety tests. No real SSH, registry credentials or Kubernetes writes."""

import base64
import contextlib
import copy
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import remote
from bundle import STAGES, create, digest, encoded, validate
from check_publication import check
from operate import secret_document, ssh_command
from prepare_registry_secret import prepare as prepare_pull
from prepare_secrets import prepare
from render import IMAGE_REPOSITORIES, NAMESPACE, PULL_SECRET, render

IMAGES = {role: repo + "@sha256:" + str(i) * 64
          for i, (role, repo) in enumerate(IMAGE_REPOSITORIES.items(), 1)}
SOURCE = "a" * 40


class BundleTests(unittest.TestCase):
    def test_bundle_binds_source_images_every_stage_and_exact_renderer(self):
        bundle = create(IMAGES, SOURCE)
        self.assertEqual(validate(bundle), bundle)
        self.assertEqual(set(bundle["sha256"]), set(STAGES))
        changed = copy.deepcopy(bundle)
        changed["stages"]["foundation"]["items"][0]["metadata"]["name"] = "another-namespace"
        changed["sha256"]["foundation"] = digest(changed["stages"]["foundation"])
        with self.assertRaises(ValueError):
            validate(changed)
        with self.assertRaises(ValueError):
            create(IMAGES, "main")

    def test_all_pods_reference_pull_secret_and_only_digest_images(self):
        for stage in STAGES:
            for item in render(stage, IMAGES)["items"]:
                if "template" not in item.get("spec", {}):
                    continue
                pod = item["spec"]["template"]["spec"]
                self.assertEqual(pod["imagePullSecrets"], [{"name": PULL_SECRET}])
                for container in pod["containers"] + pod.get("initContainers", []):
                    self.assertRegex(container["image"], r"@sha256:[0-9a-f]{64}$")

    def test_only_exact_successful_manual_publication_is_eligible(self):
        run = {"repository": {"full_name": "DamiBeGud/deploy-flow"}, "path": ".github/workflows/platform-images.yml",
               "event": "workflow_dispatch", "head_branch": "main", "head_sha": SOURCE,
               "run_attempt": 2, "status": "completed", "conclusion": "success"}
        check(run, SOURCE, "2")
        for key, value in {"repository": {"full_name": "other/repo"}, "path": "other.yml",
                           "event": "pull_request", "head_branch": "feature", "head_sha": "b" * 40,
                           "run_attempt": 1, "status": "in_progress", "conclusion": "failure"}.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                check(run | {key: value}, SOURCE, "2")


class CredentialTests(unittest.TestCase):
    def test_pull_credential_is_private_and_secret_scope_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            token = root / "pull-token"
            token.write_text("synthetic-read-only-token")
            token.chmod(0o600)
            key = root / "app.pem"
            key.write_text("-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----")
            key.chmod(0o600)
            output = root / "secrets"
            prepare(output, key)
            target = output / "deployflow-ghcr.json"
            prepare_pull(target, "synthetic-owner", token)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            value = secret_document(output)
            self.assertEqual(len(value["items"]), 8)
            pull = next(item for item in value["items"] if item["metadata"]["name"] == PULL_SECRET)
            auth = json.loads(base64.b64decode(pull["data"][".dockerconfigjson"]))
            self.assertEqual(set(auth["auths"]), {"ghcr.io"})
            with self.assertRaises(FileExistsError):
                prepare_pull(target, "synthetic-owner", token)
            token.chmod(0o644)
            with self.assertRaises(ValueError):
                prepare_pull(output / "denied.json", "synthetic-owner", token)
            bad = json.loads(target.read_text())
            bad["metadata"]["namespace"] = "default"
            target.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                secret_document(output)

    def test_ssh_is_noninteractive_and_strictly_pinned(self):
        command = ssh_command("91.89.185.146", 22, "connectops", Path("key"), Path("hosts"))
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("IdentitiesOnly=yes", command)
        self.assertIn("UserKnownHostsFile=hosts", command)
        self.assertNotIn("-A", command)
        with self.assertRaises(ValueError):
            ssh_command("-oProxyCommand=evil", 22, "connectops", Path("key"), Path("hosts"))


class RemoteTests(unittest.TestCase):
    def test_wrong_server_or_replaced_namespace_stops_before_mutation(self):
        with patch.object(remote.socket, "gethostname", return_value="wrong"), self.assertRaises(ValueError):
            remote.identity()
        with patch.object(remote.socket, "gethostname", return_value="homeserver1"), \
                patch.object(remote, "get", return_value={"metadata": {"uid": "replaced"}}), self.assertRaises(ValueError):
            remote.identity()

    def test_foreign_owned_resource_cannot_be_adopted(self):
        item = render("foundation", IMAGES)["items"][0]
        with patch.object(remote, "get", return_value={"metadata": {"labels": {}}}), self.assertRaises(ValueError):
            remote.check_collisions([item])

    def test_wrong_approval_digest_performs_no_mutation(self):
        request = {"operation": "foundation", "document": render("foundation", IMAGES), "approval": "0" * 64}
        with patch.object(remote, "identity", return_value=None), patch.object(remote, "apply") as apply, \
                patch.object(remote.sys, "stdin", io.StringIO(json.dumps(request))), self.assertRaises(ValueError):
            remote.main()
        apply.assert_not_called()

    def test_secret_retry_does_not_rotate_or_reapply_existing_values(self):
        secret = {"kind": "Secret", "metadata": {"name": "deployflow-ghcr", "namespace": NAMESPACE,
                  "labels": {"app.kubernetes.io/part-of": "deployflow-platform"}}, "type": "Opaque", "data": {"x": "eA=="}}
        with patch.object(remote, "ensure_fresh"), patch.object(remote, "get", return_value=secret), \
                patch.object(remote, "kubectl") as kube, contextlib.redirect_stdout(io.StringIO()):
            remote.install_secrets({"items": [secret]}, namespace=True)
            kube.assert_not_called()
            with self.assertRaises(ValueError):
                remote.install_secrets({"items": [secret | {"data": {"x": "eQ=="}}]}, namespace=True)
            kube.assert_not_called()

    def test_completed_job_is_required_before_workload_start(self):
        with patch.object(remote, "get", return_value={"status": {"failed": 1}}), self.assertRaises(ValueError):
            remote.completed_job("setup")

    def test_existing_workloads_refuse_fresh_install(self):
        with patch.object(remote, "list_objects", return_value=[{"kind": "Deployment"}]), self.assertRaises(ValueError):
            remote.ensure_fresh()

    def test_identical_partial_workload_apply_can_resume_with_empty_state(self):
        document = render("workloads", IMAGES)
        existing = copy.deepcopy(document["items"][0])
        existing["metadata"]["annotations"] = {
            "kubectl.kubernetes.io/last-applied-configuration": json.dumps(document["items"][0])}

        def get(kind, name, namespace):
            if kind == "configmap":
                return {"data": {"DF_DEPLOYMENT_EXECUTION_ENABLED": "false", "DF_DEPLOYMENT_CLEANUP_ENABLED": "false"}}
            return {"metadata": {"labels": {"app.kubernetes.io/part-of": "deployflow-platform"}}}

        with patch.object(remote, "check_collisions"), patch.object(remote, "list_objects", return_value=[existing]), \
                patch.object(remote, "get", side_effect=get), patch.object(remote, "completed_job"), \
                patch.object(remote, "query", return_value="BEGIN\n0\nCOMMIT\n"), \
                patch.object(remote, "kubectl") as kube, contextlib.redirect_stdout(io.StringIO()):
            remote.apply("workloads", document, namespace=True)
        writes = [call for call in kube.call_args_list if call.args[0] == "apply"]
        self.assertEqual(len(writes), 12)
        self.assertEqual([call.kwargs["value"] for call in writes], document["items"])

    def test_partial_apply_cannot_overwrite_a_different_platform_release(self):
        existing = copy.deepcopy(render("workloads", IMAGES)["items"][0])
        previous = copy.deepcopy(existing)
        previous["spec"]["template"]["spec"]["containers"][0]["image"] = "different-image"
        existing["metadata"]["annotations"] = {
            "kubectl.kubernetes.io/last-applied-configuration": json.dumps(previous)}
        with patch.object(remote, "check_collisions"), patch.object(remote, "list_objects", return_value=[existing]), \
                patch.object(remote, "kubectl") as kube, self.assertRaises(ValueError):
            remote.apply("workloads", render("workloads", IMAGES), namespace=True)
        kube.assert_not_called()

    def test_raw_kubernetes_failures_never_print_secret_values(self):
        result = subprocess.CompletedProcess([], 1, "", "synthetic-private-value")
        with patch.object(remote.subprocess, "run", return_value=result), self.assertRaises(ValueError) as caught:
            remote.kubectl("create", "-f", "-", value={"data": "synthetic-private-value"})
        self.assertNotIn("synthetic-private-value", str(caught.exception))

    def test_cutover_checks_snapshot_and_fences_delete_by_uid_and_version(self):
        old = {"uid": "old-uid", "resourceVersion": "42", "restore": {"spec": {}}}
        new = render("ingress", IMAGES)
        request = new | {"previous": old}
        calls = []

        def kube(*args, value=None):
            calls.append((args, value))
            if args[:2] == ("get", "ingress"):
                return json.dumps({"items": [{"metadata": {"uid": "old-uid"}, "spec": {"rules": [{"host": remote.HOST}]}}]})
            return ""

        with patch.object(remote, "capture_host", return_value=old), patch.object(remote, "check_collisions"), \
                patch.object(remote, "kubectl", side_effect=kube), contextlib.redirect_stdout(io.StringIO()):
            remote.switch_host(request, namespace=True)
        deletion = next(value for args, value in calls if args[0] == "delete")
        self.assertEqual(deletion["preconditions"], {"uid": "old-uid", "resourceVersion": "42"})
        self.assertEqual([args[0] for args, value in calls].count("delete"), 1)
        writes = [(args[0], value) for args, value in calls if args[0] in {"apply", "delete"}]
        self.assertEqual([verb for verb, value in writes], ["apply", "delete", "apply"])
        self.assertEqual(writes[0][1], new["items"][0])
        self.assertEqual(writes[-1][1], new["items"][1])
        with patch.object(remote, "capture_host", return_value=old | {"uid": "replaced"}), \
                patch.object(remote, "kubectl") as kube, self.assertRaises(ValueError):
            remote.switch_host(request, namespace=True)
        kube.assert_not_called()

    def test_cutover_does_not_interrupt_old_route_if_solver_policy_fails(self):
        old = {"uid": "old-uid", "resourceVersion": "42"}
        request = render("ingress", IMAGES) | {"previous": old}
        calls = []

        def kube(*args, value=None):
            calls.append(args)
            if args[0] == "get":
                return json.dumps({"items": []})
            if args[0] == "apply":
                raise ValueError("Synthetic policy apply failure")
            return ""

        with patch.object(remote, "capture_host", return_value=old), patch.object(remote, "check_collisions"), \
                patch.object(remote, "kubectl", side_effect=kube), self.assertRaises(ValueError):
            remote.switch_host(request, namespace=True)
        self.assertFalse(any(args[0] == "delete" for args in calls))

    def test_cutover_refuses_missing_or_reordered_solver_policy_before_writes(self):
        old = {"uid": "old-uid", "resourceVersion": "42"}
        document = render("ingress", IMAGES)
        for items in (document["items"][1:], list(reversed(document["items"]))):
            with patch.object(remote, "capture_host", return_value=old), \
                    patch.object(remote, "kubectl") as kube, self.assertRaises(ValueError):
                remote.switch_host({"items": items, "previous": old}, namespace=True)
            kube.assert_not_called()

    def test_restore_fences_only_platform_ingress_and_recreates_saved_mockup(self):
        saved = {"kind": "Ingress", "metadata": {"name": "deployflow-mockup", "namespace": "deployflow-mockup"},
                 "spec": {"rules": [{"host": remote.HOST}]}}
        request = {"previous": {"restore": saved}, "platform_ingress_uid": "new-uid"}
        current = {"metadata": {"uid": "new-uid", "resourceVersion": "52",
                   "labels": {"app.kubernetes.io/part-of": "deployflow-platform"}}}
        with patch.object(remote, "get", side_effect=[None, current]), patch.object(remote, "kubectl") as kube, \
                contextlib.redirect_stdout(io.StringIO()):
            remote.restore_host(request)
        calls = kube.call_args_list
        self.assertEqual(calls[0].kwargs["value"]["preconditions"], {"uid": "new-uid", "resourceVersion": "52"})
        self.assertEqual(calls[-1].args, ("create", "-f", "-"))
        self.assertEqual(calls[-1].kwargs["value"], saved)

    def test_restore_refuses_replaced_ingress_and_existing_mockup(self):
        saved = {"kind": "Ingress", "metadata": {"name": "deployflow-mockup", "namespace": "deployflow-mockup"},
                 "spec": {"rules": [{"host": remote.HOST}]}}
        request = {"previous": {"restore": saved}, "platform_ingress_uid": "new-uid"}
        for resources in ([{"metadata": {}}], [None, {"metadata": {"uid": "another", "labels": {}}}]):
            with patch.object(remote, "get", side_effect=resources), patch.object(remote, "kubectl") as kube, \
                    self.assertRaises(ValueError):
                remote.restore_host(request)
            kube.assert_not_called()

    def test_restore_after_failed_create_requires_explicit_absence_approval(self):
        saved = {"kind": "Ingress", "metadata": {"name": "deployflow-mockup", "namespace": "deployflow-mockup"},
                 "spec": {"rules": [{"host": remote.HOST}]}}
        request = {"previous": {"restore": saved}, "platform_ingress_uid": "absent"}
        with patch.object(remote, "get", side_effect=[None, None]), patch.object(remote, "kubectl") as kube, \
                contextlib.redirect_stdout(io.StringIO()):
            remote.restore_host(request)
        kube.assert_called_once_with("create", "-f", "-", value=saved)

    def test_preview_does_not_even_start_ssh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release.json"
            path.write_bytes(encoded(create(IMAGES, SOURCE)))
            with patch("sys.argv", ["operate.py", "--stage", "foundation", "--bundle", str(path),
                                    "--key", "unused", "--known-hosts", "unused"]), \
                    patch("operate.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
                from operate import main
                main()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
