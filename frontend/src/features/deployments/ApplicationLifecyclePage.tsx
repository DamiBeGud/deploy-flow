import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, RefreshCw, RotateCcw } from 'lucide-react'
import { command, errorMessage, request } from '../../shared/api'
import type { ApplicationDetail, DeploymentRun } from '../../shared/contracts'
import { deploymentChecks, deploymentLabel, statusLabel } from '../../shared/deploymentStatus'
import { ReleaseChecklist } from '../../shared/ReleaseChecklist'
import { ErrorNotice } from '../../shared/ui'
import { DeploymentPolicyControl } from './DeploymentPolicyControl'
import type { Policy, PolicyScope } from './DeploymentPolicyControl'

type Observation = { status: string; observed_at?: string | null; code?: string; ready_replicas?: number; desired_replicas?: number; available_replicas?: number; image?: string }
type ReleaseStatus = { status: string; source_sha?: string; baseline_sha?: string; error_code?: string; preparation?: { status: string; error_code?: string }; deployment?: { id: string; release_id: string; status: string; error_code?: string }; build?: { source_sha: string; image_reference: string; run_id: number; run_attempt: number; artifact_id?: number } }
type EventPage = { events: { sequence: number; type: string; occurred_at: string; error_code?: string | null }[] }

export function ApplicationLifecyclePage({ canDeploy }: { canDeploy: boolean }) {
  const { applicationId } = useParams()
  const [params] = useSearchParams()
  const proposedPrHead = params.get('reviewedPr')
  const reviewedPr = proposedPrHead && /^[a-f0-9]{40}$/.test(proposedPrHead) ? proposedPrHead : undefined
  const [application, setApplication] = useState<ApplicationDetail | null>(null)
  const [runs, setRuns] = useState<DeploymentRun[]>([])
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [observation, setObservation] = useState<Observation | null>(null)
  const [releaseStatus, setReleaseStatus] = useState<ReleaseStatus | null>(null)
  const [eventLog, setEventLog] = useState<{ id: string; entries: EventPage['events'] } | null>(null)
  const [confirmRollback, setConfirmRollback] = useState(false)
  const [confirmCleanup, setConfirmCleanup] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [observedNow, setObservedNow] = useState(() => Date.now())
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    if (!applicationId) return
    const abort = new AbortController()
    // A new application or explicit refresh starts a new read cycle.
    // eslint-disable-next-line react/set-state-in-effect
    setLoading(true)
    let timer = 0
    let failures = 0
    const load = async () => {
      let delay = 5000
      if (document.hidden) { timer = window.setTimeout(() => void load(), 30000); return }
      try {
        const [app, history, setting] = await Promise.all([
          request<ApplicationDetail>(`/applications/${applicationId}`, { signal: abort.signal }),
          request<{ deployments: DeploymentRun[] }>(`/applications/${applicationId}/deployments`, { signal: abort.signal }),
          request<Policy>(`/applications/${applicationId}/deployment-policy`, { signal: abort.signal }),
        ])
        const [live, source] = app.lifecycle_version === 2 ? await Promise.all([
          request<Observation>(`/applications/${applicationId}/observation`, { signal: abort.signal }),
          request<ReleaseStatus>(`/applications/${applicationId}/release-status`, { signal: abort.signal }),
        ]) : [{ status: 'unavailable', code: 'backend_upgrade_required' }, { status: 'backend_upgrade_required' }]
        if (!abort.signal.aborted) {
          setObservedNow(Date.now())
          setApplication(app); setRuns(history.deployments); setPolicy(setting)
          setObservation(live); setReleaseStatus(source); setError(''); failures = 0
          delay = history.deployments.some(run => ['queued', 'validating', 'checking', 'applying', 'monitoring', 'checking_health', 'retry_wait'].includes(run.status)) ? 5000 : 30000
        }
      } catch (reason) {
        if (!abort.signal.aborted) {
          setError(errorMessage(reason)); setObservation({ status: 'unavailable', code: 'refresh_failed' })
          failures++; delay = Math.min(5000 * 2 ** failures, 60000)
        }
      } finally {
        if (!abort.signal.aborted) {
          setLoading(false)
          // Schedule after completion: requests cannot overlap. Hidden tabs do
          // no polling; the next visible cycle resumes after at most 30 seconds.
          timer = window.setTimeout(() => void load(), delay)
        }
      }
    }
    void load()
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [applicationId, revision])

  const selected = runs.find(run => run.id === selectedId) ?? runs[0] ?? null
  const selectedRunId = selected?.id
  const selectedSequence = selected?.event_sequence
  useEffect(() => {
    if (!selectedRunId) return
    const abort = new AbortController()
    request<EventPage>(`/deployments/${selectedRunId}/events`, { signal: abort.signal })
      .then(page => { if (!abort.signal.aborted) setEventLog({ id: selectedRunId, entries: page.events }) })
      .catch(reason => { if (!abort.signal.aborted) setError(errorMessage(reason)) })
    return () => abort.abort()
  }, [selectedRunId, selectedSequence])

  const current = runs.find(run => run.status === 'succeeded') ?? null
  const previous = current?.previous_release_id
    ? runs.find(run => run.release_id === current.previous_release_id && run.status === 'succeeded') ?? null
    : null
  const active = runs.some(run => ['queued', 'validating', 'retry_wait', 'waiting_for_executor', 'checking', 'applying', 'monitoring', 'checking_health', 'reconciling', 'cleanup_pending', 'cleaning'].includes(run.status))

  async function act(path: string, body: object = {}) {
    setBusy(true); setError('')
    try {
      await command(path, body)
      setConfirmRollback(false)
      setRevision(value => value + 1)
    } catch (reason) { setError(errorMessage(reason)) }
    finally { setBusy(false) }
  }

  async function setDeploymentPolicy(enabled: boolean, scope: PolicyScope = {}) {
    if (!applicationId) return
    setBusy(true); setError('')
    try {
      const next = await command<Policy>(`/applications/${applicationId}/deployment-policy`, { enabled, ...scope }, 'PUT')
      setPolicy(previous => previous ? { ...previous, ...next } : next)
      setRevision(value => value + 1)
    } finally { setBusy(false) }
  }

  if (!application || application.id !== applicationId) return <>
    <header className="page-header"><div><p className="page-eyeline"><Link to="/services"><ArrowLeft size={13} /> Applications</Link></p><h1>Application</h1></div></header>
    <ErrorNotice message={error} />
    {loading && <p role="status">Loading application…</p>}
    {!loading && error && <button type="button" className="button button-secondary" onClick={() => setRevision(value => value + 1)}>Retry loading</button>}
  </>

  const latest = runs[0]
  const candidate = releaseStatus?.deployment
  const newerCandidate = !candidate && releaseStatus?.source_sha && releaseStatus.source_sha !== latest?.source_sha
  const events = eventLog?.id === selectedRunId ? eventLog?.entries ?? [] : []
  const stale = Boolean(observation?.observed_at && observedNow - Date.parse(observation.observed_at) > 60000)
  const readiness = !observation ? 'Checking cluster…'
    : stale || observation.status === 'unavailable' ? 'Current readiness unknown'
    : observation.desired_replicas === 0 && ['ready', 'pending'].includes(observation.status) ? 'Scaled to zero'
    : statusLabel(observation.status)

  return <>
    <header className="page-header">
      <div><p className="page-eyeline"><Link to="/services"><ArrowLeft size={13} /> Applications</Link></p><h1>{application.name}</h1></div>
      <button type="button" className="button button-secondary" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={15} />Refresh deployment status</button>
    </header>
    <ErrorNotice message={error} />
    <section className="lifecycle-source" aria-label="Application source">
      <span>{application.repository_url.replace(/^https:\/\/github\.com\//, '')}</span><span>Branch or revision <code>{application.revision}</code></span>
      <Link to={`/deploy?application=${application.id}`}>Open setup <ArrowRight size={14} /></Link>
    </section>
    <div className="lifecycle-summary">
      <section className="lifecycle-detail" aria-label="Current cluster state">
        <h2>Current cluster state</h2>
        <strong>{readiness}</strong>
        {!stale && observation?.ready_replicas != null && observation.desired_replicas !== 0 && <p>{observation.ready_replicas}/{observation.desired_replicas ?? 'unknown'} replicas ready</p>}
        {observation?.observed_at && <p>{stale ? 'Last observed' : 'Observed'} {new Date(observation.observed_at).toLocaleString()}</p>}
        {(observation?.image || observation?.code) && <details className="workflow-details"><summary>Observation details</summary>
          {observation.image && <p>Observed image <code>{observation.image}</code></p>}{observation.code && <p>Code <code>{observation.code}</code></p>}
        </details>}
      </section>
      <section className="lifecycle-detail" aria-label="Latest release attempt">
        <h2>Latest release attempt</h2>
        <strong>{candidate ? deploymentLabel(candidate.status, candidate.error_code)
          : newerCandidate ? statusLabel(releaseStatus?.status ?? 'pending')
          : latest ? deploymentLabel(latest.status, latest.error_code)
          : releaseStatus && releaseStatus.status !== 'not_started' ? statusLabel(releaseStatus.status) : 'No release yet'}</strong>
        {releaseStatus?.preparation && <p>Release preparation {statusLabel(releaseStatus.preparation.status).toLowerCase()}</p>}
        {releaseStatus?.build && <p>CI run {releaseStatus.build.run_id}, attempt {releaseStatus.build.run_attempt}</p>}
      </section>
      <section className="lifecycle-detail" aria-label="Last successful deployment">
        <h2>Last successful deployment</h2>
        {current ? <><strong>{new Date(current.updated_at ?? current.created_at).toLocaleString()}</strong>
          {current.source_sha && <p>Source <code title={current.source_sha}>{current.source_sha.slice(0, 12)}</code></p>}</>
          : <strong>No successful deployment recorded</strong>}
      </section>
    </div>
    <section className="services-table-wrap lifecycle-history" aria-label="Deployment history" tabIndex={0}>
      <div className="table-heading"><div><h2>Deployment history</h2><p>{runs.length} {runs.length === 1 ? 'deployment' : 'deployments'}</p></div></div>
      {loading && !runs.length ? <p className="empty-state" role="status">Loading deployments…</p>
        : !runs.length ? <p className="empty-state">Open setup to review a bundle and create a pull request.</p>
        : <table><thead><tr><th>Attempt</th><th>Outcome</th><th>Created</th><th>Checks</th></tr></thead><tbody>
          {runs.map(run => <tr key={run.id}>
            <td><b>{run.kind === 'rollback' ? 'Manual rollback' : 'Deployment'}</b>{run.source_sha && <small className="lifecycle-subline"><code>{run.source_sha.slice(0, 12)}</code></small>}</td>
            <td>{deploymentLabel(run.status, run.error_code)}</td><td>{new Date(run.created_at).toLocaleString()}</td>
            <td><button className="button button-ghost" type="button" aria-pressed={selectedRunId === run.id}
              aria-label={`Inspect deployment ${run.id.slice(0, 8)}`} onClick={() => { setSelectedId(run.id); setConfirmCleanup(false) }}>Inspect</button></td>
          </tr>)}
        </tbody></table>}
    </section>
    {selected && <section className="lifecycle-detail" aria-label="Selected deployment">
      <div className="lifecycle-detail-header"><h2>{selected.kind === 'rollback' ? 'Rollback checks' : 'Deployment checks'}</h2>
        <p><b>{deploymentLabel(selected.status, selected.error_code)}</b></p></div>
      <ReleaseChecklist label="Recorded deployment checks" checks={deploymentChecks(selected)} />
      {selected.rollout?.observed_at && <p>Rollout recorded {new Date(selected.rollout.observed_at).toLocaleString()}</p>}
      <details className="workflow-details"><summary>Release evidence</summary>
        <p>Deployment <code>{selected.id}</code> · Target <code>{selected.environment_profile_id}</code></p>
        <p>Release <code>{selected.release_id}</code> · Digest <code>{selected.release_digest}</code></p>
        <p>Source <code>{selected.source_sha ?? 'Unavailable'}</code></p>
        <p>Image <code>{selected.image_reference}</code></p>
        {selected.error_code && <p>Error code <code>{selected.error_code}</code></p>}
        {selected.rollout?.ready_replicas != null && <p>Recorded replicas {selected.rollout.ready_replicas}/{selected.rollout.desired_replicas ?? 'unknown'} ready</p>}
        {selected.health?.path && <p>Health endpoint <code>{selected.health.service}{selected.health.path}</code></p>}
        {selected.resources.length > 0 && <div><b>Managed resources</b><ul>{selected.resources.map(row => <li key={`${row.kind}:${row.name}`}>{row.kind}/{row.name} · {statusLabel(row.status)}{row.error_code ? ` · ${row.error_code}` : ''}</li>)}</ul></div>}
        {events.length > 0 && <div><b>Progress events</b><ol>{events.map(event => <li key={event.sequence}>{new Date(event.occurred_at).toLocaleString()} · {statusLabel(event.type.replace('deployment.', ''))}{event.error_code ? ` · ${event.error_code}` : ''}</li>)}</ol></div>}
      </details>
      <div className="inline-actions">
      {canDeploy && policy?.enabled && selected.status === 'waiting_for_executor' && <button type="button" className="button button-primary" disabled={busy} onClick={() => void act(`/deployments/${selected.id}/execute`)}>Deploy this prepared release</button>}
      {canDeploy && policy?.enabled && selected.status === 'reconciling' && selected.execution_job?.purpose === 'deploy' && <button type="button" className="button button-secondary" disabled={busy} onClick={() => void act(`/deployments/${selected.id}/retry-execution`)}>Retry deployment</button>}
      {canDeploy && selected.status === 'blocked' && !selected.execution_job && <button type="button" className="button button-secondary" disabled={busy} onClick={() => void act(`/deployments/${selected.id}/retry-preflight`)}>Retry Kubernetes admission</button>}
      {canDeploy && policy?.enabled && policy.cleanup_available && selected.status === 'reconciling' && selected.previous_release_id === null && selected.resources.length > 0 && <div className="lifecycle-rollback"><h3>Clean up a failed first deployment</h3><p>There is no prior release to restore. Cleanup deletes only the {selected.resources.length} resource{selected.resources.length === 1 ? '' : 's'} below after checking their live ownership and UID. It does not delete databases or unmanaged objects.</p><ul>{selected.resources.map(row => <li key={`${row.kind}:${row.name}`}>{row.kind}/{row.name} · UID {row.uid ?? 'not recorded'}</li>)}</ul>{confirmCleanup ? <div className="lifecycle-confirm"><button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmCleanup(false)}>Cancel</button><button type="button" className="button button-primary" disabled={busy} onClick={() => void act(`/deployments/${selected.id}/cleanup`, { expected_release_digest: selected.release_digest, resources: selected.resources.map(row => ({ kind: row.kind, name: row.name, uid: row.uid })) })}>Confirm inventory cleanup</button></div> : <button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmCleanup(true)}>Review cleanup</button>}</div>}
      </div>
    </section>}
    <section className="lifecycle-actions" id="deployment-controls" aria-label="Deployment controls">
      <h2>Deployment controls</h2>
      <p>{!policy ? 'Loading authorization…' : !policy.execution_available ? 'Deployment execution is unavailable.' : policy.enabled ? 'Automatic deployment is on.' : 'Automatic deployment is off.'}</p>
      {policy?.enabled && <p>{policy.allowed_source_sha
        ? <>Authorized {policy.source_compatibility_policy === 'reviewed-infrastructure-pr-v1' ? 'PR head' : 'source'} <code>{policy.allowed_source_sha}</code></>
        : 'Future compatible commits are authorized.'}</p>}
      {canDeploy && policy?.execution_available && <DeploymentPolicyControl key={`${applicationId}:${reviewedPr ?? ''}`} policy={policy}
        applicationName={application.name} candidateSource={releaseStatus?.source_sha ?? releaseStatus?.build?.source_sha}
        reviewedPr={reviewedPr} busy={busy} onSave={setDeploymentPolicy} />}
      {canDeploy && releaseStatus?.baseline_sha && <details className="workflow-details"><summary>Release controls and build evidence</summary>
        <p>Checking GitHub can queue an eligible release when automatic deployment is on.</p>
        <button className="button button-secondary" type="button" disabled={busy} onClick={() => void act(`/applications/${applicationId}/reconcile-source`)}>Check GitHub for a release</button>
        <p>Approved baseline <code>{releaseStatus.baseline_sha}</code></p>
        {releaseStatus.source_sha && <p>Candidate source <code>{releaseStatus.source_sha}</code></p>}
        {releaseStatus.error_code && <p>Verification code <code>{releaseStatus.error_code}</code></p>}
        {releaseStatus.build && <p>Verified image <code>{releaseStatus.build.image_reference}</code> · Artifact {releaseStatus.build.artifact_id ?? 'Unavailable'}</p>}
      </details>}
      {canDeploy && policy?.execution_available && policy.enabled && current && previous && !active && <div className="lifecycle-rollback"><h3>Return to the previous release</h3><p>The current release is <code>{current.image_reference}</code>. The previous successful release is <code>{previous.image_reference}</code>. Rollback creates a new release and runs fresh Kubernetes admission before any write. It does not change application database data.</p>{confirmRollback ? <div className="lifecycle-confirm"><button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmRollback(false)}>Cancel</button><button type="button" className="button button-primary" disabled={busy} onClick={() => void act(`/deployments/${current.id}/rollback`)}><RotateCcw size={15} /> Confirm rollback</button></div> : <button type="button" className="button button-secondary" disabled={busy} onClick={() => setConfirmRollback(true)}><RotateCcw size={15} /> Review rollback</button>}</div>}
    </section>
  </>
}
