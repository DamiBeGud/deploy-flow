export type RecommendationOrigin = 'explicit' | 'inferred' | 'heuristic' | 'policy_default' | 'user_supplied'

export type FieldRecommendation = {
  recommended_value: unknown
  effective_value: unknown
  origin: RecommendationOrigin
  recommendation_origin: Exclude<RecommendationOrigin, 'user_supplied'>
  source: string
  confidence: number
  explanation: string
  evidence: { file_path: string; line_start: number | null } | null
  overridden: boolean
}

type Resources = { requests: Record<string, string>; limits: Record<string, string> }

export type DeploymentIntent = {
  schema_version: 'deployment-intent-v1'
  version: number
  assessment_version: number
  id?: string
  analysis_run_id?: string
  created_at?: string
  environment_profile_id: 'local-k3s'
  field_recommendations: Record<string, FieldRecommendation>
  connectivity: {
    port: number
    health_path: string
    replicas: number
    resources: Resources
    environment: Record<string, string>
    connections?: unknown[]
    exposure: {
      kind: 'internal' | 'external'
      hostname?: string | null
      path: string
      tls_secret_name?: string | null
    }
  }
  build: {
    schema_version: 'build-specification-v1'
    branch: string
    image: string
    build_context: '.'
    install_command: string[]
    test_command: string[] | null
    build_command: string[] | null
    start_command: string[]
  }
}

export type PlanFile = {
  path: string
  content: string
  hash: string
  change: 'added' | 'changed' | 'unchanged'
}

export type InfrastructurePlan = {
  schema_version: 'infrastructure-plan-v1'
  id: string
  bundle_digest: string
  status: 'proposed' | 'approved' | 'stale' | 'invalid'
  validation: {
    checks: string[]
    provisional_image: string
    image_binding: {
      name: 'application-image'
      repository: string
      preview_reference: string
      status: 'provisional'
      final_reference_kind: 'digest'
    }
  }
  cluster_validation: {
    status: 'passed' | 'failed' | 'unavailable'
    observed_at?: string
    checks: { kind?: string; name?: string; code: string; details?: { field: string; reason: string }[] }[]
  } | null
  files: PlanFile[]
  comparison: {
    previous_plan_id: string | null
    added: string[]
    changed: string[]
    removed: string[]
  }
  approval: { actor_id: string } | null
  pull_request?: InfrastructurePullRequest | null
}

export type InfrastructurePullRequest = {
  id: string
  plan_id: string
  status: 'queued' | 'open' | 'merged' | 'verified' | 'closed' | 'blocked' | 'failed' | 'ci_failed'
  url: string | null
  number: number | null
  head_sha: string | null
  merged_sha: string | null
  error_code: string | null
  verified_build: { run_id: number; run_attempt: number; image_repository: string; image_digest: string } | null
}
