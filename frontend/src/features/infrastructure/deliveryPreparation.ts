import { command } from '../../shared/api'
import type { DeploymentIntent, InfrastructurePlan } from './contracts'

type ConfigurationRecommendation = { name: string; required: boolean; recommended_value: string | null; purpose: string; source: string; confidence: number; evidence: { file_path: string; line_start: number | null } | null }
export type Recommendation = { intent: DeploymentIntent; generation_blocker?: string | null; approved_database?: { migration: string; ephemeral?: boolean } | null; approved_queue?: { worker: string } | null; approved_api_token?: boolean; application_configuration: ConfigurationRecommendation[] }

function writableIntent(intent: DeploymentIntent) {
  return {
    schema_version: intent.schema_version,
    environment_profile_id: intent.environment_profile_id,
    connectivity: {
      port: intent.connectivity.port,
      health_path: intent.connectivity.health_path,
      replicas: intent.connectivity.replicas,
      resources: {
        requests: { ...intent.connectivity.resources.requests },
        limits: { ...intent.connectivity.resources.limits },
      },
      environment: { ...intent.connectivity.environment },
      connections: [...(intent.connectivity.connections || [])],
      exposure: { ...intent.connectivity.exposure },
    },
    build: {
      schema_version: intent.build.schema_version,
      branch: intent.build.branch,
      image: intent.build.image,
      build_context: intent.build.build_context,
      install_command: [...intent.build.install_command],
      test_command: intent.build.test_command ? [...intent.build.test_command] : null,
      build_command: intent.build.build_command ? [...intent.build.build_command] : null,
      start_command: [...intent.build.start_command],
    },
  }
}
function preparationKey(runId: string, intent: DeploymentIntent, automatic: boolean) {
  // Concurrent browser sessions for this run use the same key. The API scopes it by actor and action.
  return automatic ? `prepare:${runId}:${intent.assessment_version}:${intent.version}` : undefined
}

export function saveDeliveryIntent(applicationId: string, runId: string, intent: DeploymentIntent, automatic = false) {
  return command<DeploymentIntent>(`/applications/${applicationId}/deployment-intent`, {
    ...writableIntent(intent), expected_version: intent.version, analysis_run_id: runId,
    expected_assessment_version: intent.assessment_version,
  }, 'PUT', preparationKey(runId, intent, automatic))
}

export function generateDeliveryBundle(applicationId: string, runId: string, intent: DeploymentIntent, automatic = false) {
  return command<InfrastructurePlan>(`/applications/${applicationId}/infrastructure-plans`, {
    analysis_run_id: runId, expected_assessment_version: intent.assessment_version,
    intent_version: intent.version, environment_profile_id: intent.environment_profile_id,
  }, 'POST', preparationKey(runId, intent, automatic))
}
