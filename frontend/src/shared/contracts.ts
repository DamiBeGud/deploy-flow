export type Identity = { actor_id: string; organization_id: string; roles: string[]; authentication: string }
export type Application = { id: string; name: string; repository_url: string; revision: string }
export type Finding = { value: string | number | null; source: 'mock' | 'unknown' | 'user' | 'explicit' | 'inferred'; command?: string | null }
export type FakeAssessment = {
  mode: 'fake'; runtime: Finding; port: Finding; health_path: Finding
  environment_variable_names: string[]; unknowns: string[]
}
export type DeterministicAssessment = {
  mode: 'deterministic' | 'agent'; schema_version?: 'infrastructure-assessment-v1' | 'infrastructure-assessment-v2'; completion: 'complete' | 'partial'; incomplete_reason: string | null
  runtime: Finding | { name: string | null; command: string | null; version: string | null }; port?: Finding; health_path?: Finding; environment_variable_names?: string[]
  repository: { url: string; commit_sha: string }; review: { runtime: string; port: number | null; health_path: string | null }
  build?: { install_command: string | null; artifact: string | null }
  workflow?: { test_command: string | null; build_command: string | null }
  detected_stack: { languages: string[]; frameworks: string[] }; configuration: { required_variables: string[]; optional_variables: string[] }
  evidence: { id: string; file_path: string; line_start: number | null; line_end: number | null; detector: string }[]; unknowns: string[]
  findings?: { id: string; category: string; value: string | number | boolean | null; status: string; confidence: number; evidence_ids: string[] }[]
  questions?: { id: string; field_path: string; prompt: string; reason: string; required: boolean; answer_type: string; choices: string[] }[]
  risks?: string[]; summary?: string | null
}
export type Assessment = FakeAssessment | DeterministicAssessment
export type Analysis = {
  id: string; run_id: string; application_id: string
  status: 'queued' | 'running' | 'completed' | 'needs_input' | 'failed' | 'cancelled'
  progress: number; revision: string; commit_sha?: string | null; analysis_mode?: string; attempts: number; assessment_version: number
  assessment: Assessment | null; error_code: string | null; error_message?: string | null
}
export type ApplicationDetail = Application & { latest_analysis: Analysis | null }
export type QueuedAnalysis = { run_id: string; events_url: string; status: 'queued' }
export type DeploymentRun = {
  id: string; application_id: string; environment_profile_id: string
  release_id: string; release_digest: string; image_reference: string
  kind: 'standard' | 'rollback'; rollback_target_release_id: string | null
  status: string; error_code: string | null; event_sequence: number
  previous_release_id: string | null; created_at: string
  preflight_validation: { status: string; checks?: unknown[] } | null
  rollout: { status?: string; desired_replicas: number; updated_replicas?: number; ready_replicas: number; available_replicas: number; reason?: string } | null
  health: { status: string; service: string; path: string; code?: string } | null
  execution_job: { id: string; purpose: 'deploy' | 'cleanup'; status: string; error_code: string | null } | null
  resources: { kind: string; name: string; status: string; uid: string | null; generation: number | null; error_code: string | null }[]
}
export const isTerminal = (status: Analysis['status']) => ['completed', 'needs_input', 'failed', 'cancelled'].includes(status)
