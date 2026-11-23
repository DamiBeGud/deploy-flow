import { command, request } from '../../shared/api'
import type { DeploymentIntent, InfrastructurePlan } from './contracts'

type ConfigurationRecommendation = { name: string; required: boolean; recommended_value: string | null; purpose: string; source: string; confidence: number; evidence: { file_path: string; line_start: number | null } | null }
type RailsProvisioning = { status: 'required' | 'queued' | 'ready' | 'blocked'; code: string | null }
export type Recommendation = { rails_provisioning?: RailsProvisioning; intent: DeploymentIntent; generation_blocker?: string | null; approved_database?: { migration: string; ephemeral?: boolean } | null; approved_queue?: { worker: string } | null; approved_api_token?: boolean; application_configuration: ConfigurationRecommendation[] }

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

export async function deliveryRecommendation(
  applicationId: string, runId: string, signal: AbortSignal, onProvisioning: () => void,
) {
  const path = `/applications/${applicationId}`
  const query = `?analysis_run_id=${encodeURIComponent(runId)}`
  const load = () => request<Recommendation>(`${path}/kubernetes-recommendation${query}`, { signal })
  let result = await load()
  if (result.rails_provisioning?.status === 'required') {
    onProvisioning()
    await request<RailsProvisioning>(`${path}/rails-provisioning${query}`, { method: 'POST', signal })
    result = await load()
  }
  for (let attempt = 0; result.rails_provisioning?.status === 'queued' && attempt < 45; attempt++) {
    onProvisioning()
    await new Promise(resolve => setTimeout(resolve, 2000))
    signal.throwIfAborted()
    result = await load()
  }
  if (result.rails_provisioning?.status === 'queued') {
    throw new DatabaseSetupPending('Database setup is still queued. Reload this page to check its progress.')
  }
  if (result.rails_provisioning?.status === 'blocked') {
    throw new Error(`Database setup stopped (${result.rails_provisioning.code ?? 'status unavailable'}).`)
  }
  return result
}

export class DatabaseSetupPending extends Error {}
