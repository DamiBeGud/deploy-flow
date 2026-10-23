"""Offline provenance, policy and two-phase recovery tests. No live credentials."""

import copy
import datetime
import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import automatic
import automatic_candidate
import public_checks
import test_upgrade
import upgrade_remote as remote
from bundle import create
from github_proof import REPO, GitHubProof
from prepare_automatic_policy import prepare
from test_upgrade import NEW, SOURCE, fixtures
from upgrade_protocol import digest, make_plan, verification_plan

CONTEXT = {"run_id": 123, "attempt": 1, "source_sha": SOURCE, "workflow": ".github/workflows/platform-upgrade.yml"}


class ManualEntryTests(unittest.TestCase):
    def test_remote_entry_dispatches_recovery_and_status_after_identity_check(self):
        for operation in ("verify-preview", "verify-apply", "finalize", "status"):
            request = {"operation": operation, "receipt_id": "a" * 64}
            with self.subTest(operation=operation), patch("sys.stdin", io.StringIO(json.dumps(request))), \
                    patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(remote, "identity") as identity, \
                    patch.object(remote, "execute", return_value={"accepted": operation}) as execute:
                execute.side_effect = lambda value, op=operation: {"accepted": op} if identity.called else self.fail("Identity not checked")
                remote.main()
                execute.assert_called_once_with(request)
                self.assertEqual(json.loads(output.getvalue()), {"accepted": operation})

    def test_manual_entry_rejects_automatic_operation(self):
        with patch("sys.stdin", io.StringIO('{"operation":"auto-apply"}')), patch.object(remote, "identity") as identity:
            with self.assertRaisesRegex(ValueError, "Unknown upgrade operation"):
                remote.main()
            identity.assert_not_called()


class ProofTests(unittest.TestCase):
    def controller(self):
        return {"repository": {"full_name": REPO}, "head_branch": "main", "head_sha": SOURCE,
                "path": CONTEXT["workflow"], "run_attempt": 1, "event": "workflow_dispatch"}

    def test_public_job_requires_authoritative_exact_workflow_source_attempt_and_success(self):
        proof = GitHubProof("synthetic-only")
        run = self.controller()
        jobs = {"jobs": [{"id": 99, "name": "public-verification", "status": "completed", "conclusion": "success"}]}
        with patch.object(proof, "read", side_effect=[run, jobs]):
            self.assertEqual(proof.public_job(CONTEXT)["job_id"], 99)
        for field, value in (("head_sha", "0" * 40), ("head_branch", "other"), ("path", "other.yml"),
                             ("run_attempt", 2), ("event", "pull_request"), ("repository", {"full_name": "other/repo"})):
            with self.subTest(field=field), patch.object(proof, "read", return_value=run | {field: value}), self.assertRaises(ValueError):
                proof.public_job(CONTEXT)
        for conclusion in (None, "failure", "cancelled", "skipped"):
            bad = copy.deepcopy(jobs)
            bad["jobs"][0]["conclusion"] = conclusion
            with patch.object(proof, "read", side_effect=[run, bad]), self.assertRaises(ValueError):
                proof.public_job(CONTEXT)
        for invalid in (CONTEXT | {"attempt": 0}, CONTEXT | {"source_sha": "main"}, CONTEXT | {"run_id": "123"}):
            with self.assertRaises(ValueError):
                proof.run(invalid)

    def test_cross_host_redirect_strips_authorization(self):
        from urllib.request import Request

        from github_proof import Redirect
        request = Request("https://api.github.com/repos/example/artifact", headers={"Authorization": "Bearer synthetic-only"})
        redirected = Redirect().redirect_request(request, None, 302, "", {}, "https://artifacts.example/file")
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_old_or_missing_public_check_timestamp_cannot_finalize_a_later_rollout(self):
        proof = GitHubProof("synthetic-only")
        now = datetime.datetime.now(datetime.timezone.utc)
        for stamp in (None, (now - datetime.timedelta(hours=1)).isoformat()):
            job = {"id": 99, "name": "public-verification", "status": "completed", "conclusion": "success", "completed_at": stamp}
            with patch.object(proof, "read", side_effect=[self.controller(), {"jobs": [job]}]), self.assertRaises(ValueError):
                proof.public_job(CONTEXT, since=now.isoformat())

    def test_current_main_candidate_does_not_use_older_success_after_latest_failed_or_running(self):
        class Proof:
            status = "completed"
            conclusion = "success"
            def read(self, path):
                if path.startswith("/git/"):
                    return {"object": {"sha": SOURCE}}
                return {"workflow_runs": [{"id": 20, "run_attempt": 2, "status": self.status, "conclusion": self.conclusion},
                                          {"id": 10, "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
            def publication(self, identifier, attempt, source):
                assert (identifier, attempt, source) == (20, 2, SOURCE)
                return create(NEW, SOURCE)
        proof = Proof()
        self.assertEqual(automatic_candidate.select(proof)["publication_attempt"], "2")
        proof.status = "in_progress"
        self.assertEqual(automatic_candidate.select(proof)["eligible"], "false")
        proof.status, proof.conclusion = "completed", "failure"
        self.assertEqual(automatic_candidate.select(proof)["eligible"], "false")

    def test_public_checks_retry_transient_failures_but_never_fake_success(self):
        with patch.object(public_checks, "check_once", side_effect=[urllib.error.URLError("synthetic"), {"/": 200}]) as check:
            waits = []
            self.assertEqual(public_checks.verify(sleep=waits.append), {"/": 200})
            self.assertEqual(check.call_count, 2)
            self.assertEqual(waits, [5])
        with patch.object(public_checks, "check_once", side_effect=urllib.error.URLError("synthetic")), self.assertRaisesRegex(ValueError, "public_verification_failed"):
            public_checks.verify(attempts=2, sleep=lambda _: None)

    def test_public_checks_require_correct_tls_origin_content_and_oidc_issuer(self):
        class Response(io.BytesIO):
            status = 200
            def __init__(self, url, body, kind):
                super().__init__(body)
                self.url, self.headers = url, {"Content-Type": kind}
        def fetch(url, **_):
            if url.endswith("/auth/config"):
                return Response(url, b'{"mode":"oidc"}', "application/json")
            if url.endswith("openid-configuration"):
                return Response(url, json.dumps({"issuer": public_checks.HOST + "/identity/realms/deployflow-local"}).encode(), "application/json")
            return Response(url, b"<html>DeployFlow</html>", "text/html")
        with patch.object(public_checks.urllib.request, "urlopen", side_effect=fetch):
            self.assertEqual(len(public_checks.check_once()), 3)
        with patch.object(public_checks.urllib.request, "urlopen", return_value=Response("https://other.example", b"{}", "text/html")), self.assertRaises(ValueError):
            public_checks.check_once()


class AutomaticAdmissionTests(unittest.TestCase):
    def fixture(self):
        _, state, release = fixtures()
        files = {p: p.encode() for p in automatic.PROTECTED_FILES}
        policy = {"id": "reviewed-policy", "enabled": True, "baseline_sha": "e" * 40,
                  "source_scope": {"mode": "exact", "sha": SOURCE},
                  "schema_revision": release["schema_revision"], "keycloak_version": release["keycloak_version"],
                  "deployment_uids": {name: row["uid"] for name, row in state["deployments"].items()},
                  "infrastructure": automatic.infrastructure_fingerprints(state["protected"]),
                  "approved_files": {p: hashlib.sha256(v).hexdigest() for p, v in files.items()}, "flags": state["flags"]}
        class Proof:
            head = SOURCE
            changed = None
            relation = "ahead"
            conclusion = "success"
            def run(self, context):
                self.context = context
            def read(self, path):
                if path.startswith("/actions/workflows/"):
                    return {"workflow_runs": [{"id": 10, "run_attempt": 2, "status": "completed", "conclusion": self.conclusion}]}
                if path.startswith("/contents/backend/migrations/"):
                    return [{"name": p.rsplit("/", 1)[-1]} for p in automatic.PROTECTED_FILES if p.startswith("backend/migrations/versions/")]
                return {"object": {"sha": self.head}} if path.startswith("/git/") else {"status": self.relation}
            def file(self, path, source):
                return b"modified" if path == self.changed else files[path]
            def publication(self, *_):
                return create(NEW, SOURCE)
        request = {"operation": "auto-apply", "controller": CONTEXT | {"workflow": ".github/workflows/platform-auto.yml"},
                   "publication_run_id": 10, "publication_attempt": 2, "github_token": "synthetic-only"}
        return state, policy, Proof(), request

    def test_disabled_missing_or_unprotected_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            with patch.object(automatic, "POLICY", path), self.assertRaises(FileNotFoundError):
                automatic.load_policy()
            path.write_text('{"enabled":false}')
            path.chmod(0o666)
            with patch.object(automatic, "POLICY", path), self.assertRaises(ValueError):
                automatic.load_policy()

    def test_admission_binds_workflow_current_main_and_every_approved_file(self):
        _, policy, proof, request = self.fixture()
        with tempfile.TemporaryDirectory() as tmp, patch.object(remote, "private_base", return_value=Path(tmp)):
            self.assertEqual(automatic.admit(request, policy, proof)["source_sha"], SOURCE)
            for name in automatic.PROTECTED_FILES:
                proof.changed = name
                with self.subTest(path=name), self.assertRaisesRegex(ValueError, "fresh platform review"):
                    automatic.admit(request, policy, proof)
            proof.changed = None
            proof.head = "f" * 40
            with self.assertRaisesRegex(ValueError, "superseded"):
                automatic.admit(request, policy, proof)
            proof.head, proof.relation = SOURCE, "diverged"
            with self.assertRaises(ValueError):
                automatic.admit(request, policy, proof)
            proof.relation, proof.conclusion = "ahead", "failure"
            with self.assertRaisesRegex(ValueError, "latest current-main"):
                automatic.admit(request, policy, proof)
            proof.conclusion = "success"
            with self.assertRaisesRegex(ValueError, "source scope"):
                automatic.admit(request, policy | {"source_scope": {"mode": "exact", "sha": "f" * 40}}, proof)

    def test_duplicate_or_rolled_back_source_cannot_be_admitted_again(self):
        _, policy, proof, request = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ("a" * 64)).mkdir()
            original = {"plan": {"operation": "upgrade"}, "release": {"source_sha": SOURCE}, "reverted_by": "rollback"}
            with patch.object(remote, "private_base", return_value=Path(tmp)), patch.object(remote, "load_receipt", return_value=original):
                with self.assertRaisesRegex(ValueError, "already_admitted"):
                    automatic.admit(request, policy, proof)
                original["release"]["source_sha"] = "f" * 40
                proof.relation = "identical"
                with self.assertRaisesRegex(ValueError, "older candidate"):
                    automatic.admit(request, policy, proof)

    def test_automatic_gate_preserves_live_flags_and_waits_without_receipts_or_patches(self):
        state, policy, proof, request = self.fixture()
        with patch.object(automatic, "load_policy", return_value=policy), patch.object(automatic, "GitHubProof", return_value=proof), \
                patch.object(automatic, "admit", return_value=fixtures()[2]), patch.object(remote, "snapshot", return_value=state), \
                patch.object(remote, "unfinished", return_value=[]), patch.object(remote, "execute") as execute:
            state["pending_jobs"]["execution"] = 1
            self.assertEqual(automatic.execute(request)["status"], "waiting")
            execute.assert_not_called()
            state["pending_jobs"]["execution"] = 0
            automatic.execute(request)
            admitted = execute.call_args.args[0]
            self.assertEqual(admitted["authorization"]["mode"], "automatic-policy")
            self.assertNotIn("github_token", admitted)
            policy_changed = policy | {"enabled": False}
            with patch.object(automatic, "load_policy", return_value=policy_changed), self.assertRaises(ValueError):
                admitted["_mutation_guard"]()
            with self.assertRaises(ValueError):
                automatic.execute(request | {"approval": "caller-generated"})
            state["deployments"]["api"]["uid"] = "replacement"
            with self.assertRaisesRegex(ValueError, "identity/inventory"):
                automatic.execute(request)

    def test_policy_proposal_is_offline_disabled_bounded_and_hashes_all_controllers(self):
        _, state, release = fixtures()
        plan = make_plan(state, release)
        until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=20)).isoformat()
        policy = prepare(create(NEW, SOURCE), {"plan": plan, "plan_sha256": digest(plan)}, SOURCE, until)
        self.assertFalse(policy["enabled"])
        self.assertEqual(policy["source_scope"], {"mode": "exact", "sha": SOURCE})
        self.assertEqual(set(policy["approved_files"]), set(automatic.PROTECTED_FILES))
        with self.assertRaises(ValueError):
            prepare(create(NEW, SOURCE), {"plan": plan, "plan_sha256": digest(plan)}, "main", until)

    def test_policy_revoked_after_backups_prevents_the_first_real_orchestrator_patch(self):
        state, policy, proof, request = self.fixture()
        env = test_upgrade.UpgradeRemoteTests().environment(fixtures()[0], state)
        reads = 0
        def authorization():
            nonlocal reads
            reads += 1
            return policy if reads == 1 else policy | {"enabled": False}
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            stack.enter_context(patch.object(automatic, "load_policy", side_effect=authorization))
            stack.enter_context(patch.object(automatic, "GitHubProof", return_value=proof))
            stack.enter_context(patch.object(remote, "private_base", return_value=Path(tmp)))
            stack.enter_context(patch.object(remote, "snapshot", side_effect=env.snapshot))
            stack.enter_context(patch.object(remote, "get", side_effect=lambda kind, name: copy.deepcopy(env.objects[name])))
            stack.enter_context(patch.object(remote, "kube", side_effect=env.kube))
            stack.enter_context(patch.object(remote, "query", return_value=[{"name": "applications"}]))
            stack.enter_context(patch.object(remote, "backup", return_value=[{"validation": "synthetic-only"}]))
            stack.enter_context(patch.object(remote, "verify_images", side_effect=lambda refs: {r: {r} for r in refs}))
            stack.enter_context(patch.object(remote, "WriteFence"))
            with self.assertRaisesRegex(ValueError, "revoked"):
                automatic.execute(request)
            self.assertFalse(any(c[0] == "patch" for c in env.commands))
            receipt = next(Path(tmp).glob("*/receipt.json"))
            record = json.loads(receipt.read_text())
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["applied"], [])
            self.assertEqual(record["authorization"]["policy_id"], policy["id"])
            self.assertNotIn("synthetic-only", json.dumps(record["authorization"]))


class VerificationRecoveryTests(unittest.TestCase):
    def environment(self, stack, tmp):
        items, state, release = fixtures()
        env = test_upgrade.UpgradeRemoteTests().environment(items, state)
        stack.enter_context(patch.object(remote, "private_base", return_value=Path(tmp)))
        stack.enter_context(patch.object(remote, "snapshot", side_effect=env.snapshot))
        stack.enter_context(patch.object(remote, "get", side_effect=lambda kind, name: copy.deepcopy(env.objects[name])))
        stack.enter_context(patch.object(remote, "rows", side_effect=env.rows))
        stack.enter_context(patch.object(remote, "kube", side_effect=env.kube))
        stack.enter_context(patch.object(remote, "protected", return_value=state["protected"]))
        stack.enter_context(patch.object(remote, "query", return_value=[{"name": "applications"}]))
        stack.enter_context(patch.object(remote, "backup", return_value=[{"validation": "synthetic-only"}]))
        stack.enter_context(patch.object(remote, "verify_images", side_effect=lambda refs: {r: {r} for r in refs}))
        stack.enter_context(patch.object(remote, "WriteFence"))
        plan = make_plan(state, release)
        result = remote.execute({"operation": "apply", "release": release, "approval": digest(plan), "controller": CONTEXT})
        return env, result

    def test_healthy_rollout_is_not_success_without_independent_proof_and_fresh_recheck(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            env, result = self.environment(stack, tmp)
            self.assertEqual(result["status"], "awaiting_public_verification")
            identifier = result["receipt_id"]
            with patch("github_proof.GitHubProof.public_job", side_effect=ValueError("synthetic failed external check")), self.assertRaises(ValueError):
                remote.finalize({"receipt_id": identifier, "controller": CONTEXT, "github_token": "synthetic-only"})
            self.assertEqual(remote.load_receipt(identifier)["status"], "awaiting_public_verification")
            with patch("github_proof.GitHubProof.public_job", return_value={"job_id": 99}):
                result = remote.finalize({"receipt_id": identifier, "controller": CONTEXT, "github_token": "synthetic-only"})
            self.assertEqual(result["status"], "succeeded")
            self.assertFalse(remote.unfinished())
            with self.assertRaises(ValueError):
                remote.finalize({"receipt_id": identifier, "controller": CONTEXT, "github_token": "synthetic-only"})
            self.assertEqual(len([a for a in env.commands if a[0] == "patch"]), 12)

    def test_failed_receipt_recovery_never_reapplies_and_retains_original_failure(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            env, result = self.environment(stack, tmp)
            original = remote.load_receipt(result["receipt_id"])
            original["status"] = "failed"  # Reconstruct historical failed verifier in this synthetic private fixture only.
            remote.save_receipt(remote.receipt_path(original["id"]), original)
            count = len(env.commands)
            with patch.object(remote, "backup") as backup, patch.object(remote, "WriteFence") as fence:
                preview = remote.execute({"operation": "verify-preview", "receipt_id": original["id"]})
                recovery = remote.execute({"operation": "verify-apply", "receipt_id": original["id"],
                                           "approval": preview["plan_sha256"], "controller": CONTEXT})
                backup.assert_not_called()
                fence.assert_not_called()
            with patch("github_proof.GitHubProof.public_job", return_value={"job_id": 99}):
                final = remote.finalize({"receipt_id": recovery["receipt_id"], "controller": CONTEXT, "github_token": "synthetic-only"})
            self.assertEqual(final["status"], "succeeded")
            old = remote.load_receipt(original["id"])
            self.assertEqual(old["status"], "failed")
            self.assertNotIn("recovered_by", old)
            self.assertEqual(remote.resolved_receipts()[old["id"]], recovery["receipt_id"])
            self.assertEqual(len(env.commands), count)
            self.assertFalse(remote.unfinished())
            with self.assertRaises(ValueError):
                remote.execute({"operation": "verify-preview", "receipt_id": original["id"]})

    def test_recovery_or_finalization_rejects_source_authority_schema_and_uid_drift(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            env, result = self.environment(stack, tmp)
            origin = remote.load_receipt(result["receipt_id"])
            current = env.snapshot()
            for mutate in (lambda s: s["deployments"]["api"].update(uid="replaced"),
                           lambda s: s["deployments"]["api"].update(source_sha="f" * 40),
                           lambda s: s["flags"].update(DF_DEPLOYMENT_EXECUTION_ENABLED="false"),
                           lambda s: s["schema_fingerprints"].update(application="changed")):
                state = copy.deepcopy(current)
                mutate(state)
                with self.assertRaises(ValueError):
                    verification_plan(state, origin)
            with patch("github_proof.GitHubProof.public_job", return_value={"job_id": 99}), self.assertRaises(ValueError):
                remote.finalize({"receipt_id": origin["id"], "controller": CONTEXT | {"run_id": 124}, "github_token": "synthetic-only"})

    def test_recovery_of_an_interrupted_recovery_resolves_ancestry_atomically(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            env, original = self.environment(stack, tmp)
            preview = remote.execute({"operation": "verify-preview", "receipt_id": original["receipt_id"]})
            first = remote.execute({"operation": "verify-apply", "receipt_id": original["receipt_id"], "approval": preview["plan_sha256"], "controller": CONTEXT})
            # Simulate runner loss before public proof; use the pending child as
            # the origin of a fresh explicitly reviewed verification attempt.
            preview = remote.execute({"operation": "verify-preview", "receipt_id": first["receipt_id"]})
            second = remote.execute({"operation": "verify-apply", "receipt_id": first["receipt_id"], "approval": preview["plan_sha256"], "controller": CONTEXT})
            with patch("github_proof.GitHubProof.public_job", return_value={"job_id": 99}):
                remote.finalize({"receipt_id": second["receipt_id"], "controller": CONTEXT, "github_token": "synthetic-only"})
            self.assertFalse(remote.unfinished())
            resolved = remote.resolved_receipts()
            self.assertEqual(resolved[original["receipt_id"]], second["receipt_id"])
            self.assertEqual(resolved[first["receipt_id"]], second["receipt_id"])
            self.assertEqual(remote.load_receipt(first["receipt_id"])["status"], "awaiting_public_verification")
            self.assertEqual(len([c for c in env.commands if c[0] == "patch"]), 12)

    def test_partial_rollback_retry_closes_prior_failures_without_replaying_images(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            env, original = self.environment(stack, tmp)
            preview = remote.execute({"operation": "rollback-preview", "receipt_id": original["receipt_id"]})
            calls = 0
            def fail_partial(*args, **kwargs):
                nonlocal calls
                if args[0] == "patch":
                    calls += 1
                    if calls == 3:
                        raise ValueError("synthetic partial rollback")
                return env.kube(*args, **kwargs)
            with patch.object(remote, "kube", side_effect=fail_partial), self.assertRaises(ValueError):
                remote.execute({"operation": "rollback-apply", "receipt_id": original["receipt_id"], "approval": preview["plan_sha256"], "controller": CONTEXT})
            preview = remote.execute({"operation": "rollback-preview", "receipt_id": original["receipt_id"]})
            self.assertEqual(len(preview["plan"]["changes"]), 10)
            rollback = remote.execute({"operation": "rollback-apply", "receipt_id": original["receipt_id"], "approval": preview["plan_sha256"], "controller": CONTEXT})
            with patch("github_proof.GitHubProof.public_job", return_value={"job_id": 99}):
                remote.finalize({"receipt_id": rollback["receipt_id"], "controller": CONTEXT, "github_token": "synthetic-only"})
            self.assertFalse(remote.unfinished())
            self.assertEqual(sum(1 for c in env.commands if c[0] == "patch"), 24)

    def test_matching_images_still_record_verifiable_admission_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            env, initial = self.environment(stack, tmp)
            with patch("github_proof.GitHubProof.public_job", return_value={"job_id": 99}):
                remote.finalize({"receipt_id": initial["receipt_id"], "controller": CONTEXT, "github_token": "synthetic-only"})
            release = fixtures()[2]
            preview = remote.execute({"operation": "preview", "release": release})
            self.assertEqual(preview["plan"]["changes"], [])
            count = len(env.commands)
            with patch.object(remote, "backup") as backup, patch.object(remote, "WriteFence") as fence:
                result = remote.execute({"operation": "apply", "release": release, "approval": preview["plan_sha256"], "controller": CONTEXT})
                self.assertEqual(result["status"], "awaiting_public_verification")
                backup.assert_not_called()
                fence.assert_not_called()
            self.assertEqual(len(env.commands), count)


if __name__ == "__main__":
    unittest.main()
