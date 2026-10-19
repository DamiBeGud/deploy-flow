"""Migration review must work without access to a database."""

import os
import subprocess
import sys


def test_phase7_upgrade_can_be_rendered_without_database_access():
    environment = os.environ | {
        "DF_DATABASE_URL": "postgresql+psycopg://preview:preview@127.0.0.1:1/preview",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "2f73b94e8c16:91d2e0a7b534", "--sql"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert "CREATE TABLE execution_jobs" in result.stdout
    assert "CREATE TABLE deployment_policies" in result.stdout
    assert "rollback_target_release_id" in result.stdout
    assert "ADD COLUMN purpose" in result.stdout
    assert "91d2e0a7b534" in result.stdout
    assert "COMMIT;" in result.stdout
