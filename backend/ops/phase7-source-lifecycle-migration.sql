BEGIN;

-- Running upgrade 91d2e0a7b534 -> c3a71d9e4208

ALTER TABLE deployment_policies ADD COLUMN allowed_source_sha VARCHAR(40);

ALTER TABLE infrastructure_pull_requests ADD COLUMN source_head_sha VARCHAR(40);

ALTER TABLE infrastructure_pull_requests ADD COLUMN source_status VARCHAR(32);

ALTER TABLE infrastructure_pull_requests ADD COLUMN source_error_code VARCHAR(64);

ALTER TABLE verified_builds ADD COLUMN source_sha VARCHAR(40);

ALTER TABLE verified_builds ADD COLUMN artifact_id BIGINT;

ALTER TABLE verified_builds ADD COLUMN compatibility_json JSONB;

ALTER TABLE release_specifications ADD COLUMN source_sha VARCHAR(40);

UPDATE verified_builds SET source_sha = merged_sha;

UPDATE release_specifications SET source_sha = merged_sha;

UPDATE alembic_version SET version_num='c3a71d9e4208' WHERE alembic_version.version_num = '91d2e0a7b534';

COMMIT;

