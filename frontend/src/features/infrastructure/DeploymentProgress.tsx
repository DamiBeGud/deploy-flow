import { useEffect, useState } from 'react'
import { command, errorMessage, request } from '../../shared/api'
import type { DeploymentRun, InfrastructurePullRequest } from './contracts'

type Policy = { enabled: boolean; execution_available: boolean; actor_id: string | null }
type Events = { events: { sequence: number; type: string; occurred_at: string; error_code?: string | null }[]; last_sequence: number }

export function DeploymentProgress({ applicationId, pullRequest, canDeploy }: {
  applicationId: string
  pullRequest: InfrastructurePullRequest
  canDeploy: boolean
}) {
  const [deployment, setDeployment] = useState<DeploymentRun | null>(null)
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [events, setEvents] = useState<Events['events']>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const deploymentId = pullRequest.deployment?.id ?? null

  useEffect(() => {
    const abort = new AbortController()
    request<Policy>(`/applications/${applicationId}/deployment-policy`, { signal: abort.signal })
      .then(setPolicy).catch(reason => { if (!abort.signal.aborted) setError(errorMessage(reason)) })
    return () => abort.abort()
  }, [applicationId])

  useEffect(() => {
    if (!deploymentId) return
    const abort = new AbortController()
    let cursor = 0
    let history: Events['events'] = []
    let inFlight = false
    const poll = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const run = await request<DeploymentRun>(`/deployments/${deploymentId}`, { signal: abort.signal })
        for (let page = 0; page < 10; page++) {
          const batch = await request<Events>(`/deployments/${deploymentId}/events?after=${cursor}`, { signal: abort.signal })
          if (batch.events.length) {
            cursor = batch.last_sequence
            history = [...history, ...batch.events]
          }
          if (batch.events.length < 100) break
        }
        if (!abort.signal.aborted) { setDeployment(run); setEvents(history) }
      } catch (reason) { if (!abort.signal.aborted) setError(errorMessage(reason)) }
      finally { inFlight = false }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 5000)
    return () => { abort.abort(); window.clearInterval(timer) }
  }, [deploymentId])

  async function enable() {
    setBusy(true); setError('')
    try {
      const next = await command<Policy>(`/applications/${applicationId}/deployment-policy`, { enabled: true }, 'PUT')
      setPolicy({ ...next, execution_available: true })
    } catch (reason) { setError(errorMessage(reason)) } finally { setBusy(false) }
  }

  async function disable() {
    setBusy(true); setError('')
    try {
      const next = await command<Policy>(`/applications/${applicationId}/deployment-policy`, { enabled: false }, 'PUT')
      setPolicy({ ...next, execution_available: true })
    } catch (reason) { setError(errorMessage(reason)) } finally { setBusy(false) }
  }

  async function execute() {
    if (!deploymentId) return
    setBusy(true); setError('')
    try {
      await command(`/deployments/${deploymentId}/execute`)
      setDeployment(await request<DeploymentRun>(`/deployments/${deploymentId}`))
    } catch (reason) { setError(errorMessage(reason)) } finally { setBusy(false) }
  }

  async function retry() {
    if (!deploymentId) return
    setBusy(true); setError('')
    try {
      await command(`/deployments/${deploymentId}/retry-execution`)
      setDeployment(await request<DeploymentRun>(`/deployments/${deploymentId}`))
    } catch (reason) { setError(errorMessage(reason)) } finally { setBusy(false) }
  }

  return <section className="deployment-progress" aria-label="Deployment progress">
    <b>Deployment</b>
    {error && <p role="alert">{error}</p>}
    {!deploymentId ? <p role="status">{pullRequest.release_preparation
      ? `Release preparation: ${pullRequest.release_preparation.status}${pullRequest.release_preparation.error_code ? ` · ${pullRequest.release_preparation.error_code}` : ''}`
      : 'Waiting for the verified release to be prepared.'}</p> : <>
      <p role="status">Status: {deployment?.status ?? pullRequest.deployment?.status}{deployment?.error_code ? ` · ${deployment.error_code}` : ''}</p>
      <p className="section-note">Deployment ID: <code>{deploymentId}</code> · Target: deployflow-test</p>
      {deployment && <p>Image: <code>{deployment.image_reference}</code></p>}
      {deployment?.rollout && <p>Replicas: {deployment.rollout.ready_replicas}/{deployment.rollout.desired_replicas} ready · {deployment.rollout.available_replicas} available{deployment.rollout.reason ? ` · ${deployment.rollout.reason}` : ''}</p>}
      {deployment?.health && <p>Internal service health: {deployment.health.status} · {deployment.health.service}{deployment.health.path}</p>}
      {deployment?.resources.length ? <ul>{deployment.resources.map(row => <li key={`${row.kind}:${row.name}`}>{row.kind}/{row.name}: {row.status}{row.error_code ? ` · ${row.error_code}` : ''}</li>)}</ul> : null}
      {events.length > 0 && <div className="deployment-events"><b>Recent progress</b><ul>{events.slice(-8).map(event => <li key={event.sequence}>{event.type.replace('deployment.', '')}{event.error_code ? ` · ${event.error_code}` : ''}</li>)}</ul></div>}
      {policy?.execution_available && canDeploy && !policy.enabled && <button className="button button-secondary" disabled={busy} type="button" onClick={() => void enable()}>Enable automatic deployment for this application</button>}
      {policy?.execution_available && canDeploy && policy.enabled && <button className="button button-secondary" disabled={busy} type="button" onClick={() => void disable()}>Disable future automatic deployments</button>}
      {policy?.enabled && canDeploy && deployment?.status === 'waiting_for_executor' && <button className="button button-primary" disabled={busy} type="button" onClick={() => void execute()}>Deploy this prepared release</button>}
      {policy?.enabled && canDeploy && deployment?.status === 'reconciling' && <button className="button button-primary" disabled={busy} type="button" onClick={() => void retry()}>Retry reconciliation</button>}
      {!policy?.execution_available && deployment?.status === 'waiting_for_executor' && <p className="section-note">Release is ready. Cluster execution has not been configured.</p>}
    </>}
  </section>
}
