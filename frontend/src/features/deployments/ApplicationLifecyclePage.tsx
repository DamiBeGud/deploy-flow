import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, RefreshCw, RotateCcw } from 'lucide-react'
import { command, errorMessage, request } from '../../shared/api'
import type { ApplicationDetail, DeploymentRun } from '../../shared/contracts'
import { ErrorNotice } from '../../shared/ui'

type Policy = { enabled: boolean; execution_available: boolean; cleanup_available: boolean; actor_id: string | null }
type EventPage = { events: { sequence: number; type: string; occurred_at: string; error_code?: string | null }[] }

export function ApplicationLifecyclePage({ canDeploy }: { canDeploy: boolean }) {
  const { applicationId } = useParams()
  const [application, setApplication] = useState<ApplicationDetail | null>(null)
  const [runs, setRuns] = useState<DeploymentRun[]>([])
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [events, setEvents] = useState<EventPage['events']>([])
  const [confirmRollback, setConfirmRollback] = useState(false)
  const [confirmCleanup, setConfirmCleanup] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    if (!applicationId) return
    const abort = new AbortController()
    // A new application or explicit refresh starts a new read cycle.
    // eslint-disable-next-line react/set-state-in-effect
    setLoading(true)
    const load = async () => {
      try {
        const [app, history, setting] = await Promise.all([
          request<ApplicationDetail>(`/applications/${applicationId}`, { signal: abort.signal }),
          request<{ deployments: DeploymentRun[] }>(`/applications/${applicationId}/deployments`, { signal: abort.signal }),
          request<Policy>(`/applications/${applicationId}/deployment-policy`, { signal: abort.signal }),
        ])
        if (!abort.signal.aborted) {
          setApplication(app); setRuns(history.deployments); setPolicy(setting); setError('')
          setSelectedId(id => id && history.deployments.some(run => run.id === id) ? id : history.deployments[0]?.id ?? null)
        }
      } catch (reason) { if (!abort.signal.aborted) setError(errorMessage(reason)) }
      finally { if (!abort.signal.aborted) setLoading(false) }
    }
    void load()
    const timer = window.setInterval(() => {
      request<{ deployments: DeploymentRun[] }>(`/applications/${applicationId}/deployments`, { signal: abort.signal })
        .then(history => { if (!abort.signal.aborted) setRuns(history.deployments) })
        .catch(reason => { if (!abort.signal.aborted) setError(errorMessage(reason)) })
    }, 5000)
    return () => { abort.abort(); window.clearInterval(timer) }
  }, [applicationId, revision])

  useEffect(() => {
    if (!selectedId) return
    const abort = new AbortController()
    const load = () => request<EventPage>(`/deployments/${selectedId}/events`, { signal: abort.signal })
      .then(page => { if (!abort.signal.aborted) setEvents(page.events) })
      .catch(reason => { if (!abort.signal.aborted) setError(errorMessage(reason)) })
    void load()
    const timer = window.setInterval(() => void load(), 5000)
    return () => { abort.abort(); window.clearInterval(timer) }
  }, [selectedId])

  const selected = runs.find(run => run.id === selectedId) ?? null
  const current = runs.find(run => run.status === 'succeeded') ?? null
  const previous = current?.previous_release_id
    ? runs.find(run => run.release_id === current.previous_release_id && run.status === 'succeeded') ?? null
    : null
  const active = runs.some(run => ['queued', 'validating', 'retry_wait', 'waiting_for_executor', 'checking', 'applying', 'monitoring', 'checking_health', 'reconciling'].includes(run.status))

  async function act(path: string, body: object = {}) {
    setBusy(true); setError('')
    try {
      await command(path, body)
      setConfirmRollback(false)
      setRevision(value => value + 1)
    } catch (reason) { setError(errorMessage(reason)) }
    finally { setBusy(false) }
  }

  async function setDeploymentPolicy(enabled: boolean) {
    if (!applicationId) return
    setBusy(true); setError('')
    try {
      const next = await command<Policy>(`/applications/${applicationId}/deployment-policy`, { enabled }, 'PUT')
      setPolicy({ ...next, execution_available: true })
    } catch (reason) { setError(errorMessage(reason)) }
    finally { setBusy(false) }
  }

  return <>
    <header className="page-header"><div><p className="page-eyeline"><Link to="/services"><ArrowLeft size={13} /> Applications</Link></p><h1>{application?.name ?? 'Application lifecycle'}</h1><p className="page-subtitle">Deployment history and the current Kubernetes release in deployflow-test.</p></div><button type="button" className="button button-secondary" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={15} /> Refresh</button></header>
    <ErrorNotice message={error} />
    {application && <section className="lifecycle-source"><span>Repository <code>{application.repository_url}</code></span><span>Revision <code>{application.revision}</code></span><Link to={`/deploy?application=${application.id}`}>Open workflow <ArrowRight size={14} /></Link></section>}
    <section className="services-table-wrap lifecycle-history"><div className="table-heading"><div><h2>Deployment history</h2><p>{runs.length} stored release{runs.length === 1 ? '' : 's'} · newest first</p></div></div>
      {loading && !runs.length ? <p className="empty-state" role="status">Loading deployments…</p> : !runs.length ? <p className="empty-state">No deployment has been prepared yet. Open the workflow to review the repository and create a PR.</p> : <table><thead><tr><th>Release</th><th>Status</th><th>Image</th><th>Created</th><th></th></tr></thead><tbody>{runs.map(run => <tr key={run.id}><td><b>{run.kind === 'rollback' ? 'Manual rollback' : 'Deployment'}</b><small className="lifecycle-subline"><code>{run.id}</code></small></td><td>{run.status}{run.error_code ? <small className="lifecycle-subline">{run.error_code}</small> : null}</td><td className="image-name" title={run.image_reference}><code>{run.image_reference}</code></td><td>{new Date(run.created_at).toLocaleString()}</td><td><button className="button button-ghost" type="button" onClick={() => setSelectedId(run.id)}>Details</button></td></tr>)}</tbody></table>}
    </section>
    {selected && <section className="lifecycle-detail" aria-label="Selected deployment"><div className="lifecycle-detail-header"><div><h2>{selected.kind === 'rollback' ? 'Rollback' : 'Deployment'} details</h2><p><code>{selected.id}</code> · {selected.status}</p></div></div>
      <div className="lifecycle-grid"><div><b>Release</b><p>Digest <code>{selected.release_digest}</code></p><p>Image <code>{selected.image_reference}</code></p><p>Target {selected.environment_profile_id}</p></div><div><b>Validation and health</b><p>Admission: {selected.preflight_validation?.status ?? 'pending'}</p><p>Rollout: {selected.rollout?.status ?? 'pending'}{selected.rollout ? ` · ${selected.rollout.ready_replicas}/${selected.rollout.desired_replicas} ready` : ''}</p><p>Health: {selected.health?.status ?? 'pending'}{selected.health?.code ? ` · ${selected.health.code}` : ''}</p></div></div>
      {selected.resources.length > 0 && <div><b>Managed resource journal</b><ul>{selected.resources.map(row => <li key={`${row.kind}:${row.name}`}>{row.kind}/{row.name}: {row.status}{row.error_code ? ` · ${row.error_code}` : ''}</li>)}</ul></div>}
      {events.length > 0 && <div><b>Progress events</b><ol>{events.map(event => <li key={event.sequence}>{new Date(event.occurred_at).toLocaleString()} · {event.type.replace('deployment.', '')}{event.error_code ? ` · ${event.error_code}` : ''}</li>)}</ol></div>}
      {canDeploy && policy?.enabled && selected.status === 'waiting_for_executor' && <button type="button" className="button button-primary" disabled={busy} onClick={() => void act(`/deployments/${selected.id}/execute`)}>Deploy this prepared release</button>}
      {canDeploy && policy?.enabled && selected.status === 'reconciling' && selected.execution_job?.purpose === 'deploy' && <button type="button" className="button button-secondary" disabled={busy} onClick={() => void act(`/deployments/${selected.id}/retry-execution`)}>Retry reconciliation</button>}
      {canDeploy && selected.status === 'blocked' && !selected.execution_job && <button type="button" className="button button-secondary" disabled={busy} onClick={() => void act(`/deployments/${selected.id}/retry-preflight`)}>Retry preflight</button>}
      {canDeploy && policy?.enabled && policy.cleanup_available && selected.status === 'reconciling' && selected.previous_release_id === null && selected.resources.length > 0 && <div className="lifecycle-rollback"><h3>Clean up a failed first deployment</h3><p>There is no prior release to restore. Cleanup deletes only the {selected.resources.length} resource{selected.resources.length === 1 ? '' : 's'} below after checking their live ownership and UID. It does not delete databases or unmanaged objects.</p><ul>{selected.resources.map(row => <li key={`${row.kind}:${row.name}`}>{row.kind}/{row.name} · UID {row.uid ?? 'not recorded'}</li>)}</ul>{confirmCleanup ? <div className="lifecycle-confirm"><button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmCleanup(false)}>Cancel</button><button type="button" className="button button-primary" disabled={busy} onClick={() => void act(`/deployments/${selected.id}/cleanup`, { expected_release_digest: selected.release_digest, resources: selected.resources.map(row => ({ kind: row.kind, name: row.name, uid: row.uid })) })}>Confirm inventory cleanup</button></div> : <button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmCleanup(true)}>Review cleanup</button>}</div>}
    </section>}
    {canDeploy && <section className="lifecycle-actions"><h2>Deployment controls</h2><p>{policy?.execution_available ? 'Changes here affect only this application.' : 'Deployment execution is not configured for this backend.'}</p>{policy?.execution_available && <button className="button button-secondary" type="button" disabled={busy} onClick={() => void setDeploymentPolicy(!policy.enabled)}>{policy.enabled ? 'Disable future automatic deployments' : 'Enable deployment for this application'}</button>}
      {policy?.execution_available && policy.enabled && current && previous && !active && <div className="lifecycle-rollback"><h3>Return to the previous release</h3><p>The current release is <code>{current.image_reference}</code>. The previous successful release is <code>{previous.image_reference}</code>. Rollback creates a new release and runs fresh Kubernetes admission before any write. It does not change application database data.</p>{confirmRollback ? <div className="lifecycle-confirm"><button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmRollback(false)}>Cancel</button><button type="button" className="button button-primary" disabled={busy} onClick={() => void act(`/deployments/${current.id}/rollback`)}><RotateCcw size={15} /> Confirm rollback</button></div> : <button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmRollback(true)}><RotateCcw size={15} /> Review rollback</button>}</div>}
      {current && !previous && <p>No previous successful release exists for rollback.</p>}
    </section>}
  </>
}
