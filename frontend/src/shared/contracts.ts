export type Identity = { actor_id: string; organization_id: string; roles: string[]; authentication: string }
export type Application = { id: string; name: string; repository_url: string; revision: string }
export type Finding = { value: string | number | null; source: 'mock' | 'unknown' | 'user' }
export type Assessment = {
  mode: 'fake'; runtime: Finding; port: Finding; health_path: Finding
  environment_variable_names: string[]; unknowns: string[]
}
export type Analysis = {
  id: string; run_id: string; application_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number; revision: string; attempts: number; assessment_version: number
  assessment: Assessment | null; error_code: string | null
}
export type ApplicationDetail = Application & { latest_analysis: Analysis | null }
export type QueuedAnalysis = { run_id: string; events_url: string; status: 'queued' }
export const isTerminal = (status: Analysis['status']) => ['completed', 'failed', 'cancelled'].includes(status)

