import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Bot, Check, GitBranch, Sparkles } from 'lucide-react'
import { command, errorMessage, request } from '../../shared/api'
import type { Application, ApplicationDetail, QueuedAnalysis } from '../../shared/contracts'
import { isTerminal } from '../../shared/contracts'
import { ErrorNotice, Field } from '../../shared/ui'
import { PlanWorkflow } from '../infrastructure/PlanWorkflow'
import { PlanFileReview } from '../infrastructure/PlanFileReview'
import { ClusterValidation } from '../infrastructure/ClusterValidation'
import { PullRequestReview } from '../infrastructure/PullRequestReview'
import type { DeploymentIntent, InfrastructurePlan } from '../infrastructure/contracts'
import { useAnalysis } from './useAnalysis'

const steps = ['Source', 'Kubernetes recommendation', 'Bundle', 'Review']

export function AnalysisPage({ canWrite, canDeploy }: { canWrite: boolean; canDeploy: boolean }) {
  const [params, setParams] = useSearchParams()
  const appId = params.get('application')
  const runId = params.get('run')
  const [application, setApplication] = useState<Application | null>(null)
  const [source, setSource] = useState({ name: 'node-express-service', repository_url: 'https://github.com/DamiBeGud/deployflow-node-express-fixture', revision: 'main' })
  const [step, setStep] = useState(0)
  const [furthestStep, setFurthestStep] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(Boolean(appId))
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [notice, setNotice] = useState('')
  const [reviewIntent, setReviewIntent] = useState<DeploymentIntent | null>(null)
  const [reviewPlan, setReviewPlan] = useState<InfrastructurePlan | null>(null)
  const mutation = useRef(false)
  const { run, error: runError, message, connection, reload } = useAnalysis(runId)

  function openStep(index: number) {
    setStep(index)
    setFurthestStep(previous => Math.max(previous, index))
  }

  useEffect(() => {
    // A different application or analysis starts a distinct wizard history.
    // eslint-disable-next-line react/set-state-in-effect
    setStep(0); setFurthestStep(0); setReviewIntent(null); setReviewPlan(null)
  }, [appId, runId])

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
  const canOpenStep = (index: number) => index <= furthestStep && (index === 0 || ready)

  useEffect(() => {
    if (!ready || !appId || !runId) return
    const abort = new AbortController()
    Promise.all([
      request<DeploymentIntent | null>(`/applications/${appId}/deployment-intent?analysis_run_id=${runId}`, { signal: abort.signal }),
      request<InfrastructurePlan | null>(`/applications/${appId}/infrastructure-plans/latest?analysis_run_id=${runId}`, { signal: abort.signal }),
    ]).then(([intent, plan]) => {
      if (abort.signal.aborted) return
      setReviewIntent(intent)
      setReviewPlan(plan)
      setFurthestStep(plan ? 3 : intent ? 2 : 1)
    }).catch(exception => { if (!abort.signal.aborted) setError(errorMessage(exception)) })
    return () => abort.abort()
  }, [appId, runId, ready])

  return <>
    <header className="page-header"><div><p className="page-eyeline">Application requirements</p><h1>{application?.name || 'Add application'}</h1><p className="page-subtitle">Register a repository, run analysis, and review its requirements.</p></div><Link className="button button-ghost" to="/services">View applications</Link></header>
    <ol className="stepper">{steps.map((name, index) => { const complete = index !== step && index <= furthestStep; return <li key={name} className={index === step ? 'active' : complete ? 'complete' : ''}><button onClick={() => setStep(index)} disabled={!canOpenStep(index)}><span>{complete ? <Check size={14} /> : index + 1}</span><b>{name}</b></button>{index < steps.length - 1 && <i />}</li> })}</ol>
    <ErrorNotice message={error || runError || (!matchesApplication ? 'This analysis belongs to a different application.' : '')} />
    {(error || runError) && <button className="button button-secondary" onClick={() => { setLoadAttempt(n => n + 1); reload() }}>Retry loading</button>}
    {notice && <p className="integration-notice" role="status">{notice}</p>}
    {ready && run && application && (step === 1 || step === 2) ? <PlanWorkflow application={application} run={run} canWrite={canWrite} stage={step === 1 ? 'connectivity' : 'workflow'} plan={reviewPlan} onContinue={() => openStep(Math.min(step + 1, 3))} onIntentChange={setReviewIntent} onPlanChange={setReviewPlan} /> : <section className="wizard-card"><div className="wizard-step">
      {step === 0 ? <><div className="step-intro"><h2>Connect your source</h2><p>DeployFlow pins a GitHub revision and performs deterministic, read-only repository analysis.</p></div>
        {loading && <p role="status">Loading application…</p>}
        <form onSubmit={analyze}><fieldset disabled={Boolean(appId) || busy || !canWrite} className="source-fields"><div className="form-grid"><Field label="GitHub repository URL"><input type="url" required maxLength={512} pattern="https://github.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+/?" value={source.repository_url} onChange={e => setSource({ ...source, repository_url: e.target.value })} /></Field><Field label="Application name"><input required pattern="[a-z]([a-z0-9\-]*[a-z0-9])?" maxLength={63} value={source.name} onChange={e => setSource({ ...source, name: e.target.value })} /></Field><Field label="Repository revision"><input required maxLength={255} value={source.revision} onChange={e => setSource({ ...source, revision: e.target.value })} /></Field></div></fieldset>
          {!runId && <button className="button button-primary" type="submit" disabled={!canAnalyze}><Sparkles size={16} />{busy ? 'Starting analysis…' : 'Analyze repository'}</button>}
        </form>
        {runId && matchesApplication && <div className="analysis-banner"><div><span className="bot-icon"><Bot size={19} /></span><div><b>Analysis {run?.status ?? 'loading'}</b><p>{message || 'Progress and results are stored in the backend.'}</p><small>{connection} · {run?.attempts ?? 0} attempt(s)</small></div></div><div className="analysis-progress"><progress max={100} value={run?.progress ?? 0} aria-label="Analysis progress" /><span>{run?.progress ?? 0}%</span></div></div>}
        {run?.error_code && <p role="alert">Analysis error: {run.error_message || run.error_code} You can start a new analysis.</p>}
        {run && matchesApplication && <div className="inline-actions">{active ? <button className="button button-secondary" disabled={busy || !canWrite} onClick={() => void perform(async () => { await command(`/analyses/${run.id}/cancel`); reload() })}>Cancel analysis</button> : <button className="button button-secondary" disabled={busy || !canWrite} onClick={() => void perform(async () => { const queued = await command<QueuedAnalysis>(`/analyses/${run.id}/reanalyze`, {}); setParams({ application: run.application_id, run: queued.run_id }, { replace: true }) })}>Reanalyze repository</button>}<code>Run {run.id}</code></div>}
      </> : step === 3 && run ? <><div className="step-intro"><h2>Review delivery plan</h2><p>Review the exact bundle below. The final action creates a GitHub PR; it does not deploy anything.</p></div><div className="review-grid"><section className="review-section"><h3>Source</h3><dl><dt>Repository</dt><dd>{application?.repository_url}</dd></dl><dl><dt>Revision</dt><dd>{run.revision}</dd></dl><dl><dt>Assessment</dt><dd>Version {run.assessment_version}</dd></dl></section><section className="review-section"><h3>Saved delivery settings</h3><dl><dt>Port</dt><dd>{reviewIntent?.connectivity.port ?? 'Not saved'}</dd></dl><dl><dt>Exposure</dt><dd>{reviewIntent?.connectivity.exposure.kind ?? 'Not saved'}</dd></dl><dl><dt>Image</dt><dd>{reviewIntent?.build.image ?? 'Not saved'}</dd></dl></section></div>{reviewPlan ? <section className="plan-preview"><h3>Bundle {reviewPlan.status}</h3><p>Digest: <code>{reviewPlan.bundle_digest}</code></p><p>Provisional image: <code>{reviewPlan.validation.image_binding.preview_reference}</code></p><ClusterValidation plan={reviewPlan} onPlanChange={setReviewPlan} canWrite={canWrite} /><PlanFileReview key={reviewPlan.id} plan={reviewPlan} /><PullRequestReview plan={reviewPlan} onPlanChange={setReviewPlan} canWrite={canWrite} canDeploy={canDeploy} /></section> : <div className="review-callout"><GitBranch size={20} /><div><b>No bundle has been generated</b><p>Return to Workflow, save the delivery settings, then generate a review bundle.</p></div></div>}</> : <p role="status">Loading assessment…</p>}
    </div></section>}
    <div className="wizard-actions page-actions">{step > 0 ? <button className="button button-ghost" onClick={() => setStep(n => n - 1)}><ArrowLeft size={16} />Back</button> : <Link className="button button-ghost" to="/services">Back to applications</Link>}{step === 0 && <button className="button button-primary" disabled={!ready || busy} onClick={() => openStep(1)}>Continue to connectivity</button>}</div>
  </>
}
