import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage, request } from '../../shared/api'
import { deploymentChecks, deploymentLabel, statusLabel } from '../../shared/deploymentStatus'
import { ReleaseChecklist } from '../../shared/ReleaseChecklist'
import type { DeploymentRun } from '../../shared/contracts'
import type { InfrastructurePullRequest } from './contracts'

type Events = { events: { sequence: number; type: string; occurred_at: string; error_code?: string | null }[]; last_sequence: number }

export function DeploymentProgress({ applicationId, pullRequest }: {
  applicationId: string
  pullRequest: InfrastructurePullRequest
}) {
  const [deployment, setDeployment] = useState<DeploymentRun | null>(null)
  const [events, setEvents] = useState<Events['events']>([])
  const [error, setError] = useState('')
  const deploymentId = pullRequest.deployment?.id ?? null
  const selected = deployment?.id === deploymentId ? deployment : null

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
        if (!abort.signal.aborted) { setDeployment(run); setEvents(history); setError('') }
      } catch (reason) { if (!abort.signal.aborted) setError(errorMessage(reason)) }
      finally { inFlight = false }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 5000)
    return () => { abort.abort(); window.clearInterval(timer) }
  }, [deploymentId])

  return <section className="deployment-progress" aria-label="Deployment progress">
    {error && <p role="alert">{error}</p>}
    {deploymentId
      ? <>
        <p role="status"><b>{deploymentLabel(selected?.status ?? pullRequest.deployment?.status ?? 'pending', selected?.error_code ?? pullRequest.deployment?.error_code)}</b></p>
        <ReleaseChecklist label="Recorded deployment checks" checks={deploymentChecks(selected)} />
        <details className="workflow-details"><summary>Recorded deployment details</summary>
          <p>Deployment <code>{deploymentId}</code></p>
          {selected?.image_reference && <p>Image <code>{selected.image_reference}</code></p>}
          {selected?.rollout?.observed_at && <p>Rollout checked {new Date(selected.rollout.observed_at).toLocaleString()}</p>}
          {selected?.health && <p>Health endpoint <code>{selected.health.service}{selected.health.path}</code></p>}
          {selected?.error_code && <p>Error code <code>{selected.error_code}</code></p>}
          {events.length > 0 && <ul>{events.slice(-8).map(event => <li key={event.sequence}>{statusLabel(event.type.replace('deployment.', ''))}{event.error_code ? ` · ${event.error_code}` : ''}</li>)}</ul>}
        </details>
      </> : <p role="status">Release preparation {pullRequest.release_preparation ? statusLabel(pullRequest.release_preparation.status).toLowerCase() : 'pending'}.</p>}
    <p><Link to={`/applications/${applicationId}`}>View current cluster state and deployment controls</Link></p>
  </section>
}
