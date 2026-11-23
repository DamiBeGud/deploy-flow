import type { DeploymentRun } from './contracts'

const statusLabels: Readonly<Record<string, string>> = {
  not_deployed: 'Not deployed yet',
  unavailable: 'Current readiness unknown',
  ready: 'Ready',
  pending: 'Pending',
  missing: 'Not deployed yet',
  zero: 'Scaled to zero',
  open: 'Pull request open',
  merged: 'Merged, verifying build',
  verified: 'Image verified',
  waiting_for_executor: 'Waiting to deploy',
  reconciling: 'Needs attention',
  succeeded: 'Deployment succeeded',
  checking_health: 'Checking application health',
  retry_wait: 'Waiting to retry',
  scaled_down: 'Scaled to zero',
}

const failureLabels: Readonly<Record<string, string>> = {
  application_start_failed: 'Application failed to start',
  container_image_reference_invalid: 'Container image reference is invalid',
  execution_deadline_exceeded: 'Deployment timed out',
  rollout_deadline_exceeded: 'Rollout timed out',
}

export function statusLabel(status: string): string {
  return Object.hasOwn(statusLabels, status) ? statusLabels[status] : status.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase())
}

export function deploymentLabel(status: string, error?: string | null): string {
  if (error && Object.hasOwn(failureLabels, error)) return failureLabels[error]
  if (error && status === 'reconciling') return 'Deployment needs attention'
  return statusLabel(status)
}

export type ReleaseCheck = { label: string; result: string; tone?: 'complete' | 'attention' }

export function deploymentChecks(run?: DeploymentRun | null): ReleaseCheck[] {
  const admission = run?.preflight_validation?.status
  const rollout = run?.rollout?.status
  const health = run?.health?.status
  const rolloutBlocked = admission === 'failed' || admission === 'unavailable'
  const healthBlocked = rolloutBlocked || rollout === 'failed'
  return [
    { label: 'Kubernetes admission', result: admission ? statusLabel(admission) : 'Pending', tone: admission === 'passed' ? 'complete' : rolloutBlocked ? 'attention' : undefined },
    { label: 'Rollout', result: rollout && !(rollout === 'pending' && rolloutBlocked) ? statusLabel(rollout) : rolloutBlocked ? 'Not reached' : 'Pending', tone: rollout === 'ready' ? 'complete' : rollout === 'failed' ? 'attention' : undefined },
    { label: 'Application health', result: health && !(health === 'pending' && healthBlocked) ? statusLabel(health) : healthBlocked ? 'Not reached' : 'Pending', tone: health === 'passed' ? 'complete' : health === 'failed' ? 'attention' : undefined },
  ]
}
