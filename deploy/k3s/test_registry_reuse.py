"""Synthetic-only checks for server-side credential reuse; never run real SSH."""

import base64
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import reuse_registry_secret as reuse
from operate import secret_document
from prepare_secrets import prepare

PASSWORD = "synthetic-only-credential"


def source():
    config = {"auths": {"ghcr.io": {"auth": base64.b64encode(("synthetic:" + PASSWORD).encode()).decode()},
                        "unrelated.example": {"auth": "do-not-copy"}}}
    return {"type": "kubernetes.io/dockerconfigjson", "data": {
        ".dockerconfigjson": base64.b64encode(json.dumps(config).encode()).decode()}}


def fetch(url, authorization, accept="application/json"):
    if url == "https://api.github.com/user":
        return b"{}", {"X-OAuth-Scopes": "read:packages"}
    if url.startswith("https://ghcr.io/token?"):
        return b'{"token":"synthetic-scoped-bearer"}', {}
    digest = url.rsplit("sha256:", 1)[1]
    return b'{"manifests":[{"platform":{"os":"linux","architecture":"amd64"}}]}', {
        "Docker-Content-Digest": "sha256:" + digest}


class RegistryReuseTests(unittest.TestCase):
    def test_real_create_acknowledgement_is_not_parsed_as_json(self):
        for acknowledgement in ("namespace/deployflow-system created\n", "secret/deployflow-ghcr created\n"):
            with patch.object(reuse.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, acknowledgement, "")):
                self.assertIsNone(reuse.kubectl("create", "-f", "-", value={"kind": "synthetic"}))

    def test_get_json_is_still_parsed_and_missing_get_is_none(self):
        with patch.object(reuse.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"items": []}', "")):
            self.assertEqual(reuse.kubectl("get", "pods", "-o", "json"), {"items": []})
        with patch.object(reuse.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            self.assertIsNone(reuse.kubectl("get", "secret", "missing", "--ignore-not-found", "-o", "json"))

    def test_full_apply_and_retry_with_created_namespace_accept_kubectl_plain_text(self):
        for namespace_exists in (False, True):
            writes = []
            namespace = {"metadata": {"labels": {"app.kubernetes.io/part-of": "deployflow-platform"}}}

            def command(args, *, writes=writes, namespace=namespace, namespace_exists=namespace_exists, **kwargs):
                verb, *arguments = args[5:]
                if verb == "create":
                    document = json.loads(kwargs["input"])
                    writes.append(document)
                    output = document["kind"].lower() + "/" + document["metadata"]["name"] + " created\n"
                elif arguments[:2] == ["namespace", "deployflow-test"]:
                    output = json.dumps({"metadata": {"uid": "84a720fd-dc36-4592-a2bb-12c8f54ddc7c"}})
                elif arguments[:2] == ["namespace", "deployflow-system"]:
                    output = json.dumps(namespace) if namespace_exists else ""
                elif arguments[0] == "deployments,statefulsets,jobs,pods":
                    output = '{"items": []}'
                elif arguments[:2] == ["secret", "ghcr-pull-secret"]:
                    output = json.dumps(source())
                elif arguments[:2] == ["secret", "deployflow-ghcr"]:
                    output = ""
                else:
                    self.fail("Unexpected kubectl command")
                return subprocess.CompletedProcess(args, 0, output, "")

            with self.subTest(namespace_exists=namespace_exists), \
                    patch.object(reuse.socket, "gethostname", return_value="homeserver1"), \
                    patch.object(reuse.subprocess, "run", side_effect=command), \
                    patch.object(reuse, "fetch", side_effect=fetch), contextlib.redirect_stdout(io.StringIO()):
                reuse.run(True)
            self.assertEqual([value["kind"] for value in writes],
                             ["Secret"] if namespace_exists else ["Namespace", "Secret"])

    def test_readonly_scope_and_all_three_images_verified_without_credential_output(self):
        output = io.StringIO()
        with patch.object(reuse, "fetch", side_effect=fetch) as request, contextlib.redirect_stdout(output):
            desired = reuse.pull_secret(source())
        self.assertEqual(request.call_count, 7)
        config = json.loads(base64.b64decode(desired["data"][".dockerconfigjson"]))
        self.assertEqual(set(config["auths"]), {"ghcr.io"})
        self.assertNotIn(PASSWORD, output.getvalue())
        self.assertNotIn("synthetic-scoped-bearer", output.getvalue())
        self.assertEqual(desired["metadata"]["namespace"], "deployflow-system")

    def test_write_scopes_and_unknown_scopes_are_rejected_before_registry_use(self):
        for headers in ({"X-OAuth-Scopes": "read:packages, write:packages"}, {}):
            with patch.object(reuse, "fetch", return_value=(b"{}", headers)) as request, self.assertRaises(ValueError):
                reuse.pull_secret(source())
            self.assertEqual(request.call_count, 1)

    def test_unexpected_manifest_digest_is_rejected(self):
        def bad(url, authorization, accept="application/json"):
            value, headers = fetch(url, authorization, accept)
            if "/manifests/" in url:
                headers = {"Docker-Content-Digest": "sha256:" + "0" * 64}
            return value, headers
        with patch.object(reuse, "fetch", side_effect=bad), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            reuse.pull_secret(source())

    def run_new(self, apply):
        target = {"metadata": {"uid": "84a720fd-dc36-4592-a2bb-12c8f54ddc7c"}}
        output = io.StringIO()
        with patch.object(reuse.socket, "gethostname", return_value="homeserver1"), \
                patch.object(reuse, "get", side_effect=[target, None, source()]), \
                patch.object(reuse, "fetch", side_effect=fetch), patch.object(reuse, "kubectl") as kube, \
                contextlib.redirect_stdout(output):
            reuse.run(apply)
        return kube.call_args_list, output.getvalue()

    def test_preview_creates_nothing(self):
        calls, output = self.run_new(False)
        self.assertEqual(calls, [])
        self.assertIn("NO CHANGES", output)

    def test_apply_only_creates_namespace_and_one_credential(self):
        calls, output = self.run_new(True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call.args == ("create", "-f", "-") for call in calls))
        self.assertEqual([call.kwargs["value"]["kind"] for call in calls], ["Namespace", "Secret"])
        self.assertEqual(calls[0].kwargs["value"]["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "restricted")
        self.assertNotIn(PASSWORD, output)

    def test_pending_workloads_stop_before_credential_use(self):
        target = {"metadata": {"uid": "84a720fd-dc36-4592-a2bb-12c8f54ddc7c"}}
        namespace = {"metadata": {"labels": {"app.kubernetes.io/part-of": "deployflow-platform"}}}
        with patch.object(reuse.socket, "gethostname", return_value="homeserver1"), \
                patch.object(reuse, "get", side_effect=[target, namespace]), \
                patch.object(reuse, "kubectl", return_value={"items": [{"kind": "Deployment"}]}), \
                patch.object(reuse, "pull_secret") as credential, self.assertRaises(ValueError):
            reuse.run(True)
        credential.assert_not_called()

    def test_existing_destination_secret_cannot_be_overwritten(self):
        target = {"metadata": {"uid": "84a720fd-dc36-4592-a2bb-12c8f54ddc7c"}}
        namespace = {"metadata": {"labels": {"app.kubernetes.io/part-of": "deployflow-platform"}}}
        desired = {"type": "kubernetes.io/dockerconfigjson", "data": {".dockerconfigjson": "new"}}
        current = {"metadata": namespace["metadata"], "type": desired["type"], "data": {".dockerconfigjson": "old"}}
        with patch.object(reuse.socket, "gethostname", return_value="homeserver1"), \
                patch.object(reuse, "get", side_effect=[target, namespace, source(), current]), \
                patch.object(reuse, "pull_secret", return_value=desired), \
                patch.object(reuse, "kubectl", return_value={"items": []}) as kube, self.assertRaises(ValueError):
            reuse.run(True)
        self.assertTrue(all(call.args[0] == "get" for call in kube.call_args_list))

    def test_local_runtime_secrets_do_not_need_a_copy_of_the_registry_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o700)
            key = root / "app.pem"
            key.write_text("-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----")
            key.chmod(0o600)
            output = root / "runtime"
            prepare(output, key)
            original = {path.name: path.read_bytes() for path in output.iterdir()}
            items = secret_document(output, registry_on_cluster=True)["items"]
            self.assertEqual(len(items), 7)
            self.assertNotIn("deployflow-ghcr", [item["metadata"]["name"] for item in items])
            self.assertEqual(original, {path.name: path.read_bytes() for path in output.iterdir()})


if __name__ == "__main__":
    unittest.main()
