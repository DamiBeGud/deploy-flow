-- Review artifact only; execute the pinned Alembic upgrade after operator approval.
-- Generated without a database connection for 2f73b94e8c16 -> 91d2e0a7b534.
BEGIN;

-- Running upgrade 2f73b94e8c16 -> 76e4c12a8b03

ALTER TABLE deployment_runs ADD COLUMN rollout_json JSONB;

ALTER TABLE deployment_runs ADD COLUMN health_json JSONB;

ALTER TABLE deployment_runs ADD COLUMN previous_release_id UUID;

ALTER TABLE deployment_runs ADD FOREIGN KEY(previous_release_id) REFERENCES release_specifications (id);

DROP INDEX uq_active_deployment_application_environment;

CREATE UNIQUE INDEX uq_active_deployment_application_environment ON deployment_runs (application_id, environment_profile_id) WHERE status IN ('queued', 'validating', 'retry_wait', 'waiting_for_executor', 'checking', 'applying', 'monitoring', 'checking_health', 'reconciling');

CREATE TABLE deployment_policies (
    id UUID NOT NULL,
    application_id UUID NOT NULL,
    environment_profile_id VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL,
    actor_id VARCHAR(128) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_deployment_policy_target UNIQUE (application_id, environment_profile_id),
    FOREIGN KEY(application_id) REFERENCES applications (id)
);

CREATE INDEX ix_deployment_policies_application_id ON deployment_policies (application_id);

CREATE TABLE execution_jobs (
    id UUID NOT NULL,
    deployment_run_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempts INTEGER NOT NULL,
    error_code VARCHAR(64),
    next_dispatch_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    lease_token UUID,
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    deadline_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_execution_job_run UNIQUE (deployment_run_id),
    FOREIGN KEY(deployment_run_id) REFERENCES deployment_runs (id)
);

CREATE INDEX ix_execution_jobs_deployment_run_id ON execution_jobs (deployment_run_id);

CREATE INDEX ix_execution_jobs_status ON execution_jobs (status);

CREATE TABLE deployment_resources (
    id UUID NOT NULL,
    deployment_run_id UUID NOT NULL,
    kind VARCHAR(32) NOT NULL,
    name VARCHAR(253) NOT NULL,
    status VARCHAR(32) NOT NULL,
    uid VARCHAR(128),
    generation INTEGER,
    error_code VARCHAR(64),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_deployment_resource UNIQUE (deployment_run_id, kind, name),
    FOREIGN KEY(deployment_run_id) REFERENCES deployment_runs (id)
);

CREATE INDEX ix_deployment_resources_deployment_run_id ON deployment_resources (deployment_run_id);

UPDATE alembic_version SET version_num='76e4c12a8b03' WHERE alembic_version.version_num = '2f73b94e8c16';

-- Running upgrade 76e4c12a8b03 -> 8c27b4f1a902

ALTER TABLE release_specifications ADD COLUMN kind VARCHAR(32) DEFAULT 'standard' NOT NULL;

ALTER TABLE release_specifications ADD COLUMN rollback_target_release_id UUID;

ALTER TABLE release_specifications ADD FOREIGN KEY(rollback_target_release_id) REFERENCES release_specifications (id);

ALTER TABLE release_specifications ADD COLUMN rollback_from_release_id UUID;

ALTER TABLE release_specifications ADD FOREIGN KEY(rollback_from_release_id) REFERENCES release_specifications (id);

ALTER TABLE release_specifications DROP CONSTRAINT uq_release_plan_build;

CREATE UNIQUE INDEX uq_standard_release_plan_build ON release_specifications (plan_id, verified_build_id) WHERE kind = 'standard';

UPDATE alembic_version SET version_num='8c27b4f1a902' WHERE alembic_version.version_num = '76e4c12a8b03';

-- Running upgrade 8c27b4f1a902 -> 91d2e0a7b534

ALTER TABLE execution_jobs ADD COLUMN purpose VARCHAR(32) DEFAULT 'deploy' NOT NULL;

DROP INDEX uq_active_deployment_application_environment;

CREATE UNIQUE INDEX uq_active_deployment_application_environment ON deployment_runs (application_id, environment_profile_id) WHERE status IN ('queued', 'validating', 'retry_wait', 'waiting_for_executor', 'checking', 'applying', 'monitoring', 'checking_health', 'reconciling', 'cleanup_pending', 'cleaning');

UPDATE alembic_version SET version_num='91d2e0a7b534' WHERE alembic_version.version_num = '8c27b4f1a902';

COMMIT;
