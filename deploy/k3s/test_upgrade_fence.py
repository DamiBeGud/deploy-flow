"""Opt-in real PostgreSQL locking test. Creates only its own isolated container.

DF_UPGRADE_TEST_POSTGRES=1 enables this suite. No .env, host ports, live database
network or real credentials are passed to it. The only DB is deployflow_upgrade_test.
"""

import os
import subprocess
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import upgrade_remote


@unittest.skipUnless(os.environ.get("DF_UPGRADE_TEST_POSTGRES") == "1", "Opt-in disposable PostgreSQL locking test")
class PostgreSQLWriteFenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container = "deployflow-upgrade-test-" + uuid4().hex[:12]
        cls.image = "docker.io/library/postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
        result = subprocess.run([
            "docker", "run", "-d", "--name", cls.container, "--network", "none", "--read-only", "--user", "70:70",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "256m", "--cpus", "1",
            "--tmpfs", "/var/lib/postgresql/data:rw,uid=70,gid=70,mode=0700",
            "--tmpfs", "/var/run/postgresql:rw,uid=70,gid=70", "--tmpfs", "/tmp:rw,uid=70,gid=70",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust", cls.image,
        ], capture_output=True, check=False, timeout=90)
        if result.returncode:
            raise RuntimeError("Cannot start isolated PostgreSQL fixture")
        cls.addClassCleanup(lambda: subprocess.run(["docker", "rm", "-f", cls.container], capture_output=True, check=False, timeout=20))
        for _ in range(40):
            # pg_isready can briefly succeed against initdb's temporary server.
            # Wait until the entrypoint has exec'd the final postgres process.
            process = subprocess.run(["docker", "exec", cls.container, "cat", "/proc/1/comm"], capture_output=True, check=False)
            result = subprocess.run(["docker", "exec", cls.container, "pg_isready", "-U", "postgres"], capture_output=True, check=False)
            if result.returncode == 0 and process.stdout.strip() == b"postgres":
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Isolated PostgreSQL did not become ready")
        cls.command = ["docker", "exec", "-i", cls.container, "psql", "-X", "-U", "postgres", "-d", "deployflow_upgrade_test", "-At", "-v", "ON_ERROR_STOP=1"]
        subprocess.run(["docker", "exec", cls.container, "createdb", "-U", "postgres", "deployflow_upgrade_test"], capture_output=True, check=True)
        cls.sql("CREATE TABLE jobs(id int PRIMARY KEY)", success=True)

    @classmethod
    def sql(cls, statement, *, success):
        result = subprocess.run(cls.command, input=statement.encode(), capture_output=True, check=False, timeout=10)
        if (result.returncode == 0) != success:
            raise AssertionError("Unexpected isolated SQL result")
        return result.stdout.decode().strip()

    def test_lock_allows_reads_blocks_writes_then_releases_after_rollout(self):
        with patch.object(upgrade_remote, "psql_command", return_value=self.command):
            with upgrade_remote.WriteFence(["jobs"], seconds=30) as fence:
                fence.check()
                self.assertEqual(self.sql("SELECT count(*) FROM jobs", success=True), "0")
                self.sql("SET lock_timeout='150ms'; INSERT INTO jobs VALUES(1)", success=False)
            self.sql("INSERT INTO jobs VALUES(1)", success=True)
            self.sql("DELETE FROM jobs", success=True)

    def test_only_unleased_blocked_preparation_without_deployment_is_inactive(self):
        self.sql("CREATE TABLE alembic_version(version_num text); INSERT INTO alembic_version VALUES('synthetic'); "
                 "CREATE TABLE pending_jobs(id int); CREATE TABLE analysis_runs(status text); "
                 "CREATE TABLE github_jobs(status text); CREATE TABLE execution_jobs(status text); "
                 "CREATE TABLE delivery_jobs(status text NOT NULL,kind text NOT NULL,deployment_run_id text,lease_token text,lease_expires_at timestamptz); "
                 "INSERT INTO delivery_jobs VALUES('blocked','prepare',NULL,NULL,NULL)", success=True)
        try:
            with patch.object(upgrade_remote, "psql_command", return_value=self.command):
                self.assertEqual(upgrade_remote.pending()["delivery"], 0)
                self.sql("INSERT INTO delivery_jobs VALUES "
                         "('blocked','preflight','run',NULL,NULL),"
                         "('blocked','prepare',NULL,'lease',NULL),"
                         "('blocked','prepare',NULL,NULL,now()),"
                         "('queued','prepare',NULL,NULL,NULL),"
                         "('retry_wait','prepare',NULL,NULL,NULL),"
                         "('running','prepare',NULL,'lease',now()),"
                         "('blocked','prepare','run',NULL,NULL),"
                         "('unknown','prepare',NULL,NULL,NULL),"
                         "('completed','prepare',NULL,NULL,NULL),"
                         "('failed','prepare',NULL,NULL,NULL)", success=True)
                self.assertEqual(upgrade_remote.pending()["delivery"], 8)
        finally:
            self.sql("DROP TABLE delivery_jobs,alembic_version,pending_jobs,analysis_runs,github_jobs,execution_jobs", success=True)

    def test_exception_releases_locks_and_existing_writer_prevents_fence(self):
        with patch.object(upgrade_remote, "psql_command", return_value=self.command):
            with self.assertRaisesRegex(ValueError, "synthetic rollout failure"), upgrade_remote.WriteFence(["jobs"], seconds=30):
                raise ValueError("synthetic rollout failure")
            self.sql("INSERT INTO jobs VALUES(2)", success=True)
            writer = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                writer.stdin.write(b"BEGIN; INSERT INTO jobs VALUES(3);\n")
                writer.stdin.flush()
                # Wait for INSERT acknowledgement; this is an isolated local socket.
                self.assertEqual(writer.stdout.readline().strip(), b"BEGIN")
                self.assertEqual(writer.stdout.readline().strip(), b"INSERT 0 1")
                with self.assertRaises(ValueError), upgrade_remote.WriteFence(["jobs"], seconds=30, lock_timeout_ms=150):
                    self.fail("Fencing must fail before upgrade writes")
            finally:
                writer.stdin.write(b"ROLLBACK;\n\\q\n")
                writer.stdin.close()
                writer.wait(timeout=10)
                writer.stdout.close()
                writer.stderr.close()
            self.sql("DELETE FROM jobs", success=True)

    def test_schema_fingerprint_parses_multiline_aggregates_and_tracks_ddl_not_rows(self):
        def kube(*args):
            command = args[args.index("--") + 1:]
            result = subprocess.run(["docker", "exec", self.container, *command], capture_output=True, check=True)
            return result.stdout.decode()

        with patch.object(upgrade_remote, "kube", side_effect=kube):
            first = upgrade_remote.schema_fingerprint("synthetic", "postgres", "deployflow_upgrade_test")
            self.sql("INSERT INTO jobs VALUES(4)", success=True)
            self.assertEqual(first, upgrade_remote.schema_fingerprint("synthetic", "postgres", "deployflow_upgrade_test"))
            self.sql("ALTER TABLE jobs ADD COLUMN detail text", success=True)
            self.assertNotEqual(first, upgrade_remote.schema_fingerprint("synthetic", "postgres", "deployflow_upgrade_test"))
            self.sql("ALTER TABLE jobs DROP COLUMN detail; DELETE FROM jobs", success=True)


if __name__ == "__main__":
    unittest.main()
