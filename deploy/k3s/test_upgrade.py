"""Image-only upgrade safety tests; synthetic objects and private temporary dirs."""

import base64
import copy
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import upgrade
import upgrade_remote as remote
from bundle import create, keycloak_version
from check_publication import check_main
from render import IMAGE_REPOSITORIES, render, runtime
from upgrade_protocol import (
    NS_UID,
    SOURCE_ANNOTATION,
    TARGET_UID,
    contract,
    deployment_snapshot,
    digest,
    image_patch,
    make_plan,
)

OLD = {role: repo + "@sha256:" + str(i) * 64 for i, (role, repo) in enumerate(IMAGE_REPOSITORIES.items(), 1)}
NEW = {role: repo + "@sha256:" + c * 64 for c, (role, repo) in zip("abc", IMAGE_REPOSITORIES.items())}
SOURCE = "d" * 40


def fixtures():
    items = render("workloads", OLD)["items"]
    for item in items:
        item["metadata"].update(uid="uid-" + item["metadata"]["name"], generation=1, annotations={"retained": "yes"})
    state = {"namespace_uid": NS_UID, "target_namespace_uid": TARGET_UID, "schema_revision": "c3a71d9e4208",
             "keycloak_version": "26.8.0", "keycloak_version_observed": True,
             "schema_fingerprints": {"application": "schema-a", "identity": "schema-i"},
             "pending_jobs": {"analysis": 0, "analysis_runs": 0, "github": 0, "delivery": 0, "execution": 0},
             "deployments": {i["metadata"]["name"]: deployment_snapshot(i) for i in items},
             "protected": {"configmaps/deployflow-config": {"uid": "config", "sha256": "preserved"}},
             "flags": {"DF_DEPLOYMENT_EXECUTION_ENABLED": "true", "DF_DEPLOYMENT_CLEANUP_ENABLED": "true"},
             "application_policies": [{"application_id": "synthetic", "enabled": True}], "applications": []}
    return items, state, upgrade.release_metadata(create(NEW, SOURCE))


def apply_json_patch(value, operations):
    value = copy.deepcopy(value)
    for op in operations:
        keys = [k.replace("~1", "/").replace("~0", "~") for k in op["path"].split("/")[1:]]
        parent = value
        for key in keys[:-1]:
            parent = parent[int(key)] if isinstance(parent, list) else parent[key]
        key = int(keys[-1]) if isinstance(parent, list) else keys[-1]
        if op["op"] == "test":
            if parent[key] != op["value"]:
                raise ValueError("Optimistic patch conflict")
        elif op["op"] in {"add", "replace"}:
            parent[key] = copy.deepcopy(op["value"])
        else:
            raise AssertionError("Unexpected mutation verb")
    return value


class UpgradePlanTests(unittest.TestCase):
    def test_older_main_workflow_rerun_cannot_select_an_old_release(self):
        check_main(SOURCE, SOURCE)
        with self.assertRaises(ValueError):
            check_main(SOURCE, "e" * 40)

    def test_optional_cleaner_is_preserved_and_upgraded_without_enabling_capabilities(self):
        _, state, release = fixtures()
        cleaner = runtime("kube-cleaner", OLD["backend"],
                          ["dramatiq", "app.worker", "--processes", "1", "--threads", "1", "--queues", "cleanup"], identity=True)
        cleaner["metadata"].update(uid="uid-cleaner", generation=1)
        state["deployments"]["kube-cleaner"] = deployment_snapshot(cleaner)
        plan = make_plan(state, release)
        self.assertEqual(len(plan["changes"]), 13)
        change = next(c for c in plan["changes"] if c["name"] == "kube-cleaner")
        self.assertEqual(set(change["to_images"].values()), {NEW["backend"]})

    def test_snapshot_hashes_private_inline_configuration_without_exporting_it(self):
        items, _, _ = fixtures()
        items[0]["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "EXAMPLE_PRIVATE", "value": "synthetic-private-marker"})
        self.assertNotIn("synthetic-private-marker", json.dumps(deployment_snapshot(items[0])))

    def test_patch_changes_only_images_and_source_annotation_preserving_live_flags_and_settings(self):
        items, state, release = fixtures()
        plan = make_plan(state, release)
        self.assertEqual(len(plan["changes"]), 12)
        self.assertEqual(plan["before"]["flags"], state["flags"])
        self.assertEqual(plan["before"]["application_policies"], state["application_policies"])
        for item in items:
            change = next(x for x in plan["changes"] if x["name"] == item["metadata"]["name"])
            result = apply_json_patch(item, image_patch(item, change))
            self.assertEqual(contract(result["spec"]), contract(item["spec"]))
            self.assertEqual(result["spec"]["replicas"], item["spec"]["replicas"])
            self.assertEqual(result["metadata"]["annotations"]["retained"], "yes")
            self.assertEqual(result["metadata"]["annotations"][SOURCE_ANNOTATION], SOURCE)
            self.assertEqual(deployment_snapshot(result)["images"], change["to_images"])

    def test_known_api_defaults_and_restart_annotations_match_but_unknown_settings_do_not(self):
        items, _, _ = fixtures()
        spec = copy.deepcopy(items[0]["spec"])
        live = copy.deepcopy(spec)
        live.update(progressDeadlineSeconds=600, revisionHistoryLimit=10)
        p = live["template"]["spec"]
        p.update(dnsPolicy="ClusterFirst", restartPolicy="Always", schedulerName="default-scheduler", serviceAccount=p["serviceAccountName"])
        p["containers"][0].update(terminationMessagePath="/dev/termination-log", terminationMessagePolicy="File")
        p["containers"][0]["ports"][0]["protocol"] = "TCP"
        live["template"]["metadata"]["annotations"] = {"kubectl.kubernetes.io/restartedAt": "synthetic"}
        self.assertEqual(contract(spec), contract(live))
        p["hostNetwork"] = True
        self.assertNotEqual(contract(spec), contract(live))

    def test_stable_plan_digest_ignores_observation_clock_but_changes_for_authority_and_image_drift(self):
        _, state, release = fixtures()
        first = digest(make_plan(state, release))
        self.assertEqual(first, digest(make_plan(copy.deepcopy(state), copy.deepcopy(release))))
        state["application_policies"][0]["enabled"] = False
        self.assertNotEqual(first, digest(make_plan(state, release)))

    def test_pending_work_schema_identity_and_contract_changes_are_rejected(self):
        _, base, release = fixtures()
        changes = [lambda s: s.update(namespace_uid="replacement"),
                   lambda s: s.update(schema_revision="future"), lambda s: s.update(keycloak_version="27.0.0"),
                   lambda s: s.update(keycloak_version_observed=False),
                   lambda s: s["deployments"]["api"].update(contract_sha256="changed")]
        changes.extend(lambda s, kind=k: s["pending_jobs"].update({kind: 1}) for k in base["pending_jobs"])
        for change in changes:
            state = copy.deepcopy(base)
            change(state)
            with self.assertRaises(ValueError):
                make_plan(state, release)

    def test_unowned_unknown_missing_or_foreign_images_cannot_be_upgraded(self):
        items, state, release = fixtures()
        for mutate in (lambda i: i["metadata"].update(labels={}),
                       lambda i: i["metadata"].update(namespace="other"),
                       lambda i: i["spec"]["template"]["spec"]["containers"][0].update(image="other:latest")):
            item = copy.deepcopy(items[0])
            mutate(item)
            with self.assertRaises(ValueError):
                deployment_snapshot(item)
        state["deployments"].pop("api")
        with self.assertRaises(ValueError):
            make_plan(state, release)

    def test_uid_generation_and_annotation_fences_reject_intervening_changes(self):
        items, state, release = fixtures()
        change = make_plan(state, release)["changes"][0]
        item = next(i for i in items if i["metadata"]["name"] == change["name"])
        for mutate in (lambda i: i["metadata"].update(uid="replaced"),
                       lambda i: i["metadata"].update(generation=2),
                       lambda i: i["metadata"]["annotations"].update(intervening="change"),
                       lambda i: i["spec"].update(replicas=2)):
            altered = copy.deepcopy(item)
            mutate(altered)
            with self.assertRaises(ValueError):
                image_patch(altered, change)

    def test_completed_or_partial_upgrade_rolls_back_to_exact_recorded_images(self):
        items, before, release = fixtures()
        plan = make_plan(before, release)
        targets = {name: {"images": row["images"]} for name, row in before["deployments"].items()}
        after = copy.deepcopy(before)
        for change in plan["changes"]:
            targets[change["name"]]["images"] = change["to_images"]
        origin = {"id": digest(plan), "before": before, "targets": targets, "release": release}
        for count in (1, 12):
            for item in items[:count]:
                name = item["metadata"]["name"]
                change = next(c for c in plan["changes"] if c["name"] == name)
                after["deployments"][name] = deployment_snapshot(apply_json_patch(item, image_patch(item, change)))
            rollback = make_plan(after, release, origin=origin)
            self.assertEqual(rollback["operation"], "rollback")
            for change in rollback["changes"]:
                self.assertEqual(change["to_images"], before["deployments"][change["name"]]["images"])
                self.assertIsNone(change["to_source"])
        after["schema_fingerprints"]["identity"] = "migrated"
        with self.assertRaises(ValueError):
            make_plan(after, release, origin=origin)

    def test_rollback_will_not_overwrite_a_later_upgrade(self):
        _, before, release = fixtures()
        origin = {"id": "a" * 64, "before": before, "targets": {n: {"images": r["images"]} for n, r in before["deployments"].items()}, "release": release}
        after = copy.deepcopy(before)
        after["deployments"]["api"]["source_sha"] = "f" * 40
        with self.assertRaises(ValueError):
            make_plan(after, release, origin=origin)


class UpgradeRemoteTests(unittest.TestCase):
    def test_registry_requires_readonly_scope_and_exact_index_bytes_with_amd64(self):
        index = json.dumps({"manifests": [{"digest": "sha256:" + "f" * 64,
                            "platform": {"os": "linux", "architecture": "amd64"}}]}).encode()
        sha = hashlib.sha256(index).hexdigest()
        reference = IMAGE_REPOSITORIES["backend"] + "@sha256:" + sha
        config = json.dumps({"auths": {"ghcr.io": {"auth": base64.b64encode(b"synthetic:synthetic-only").decode()}}}).encode()
        secret = {"data": {".dockerconfigjson": base64.b64encode(config).decode()}}

        def fetch(url, *args):
            if url.endswith("/user"):
                return b"{}", {"X-OAuth-Scopes": "read:packages"}
            if "/token?" in url:
                return b'{"token":"synthetic-scoped-pull-only"}', {}
            return index, {"Docker-Content-Digest": "sha256:" + sha}

        with patch.object(remote, "get", return_value=secret), patch.object(remote, "fetch", side_effect=fetch):
            result = remote.verify_images([reference])
            self.assertIn(reference, result[reference])
            self.assertIn(IMAGE_REPOSITORIES["backend"] + "@sha256:" + "f" * 64, result[reference])
            with self.assertRaises(ValueError):
                remote.verify_images([reference[:-1] + ("0" if reference[-1] != "0" else "1")])
        with patch.object(remote, "get", return_value=secret), \
                patch.object(remote, "fetch", return_value=(b"{}", {"X-OAuth-Scopes": "read:packages, write:packages"})), self.assertRaises(ValueError):
            remote.verify_images([reference])

    def environment(self, items, baseline):
        class Environment:
            def __init__(self):
                self.objects = {i["metadata"]["name"]: copy.deepcopy(i) for i in items}
                self.commands = []
                self.fail_rollout = False

            def snapshot(self, **_):
                result = copy.deepcopy(baseline)
                result["deployments"] = {name: deployment_snapshot(i) for name, i in self.objects.items()}
                return result

            def kube(self, *args, **_):
                self.commands.append(args)
                if args[0] == "patch":
                    name = args[2]
                    self.objects[name] = apply_json_patch(self.objects[name], json.loads(args[-1]))
                    self.objects[name]["metadata"]["generation"] += 1
                elif args[0] == "rollout" and self.fail_rollout:
                    raise ValueError("synthetic rollout failure")
                return ""

            def rows(self, kind):
                if kind == "replicasets":
                    return [{"metadata": {"uid": "rs-" + name,
                             "ownerReferences": [{"uid": i["metadata"]["uid"], "controller": True}]}}
                            for name, i in self.objects.items()]
                if kind == "pods":
                    return [{"metadata": {"ownerReferences": [{"kind": "ReplicaSet", "uid": "rs-" + name, "controller": True}]},
                             "status": {field: [{"name": c["name"], "imageID": c["image"], "ready": True}
                                                for c in i["spec"]["template"]["spec"].get(group, [])]
                                        for group, field in (("containers", "containerStatuses"), ("initContainers", "initContainerStatuses"))}}
                            for name, i in self.objects.items()]
                raise AssertionError("Unexpected resource read")

        return Environment()

    def test_orchestrator_persists_upgrade_partial_failure_and_explicit_rollback_history(self):
        from contextlib import ExitStack

        items, before, release = fixtures()
        env = self.environment(items, before)
        plan = make_plan(before, release)
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            stack.enter_context(patch.object(remote, "private_base", return_value=Path(tmp)))
            stack.enter_context(patch.object(remote, "snapshot", side_effect=env.snapshot))
            stack.enter_context(patch.object(remote, "get", side_effect=lambda kind, name: copy.deepcopy(env.objects[name])))
            stack.enter_context(patch.object(remote, "rows", side_effect=env.rows))
            stack.enter_context(patch.object(remote, "kube", side_effect=env.kube))
            stack.enter_context(patch.object(remote, "protected", return_value=before["protected"]))
            stack.enter_context(patch.object(remote, "query", return_value=[{"name": "applications"}]))
            stack.enter_context(patch.object(remote, "backup", return_value=[{"validation": "synthetic-only archive result"}]))
            stack.enter_context(patch.object(remote, "verify_images", side_effect=lambda refs: {ref: {ref} for ref in refs}))
            stack.enter_context(patch.object(remote, "public_checks", return_value={"/": 200}))
            fence = stack.enter_context(patch.object(remote, "WriteFence"))
            fence.return_value.__enter__.return_value.remaining.return_value = 60
            env.fail_rollout = True
            with self.assertRaises(ValueError):
                remote.execute({"operation": "apply", "release": release, "approval": digest(plan)})
            original = remote.load_receipt(digest(plan))
            self.assertEqual(original["status"], "failed")
            self.assertEqual(len(original["applied"]), 12)
            self.assertTrue(remote.unfinished())
            # A failed rollout leaves its images/history intact. No automatic
            # inverse patch is issued; a fresh rollback preview is required.
            self.assertEqual(len([a for a in env.commands if a[0] == "patch"]), 12)
            preview = remote.execute({"operation": "rollback-preview", "receipt_id": original["id"]})
            self.assertEqual(preview["plan"]["operation"], "rollback")
            env.fail_rollout = False
            result = remote.execute({"operation": "rollback-apply", "receipt_id": original["id"], "approval": preview["plan_sha256"]})
            self.assertEqual(result["status"], "succeeded")
            self.assertFalse(remote.unfinished())
            self.assertEqual(remote.load_receipt(original["id"])["reverted_by"], result["receipt_id"])
            for name, current in env.snapshot()["deployments"].items():
                self.assertEqual(current["images"], before["deployments"][name]["images"])
                self.assertEqual(current["source_sha"], before["deployments"][name]["source_sha"])
            # Replayed original approval cannot silently re-upgrade after rollback.
            count = len(env.commands)
            with self.assertRaises(ValueError):
                remote.execute({"operation": "apply", "release": release, "approval": digest(plan)})
            self.assertEqual(len(env.commands), count)

    def test_preview_performs_no_writes_backups_locks_or_registry_credential_reads(self):
        _, state, release = fixtures()
        with patch.object(remote, "snapshot", return_value=state), patch.object(remote, "unfinished", return_value=[]), \
                patch.object(remote, "private_base") as files, patch.object(remote, "backup") as backup, \
                patch.object(remote, "verify_images") as registry, patch.object(remote, "kube") as kube:
            result = remote.execute({"operation": "preview", "release": release})
        self.assertEqual(result["plan_sha256"], digest(result["plan"]))
        for mocked in (files, backup, registry, kube):
            mocked.assert_not_called()

    def test_wrong_or_stale_approval_performs_no_mutation(self):
        _, state, release = fixtures()
        with patch.object(remote, "snapshot", return_value=state), patch.object(remote, "unfinished", return_value=[]), \
                patch.object(remote, "private_base") as files, self.assertRaises(ValueError):
            remote.execute({"operation": "apply", "release": release, "approval": "0" * 64})
        files.assert_not_called()

    def test_unfinished_attempt_blocks_new_upgrade(self):
        _, _, release = fixtures()
        with patch.object(remote, "unfinished", return_value=["pending"]), patch.object(remote, "snapshot") as snapshot, self.assertRaises(ValueError):
            remote.execute({"operation": "preview", "release": release})
        snapshot.assert_not_called()

    def test_invalid_receipt_path_cannot_read_arbitrary_server_files(self):
        for identifier in ("../credentials", "/etc/passwd", "a" * 63):
            with self.assertRaises(ValueError):
                remote.receipt_path(identifier)

    def test_backup_failure_preserves_journal_without_patching_or_automatic_rollback(self):
        _, state, release = fixtures()
        plan = make_plan(state, release)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch.object(remote, "snapshot", return_value=state), patch.object(remote, "unfinished", return_value=[]), \
                    patch.object(remote, "private_base", return_value=base), patch.object(remote, "query", return_value=[{"name": "applications"}]), \
                    patch.object(remote, "WriteFence"), patch.object(remote, "backup", side_effect=ValueError("synthetic failure")), \
                    patch.object(remote, "kube") as kube, self.assertRaises(ValueError) as caught:
                remote.execute({"operation": "apply", "release": release, "approval": digest(plan)})
            self.assertEqual(caught.exception.receipt_id, digest(plan))
            journal = json.loads((base / digest(plan) / "receipt.json").read_text())
            self.assertEqual(journal["status"], "failed")
            self.assertEqual(journal["applied"], [])
            self.assertEqual((base / digest(plan) / "receipt.json").stat().st_mode & 0o777, 0o600)
            kube.assert_not_called()

    def test_cli_requires_explicit_confirmation_and_never_overwrites_review_output(self):
        _, _, release = fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "release.json"
            bundle.write_text(json.dumps(create(NEW, SOURCE)))
            args = ["upgrade.py", "--operation", "apply", "--bundle", str(bundle), "--source-sha", SOURCE,
                    "--output", str(root / "result.json"), "--key", "unused", "--known-hosts", "unused"]
            with patch("sys.argv", args), patch.object(upgrade.subprocess, "run") as execute, \
                    patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                upgrade.main()
            execute.assert_not_called()
            response = {"plan": make_plan(fixtures()[1], release)}
            response["plan_sha256"] = digest(response["plan"])
            args[2] = "preview"
            with patch("sys.argv", args), patch.object(upgrade.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(response).encode(), b"")), \
                    patch("sys.stdout", io.StringIO()):
                upgrade.main()
            self.assertEqual((root / "result.json").stat().st_mode & 0o777, 0o600)
            with patch("sys.argv", args), patch.object(upgrade.subprocess, "run") as execute, \
                    patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                upgrade.main()
            execute.assert_not_called()

    def test_source_binding_and_keycloak_version_are_part_of_publication_bundle(self):
        self.assertEqual(keycloak_version(), "26.8.0")
        self.assertEqual(create(NEW, SOURCE)["keycloak_version"], "26.8.0")
        value = create(NEW, SOURCE)
        value["keycloak_version"] = "untrusted"
        with self.assertRaises(ValueError):
            upgrade.release_metadata(value)


if __name__ == "__main__":
    unittest.main()
