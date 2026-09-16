export type Identity = { actor_id: string; organization_id: string; roles: string[]; authentication: string }
export type Application = { id: string; name: string; repository_url: string; revision: string }
export type Finding = { value: string | number | null; source: 'mock' | 'unknown' | 'user' | 'explicit' | 'inferred' }
export type FakeAssessment = {
  mode: 'fake'; runtime: Finding; port: Finding; health_path: Finding
  environment_variable_names: string[]; unknowns: string[]
}
export type DeterministicAssessment = {
  mode: 'deterministic'; completion: 'complete' | 'partial'; incomplete_reason: string | null
  runtime: Finding; port: Finding; health_path: Finding; environment_variable_names: string[]
  repository: { url: string; commit_sha: string }; review: { runtime: string; port: number | null; health_path: string | null }
  detected_stack: { languages: string[]; frameworks: string[] }; configuration: { required_variables: string[]; optional_variables: string[] }
  evidence: { id: string; file_path: string; line_start: number | null; line_end: number | null; detector: string }[]; unknowns: string[]
}
export type Assessment = FakeAssessment | DeterministicAssessment
export type Analysis = {
  id: string; run_id: string; application_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number; revision: string; commit_sha?: string | null; analysis_mode?: string; attempts: number; assessment_version: number
  assessment: Assessment | null; error_code: string | null; error_message?: string | null
}
export type ApplicationDetail = Application & { latest_analysis: Analysis | null }
export type QueuedAnalysis = { run_id: string; events_url: string; status: 'queued' }
export const isTerminal = (status: Analysis['status']) => ['completed', 'failed', 'cancelled'].includes(status)
