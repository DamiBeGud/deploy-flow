import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Bot, Check, GitBranch, Network, Sparkles } from 'lucide-react'
import { command, errorMessage, request } from '../../shared/api'
import type { Analysis, Application, ApplicationDetail, QueuedAnalysis } from '../../shared/contracts'
import { isTerminal } from '../../shared/contracts'
import { ErrorNotice, Field } from '../../shared/ui'
import { useAnalysis } from './useAnalysis'

const steps = ['Source', 'Connectivity', 'Workflow', 'Review']

function AssessmentEditor({ run, canWrite, onSaved }: { run: Analysis; canWrite: boolean; onSaved: () => void }) {
  const deterministic = run.assessment?.mode === 'deterministic' || run.assessment?.mode === 'agent' ? run.assessment : null
  const [port, setPort] = useState(String(deterministic?.review.port ?? (run.assessment?.mode === 'fake' ? run.assessment.port.value : '')))
  const [health, setHealth] = useState(String(deterministic?.review.health_path ?? (run.assessment?.mode === 'fake' ? run.assessment.health_path.value : '')))
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const lock = useRef(false)
  async function save(event: FormEvent) {
    event.preventDefault()
    if (lock.current) return
    lock.current = true; setBusy(true); setError('')
    try {
      await command(`/analyses/${run.id}/assessment`, { expected_version: run.assessment_version, port: Number(port), health_path: health }, 'PATCH')
      onSaved()
    } catch (e) { setError(errorMessage(e)) } finally { lock.current = false; setBusy(false) }
  }
  return <form onSubmit={save}>
    <div className="agent-panel"><div className="agent-panel-heading"><div><span className="bot-icon"><Sparkles size={18} /></span><div><b>Assessment version {run.assessment_version}</b><p>{deterministic ? `Pinned commit ${deterministic.repository.commit_sha.slice(0, 12)}; repository inspected read-only.${deterministic.mode === 'agent' ? ' Selected excerpts were analyzed by DeepSeek.' : ''}` : 'Legacy simulated fixture result.'}</p></div></div><span className="confidence">{run.status === 'needs_input' ? 'Needs input' : deterministic ? deterministic.completion : 'Simulated'}</span></div>
      <div className="findings-grid"><div className="ai-finding"><Bot size={18} /><div><small>Runtime</small><b>{deterministic?.review.runtime ?? (run.assessment?.mode === 'fake' ? run.assessment.runtime.value : '')}</b><p>{deterministic ? deterministic.detected_stack.frameworks.join(', ') || 'Detected' : 'Simulated'}</p></div></div><div className="ai-finding"><Network size={18} /><div><small>Service port</small><b>{deterministic?.review.port ?? (run.assessment?.mode === 'fake' ? run.assessment.port.value : '')}</b><p>{deterministic ? 'Repository evidence' : 'Simulated default'}</p></div></div></div>
    </div>
    <div className="form-grid"><Field label="Service port"><input type="number" min="1" max="65535" step="1" required value={port} onChange={e => setPort(e.target.value)} disabled={!canWrite || busy} /></Field><Field label="Health check path"><input required pattern="/[^\s]*" maxLength={255} value={health} onChange={e => setHealth(e.target.value)} disabled={!canWrite || busy} /></Field></div>
    <ErrorNotice message={error} />
    <div className="inline-actions"><button className="button button-primary" disabled={!canWrite || busy} type="submit">{busy ? 'Saving…' : 'Save assessment'}</button><button className="button button-secondary" type="button" disabled={busy} onClick={onSaved}>Reload latest assessment</button><span>Version {run.assessment_version} · Changes are saved to PostgreSQL</span></div>
    <div className="section-divider"><span>Configuration requirements</span></div>
    <p>Environment variable names: {deterministic ? deterministic.configuration.optional_variables.join(', ') : (run.assessment?.mode === 'fake' ? run.assessment.environment_variable_names.join(', ') : '') || 'None reported'}</p>
    {deterministic?.summary && <p>{deterministic.summary}</p>}
    {deterministic?.questions?.length ? <section className="review-section"><h3>Questions to resolve</h3><ul className="unknowns">{deterministic.questions.map(question => <li key={question.id}><b>{question.required ? 'Required: ' : ''}{question.prompt}</b><br /><small>{question.reason} Answering questions is added in the next review phase.</small></li>)}</ul></section> : null}
    {deterministic?.findings?.length ? <section className="review-section"><h3>Evidence-backed findings</h3><ul className="unknowns">{deterministic.findings.map(finding => <li key={finding.id}>{finding.category}: {String(finding.value)} · {finding.status} · {Math.round(finding.confidence * 100)}%</li>)}</ul></section> : null}
    {deterministic && <p>Evidence: {deterministic.evidence.map(e => `${e.file_path}${e.line_start ? `:${e.line_start}` : ''}`).join(', ') || 'No source locations retained'}</p>}
    <ul className="unknowns">{run.assessment?.unknowns.map(unknown => <li key={unknown}>{unknown}</li>)}</ul>
    <div className="section-divider"><span>Deployment configuration — upcoming</span></div>
    <p className="page-subtitle">Network exposure, resources and dependencies will be editable when the deployment-intent API is available.</p>
    <fieldset disabled className="future-settings"><div className="form-grid three"><Field label="Replicas"><input placeholder="Not configured" /></Field><Field label="CPU request"><input placeholder="Not configured" /></Field><Field label="Memory request"><input placeholder="Not configured" /></Field><Field label="Exposure"><select><option>Internal / external — unavailable</option></select></Field><Field label="Public hostname"><input placeholder="app.example.com" /></Field><Field label="Ingress class"><input placeholder="Requires cluster capabilities" /></Field><Field label="TLS secret reference"><input placeholder="Pre-created certificate secret" /></Field><Field label="Database / queue connection"><input placeholder="Requires connection catalogue" /></Field></div></fieldset>
  </form>
}

export function AnalysisPage({ canWrite }: { canWrite: boolean }) {
  const [params, setParams] = useSearchParams()
  const appId = params.get('application')
  const runId = params.get('run')
  const [application, setApplication] = useState<Application | null>(null)
  const [source, setSource] = useState({ name: 'node-express-service', repository_url: 'https://github.com/DamiBeGud/deployflow-node-express-fixture', revision: 'main' })
  const [step, setStep] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(Boolean(appId))
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [notice, setNotice] = useState('')
  const mutation = useRef(false)
  const { run, error: runError, message, connection, reload } = useAnalysis(runId)

  useEffect(() => {
    // Route changes replace the externally loaded application and its local draft.
    // eslint-disable-next-line react/set-state-in-effect
    if (!appId) { setApplication(null); setStep(0); setLoading(false); setError(''); return }
    const abort = new AbortController()
    // eslint-disable-next-line react/set-state-in-effect
    setLoading(true); setError('')
    request<ApplicationDetail>(`/applications/${appId}`, { signal: abort.signal }).then(data => {
      if (abort.signal.aborted) return
      setApplication(data)
      setSource({ name: data.name, repository_url: data.repository_url, revision: data.revision })
      if (!runId && data.latest_analysis) setParams({ application: appId, run: data.latest_analysis.id }, { replace: true })
    }).catch(e => { if (!abort.signal.aborted) setError(errorMessage(e)) })
      .finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [appId, runId, loadAttempt, setParams])

  async function perform(action: () => Promise<void>) {
    if (mutation.current) return
    mutation.current = true; setBusy(true); setError(''); setNotice('')
    try { await action() } catch (e) { setError(errorMessage(e)) }
    finally { mutation.current = false; setBusy(false) }
  }
  async function analyze(event: FormEvent) {
    event.preventDefault()
    await perform(async () => {
      let current = application
      if (!current) {
        if (appId) throw new Error('Load the application before starting analysis.')
        current = await command<Application>('/applications', source)
        setApplication(current)
        setParams({ application: current.id }, { replace: true })
      }
      const queued = await command<QueuedAnalysis>(`/applications/${current.id}/analyses`, {})
      setParams({ application: current.id, run: queued.run_id }, { replace: true })
    })
  }
  const active = Boolean(run && !isTerminal(run.status))
  const matchesApplication = !run || run.application_id === appId
  const ready = (run?.status === 'completed' || run?.status === 'needs_input') && matchesApplication
  const canAnalyze = canWrite && !busy && !loading && !runId && (!appId || Boolean(application))
  return <>
    <header className="page-header"><div><p className="page-eyeline">Application requirements</p><h1>{application?.name || 'Add application'}</h1><p className="page-subtitle">Register a repository, run analysis, and review its requirements.</p></div><Link className="button button-ghost" to="/services">View applications</Link></header>
    <ol className="stepper">{steps.map((name, index) => <li key={name} className={index === step ? 'active' : index < step ? 'complete' : ''}><button onClick={() => setStep(index)} disabled={index > 0 && !ready}><span>{index < step ? <Check size={14} /> : index + 1}</span><b>{name}</b></button>{index < steps.length - 1 && <i />}</li>)}</ol>
    <ErrorNotice message={error || runError || (!matchesApplication ? 'This analysis belongs to a different application.' : '')} />
    {(error || runError) && <button className="button button-secondary" onClick={() => { setLoadAttempt(n => n + 1); reload() }}>Retry loading</button>}
    {notice && <p className="integration-notice" role="status">{notice}</p>}
    <section className="wizard-card"><div className="wizard-step">
      {step === 0 ? <><div className="step-intro"><h2>Connect your source</h2><p>DeployFlow pins a GitHub revision and performs deterministic, read-only repository analysis.</p></div>
        {loading && <p role="status">Loading application…</p>}
        <form onSubmit={analyze}><fieldset disabled={Boolean(appId) || busy || !canWrite} className="source-fields"><div className="form-grid"><Field label="GitHub repository URL"><input type="url" required maxLength={512} pattern="https://github.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+/?" value={source.repository_url} onChange={e => setSource({ ...source, repository_url: e.target.value })} /></Field><Field label="Application name"><input required pattern="[a-z]([a-z0-9\-]*[a-z0-9])?" maxLength={63} value={source.name} onChange={e => setSource({ ...source, name: e.target.value })} /></Field><Field label="Repository revision"><input required maxLength={255} value={source.revision} onChange={e => setSource({ ...source, revision: e.target.value })} /></Field></div></fieldset>
          {!runId && <button className="button button-primary" type="submit" disabled={!canAnalyze}><Sparkles size={16} />{busy ? 'Starting analysis…' : 'Analyze repository'}</button>}
        </form>
        {runId && matchesApplication && <div className="analysis-banner"><div><span className="bot-icon"><Bot size={19} /></span><div><b>Analysis {run?.status ?? 'loading'}</b><p>{message || 'Progress and results are stored in the backend.'}</p><small>{connection} · {run?.attempts ?? 0} attempt(s)</small></div></div><div className="analysis-progress"><progress max={100} value={run?.progress ?? 0} aria-label="Analysis progress" /><span>{run?.progress ?? 0}%</span></div></div>}
        {run?.error_code && <p role="alert">Analysis error: {run.error_message || run.error_code} You can start a new analysis.</p>}
        {run && matchesApplication && <div className="inline-actions">{active ? <button className="button button-secondary" disabled={busy || !canWrite} onClick={() => void perform(async () => { await command(`/analyses/${run.id}/cancel`); reload() })}>Cancel analysis</button> : <button className="button button-secondary" disabled={busy || !canWrite} onClick={() => void perform(async () => { const queued = await command<QueuedAnalysis>(`/analyses/${run.id}/reanalyze`, {}); setParams({ application: run.application_id, run: queued.run_id }, { replace: true }) })}>Reanalyze repository</button>}<code>Run {run.id}</code></div>}
      </> : step === 1 && ready && run ? <><div className="step-intro"><h2>Configure connectivity</h2><p>Review and save the configuration fields supported by this backend.</p></div><AssessmentEditor key={`${run.id}:${run.assessment_version}`} run={run} canWrite={canWrite} onSaved={() => { setNotice('Assessment refreshed from the backend.'); reload() }} /></> : step === 2 ? <><div className="step-intro"><h2>Delivery workflow</h2><p>Workflow generation is not available in Phase 3.</p></div><div className="integration-notice">Dockerfile, GitHub Actions, registry, and deployment configuration will be added through the infrastructure-plan API in a later phase.</div><fieldset disabled className="future-settings"><div className="form-grid"><Field label="Container image"><input placeholder="ghcr.io/owner/application" /></Field><Field label="Target environment"><input placeholder="Not configured" /></Field><Field label="Workflow"><input placeholder="Not generated" /></Field></div></fieldset></> : step === 3 && run ? <><div className="step-intro"><h2>Review saved assessment</h2><p>These values were loaded from the backend.</p></div><div className="review-grid"><section className="review-section"><h3>Source</h3><dl><dt>Repository</dt><dd>{application?.repository_url}</dd></dl><dl><dt>Revision</dt><dd>{run.revision}</dd></dl></section><section className="review-section"><h3>Assessment version {run.assessment_version}</h3><dl><dt>Port</dt><dd>{(run.assessment?.mode === 'fake' ? run.assessment.port.value : run.assessment?.review.port) ?? 'Unknown'}</dd></dl><dl><dt>Health check</dt><dd>{(run.assessment?.mode === 'fake' ? run.assessment.health_path.value : run.assessment?.review.health_path) ?? 'Unknown'}</dd></dl><dl><dt>Analysis mode</dt><dd>{run.analysis_mode ?? 'unknown'}</dd></dl></section></div><div className="review-callout"><GitBranch size={20} /><div><b>Infrastructure PR — not available yet</b><p>No delivery files, pull request, or deployment have been created. Your application and assessment are saved.</p></div></div></> : <p role="status">Loading assessment…</p>}
    </div></section>
    <div className="wizard-actions">{step > 0 ? <button className="button button-ghost" onClick={() => setStep(n => n - 1)}><ArrowLeft size={16} />Back</button> : <Link className="button button-ghost" to="/services">Back to applications</Link>}{step < 3 ? <button className="button button-primary" disabled={!ready || busy} onClick={() => setStep(n => n + 1)}>Continue <ArrowRight size={16} /></button> : <button className="button button-primary" disabled>Create infrastructure PR — coming soon</button>}</div>
  </>
}
