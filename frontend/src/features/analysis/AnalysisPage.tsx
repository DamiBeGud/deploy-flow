import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Check } from 'lucide-react'
import { command, errorMessage, request } from '../../shared/api'
import type { Application, ApplicationDetail, QueuedAnalysis } from '../../shared/contracts'
import { isTerminal } from '../../shared/contracts'
import { ErrorNotice, Field } from '../../shared/ui'
import { PlanWorkflow } from '../infrastructure/PlanWorkflow'
import { PlanFileReview } from '../infrastructure/PlanFileReview'
import { ClusterValidation } from '../infrastructure/ClusterValidation'
import { PullRequestReview } from '../infrastructure/PullRequestReview'
import type { DeploymentIntent, InfrastructurePlan } from '../infrastructure/contracts'
import { DatabaseSetupPending, deliveryRecommendation, generateDeliveryBundle, saveDeliveryIntent } from '../infrastructure/deliveryPreparation'
import { useAnalysis } from './useAnalysis'

const steps = [
  { slug: 'source', label: 'Source' },
  { slug: 'connectivity', label: 'Connectivity' },
  { slug: 'bundle', label: 'Bundle' },
  { slug: 'review', label: 'Review' },
]
const emptySource = { name: '', repository_url: '', revision: 'main' }
const analysisLabels = {
  queued: 'Analysis queued', running: 'Analyzing repository', completed: 'Analysis complete',
  needs_input: 'Review required', failed: 'Analysis failed', cancelled: 'Analysis cancelled',
}
const analysisModes: Readonly<Record<string, string>> = {
  deterministic: 'Repository scan', agent: 'Agent analysis', fake: 'Simulation',
}


export function AnalysisPage({ canWrite, canDeploy }: { canWrite: boolean; canDeploy: boolean }) {
  const [params, setParams] = useSearchParams()
  const appId = params.get('application')
  const runId = params.get('run')
  const autoPrepare = params.get('prepare') === '1'
  const [application, setApplication] = useState<Application | null>(null)
  const [draft, setDraft] = useState(emptySource)
  const [error, setError] = useState('')
  const [preparationFailure, setPreparationFailure] = useState<{ runId: string; message: string } | null>(null)
  const [databaseSetupRun, setDatabaseSetupRun] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(Boolean(appId))
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [review, setReview] = useState<{ runId: string | null; intent: DeploymentIntent | null; plan: InfrastructurePlan | null }>({ runId: null, intent: null, plan: null })
  const mutation = useRef(false)
  const focusStep = useRef(false)
  const { run: loadedRun, error: runError, message, connection, reload } = useAnalysis(runId)
  const run = loadedRun && (loadedRun.id === runId || loadedRun.run_id === runId) ? loadedRun : null
  const currentApp = application?.id === appId ? application : null
  const source = currentApp ?? (!appId ? draft : emptySource)
  const reviewIntent = review.runId === runId ? review.intent : null
  const reviewPlan = review.runId === runId ? review.plan : null
  const setReviewIntent = useCallback((intent: DeploymentIntent) => setReview(current => ({ runId, intent, plan: current.runId === runId ? current.plan : null })), [runId])
  const setReviewPlan = useCallback((plan: InfrastructurePlan) => setReview(current => ({ runId, plan, intent: current.runId === runId ? current.intent : null })), [runId])

  function openStep(index: number) {
    focusStep.current = true
    setParams(previous => {
      const next = new URLSearchParams(previous)
      next.set('step', steps[index].slug)
      return next
    })
  }

  useEffect(() => {
    // Route changes replace the externally loaded application and its local draft.
    // eslint-disable-next-line react/set-state-in-effect
    if (!appId) { setApplication(null); setDraft(emptySource); setLoading(false); setError(''); return }
    const abort = new AbortController()
    // eslint-disable-next-line react/set-state-in-effect
    setLoading(true); setError('')
    request<ApplicationDetail>(`/applications/${appId}`, { signal: abort.signal }).then(data => {
      if (abort.signal.aborted) return
      setApplication(data)
      if (!runId && data.latest_analysis) setParams(previous => {
        const next = new URLSearchParams(previous)
        next.set('run', data.latest_analysis!.id)
        return next
      }, { replace: true })
    }).catch(e => { if (!abort.signal.aborted) setError(errorMessage(e)) })
      .finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [appId, runId, loadAttempt, setParams])

  async function perform(action: () => Promise<void>) {
    if (mutation.current) return
    mutation.current = true; setBusy(true); setError('')
    try { await action() } catch (e) { setError(errorMessage(e)) }
    finally { mutation.current = false; setBusy(false) }
  }
  async function analyze(event: FormEvent) {
    event.preventDefault()
    const submitter = (event.nativeEvent as SubmitEvent).submitter
    const prepare = submitter?.getAttribute('value') !== 'analysis-only'
    await perform(async () => {
      let current = currentApp
      if (!current) {
        if (appId) throw new Error('Load the application before starting analysis.')
        const { name, ...repository } = draft
        current = await command<Application>('/applications', name ? { ...repository, name } : repository)
        setApplication(current)
      }
      try {
        const queued = await command<QueuedAnalysis>(`/applications/${current.id}/analyses`, {})
        setParams({ application: current.id, run: queued.run_id, step: 'source', ...(prepare ? { prepare: '1' } : {}) }, { replace: true })
      } catch (error) {
        // Recover the created application if only the analysis request failed.
        if (!appId) setParams({ application: current.id }, { replace: true })
        throw error
      }
    })
  }
  const active = Boolean(run && !isTerminal(run.status))
  const matchesApplication = !run || run.application_id === appId
  const ready = (run?.status === 'completed' || run?.status === 'needs_input') && matchesApplication
  const canAnalyze = canWrite && !busy && !loading && !runId && (!appId || Boolean(currentApp))
  const furthestStep = ready ? reviewPlan ? 3 : reviewIntent ? 2 : 1 : 0
  const requestedStep = steps.findIndex(item => item.slug === params.get('step'))
  const step = Math.min(requestedStep < 0 ? furthestStep : requestedStep, furthestStep)
  const completed = [ready, Boolean(reviewIntent), Boolean(reviewPlan && ['proposed', 'approved'].includes(reviewPlan.status)), reviewPlan?.status === 'approved']
  const mode = run?.analysis_mode ?? run?.assessment?.mode
  const needsRailsProfile = run?.status === 'needs_input' && run.assessment?.mode === 'agent' &&
    run.assessment.review?.runtime === 'ruby'
  const canPrepare = (run?.status === 'completed' || needsRailsProfile) && Boolean(run?.commit_sha?.match(/^[a-f0-9]{40}$/)) &&
    (mode === 'deterministic' || mode === 'agent') && run.assessment?.mode === mode &&
    run.assessment.completion === 'complete' && run.assessment.repository.commit_sha === run.commit_sha
  const preparing = canWrite && autoPrepare && ready
  const assessmentVersion = run?.assessment_version
  const analyzedRevision = run?.revision

  useEffect(() => {
    if (!focusStep.current) return
    focusStep.current = false
    document.getElementById('wizard-step-title')?.focus()
  }, [step])

  useEffect(() => {
    if (!ready || !appId || !runId) return
    const abort = new AbortController()
    Promise.all([
      request<DeploymentIntent | null>(`/applications/${appId}/deployment-intent?analysis_run_id=${runId}`, { signal: abort.signal }),
      request<InfrastructurePlan | null>(`/applications/${appId}/infrastructure-plans/latest?analysis_run_id=${runId}`, { signal: abort.signal }),
    ]).then(([intent, plan]) => {
      if (abort.signal.aborted) return
      setReview({ runId, intent, plan })
    }).catch(exception => { if (!abort.signal.aborted) setError(errorMessage(exception)) })
    return () => abort.abort()
  }, [appId, runId, ready])

  useEffect(() => {
    if (!preparing || !appId || !runId || review.runId !== runId) return
    const abort = new AbortController()
    function finish(nextStep: 'connectivity' | 'review') {
      focusStep.current = true
      setParams(previous => {
        const next = new URLSearchParams(previous)
        next.delete('prepare')
        next.set('step', nextStep)
        return next
      }, { replace: true })
    }
    async function prepare(appId: string, runId: string) {
      if (reviewPlan) { finish('review'); return }
      if (!canPrepare) throw new Error('Automatic preparation requires a complete, source-pinned assessment. Review the analysis before continuing.')
      const result = await deliveryRecommendation(appId, runId, abort.signal, () => setDatabaseSetupRun(runId), needsRailsProfile)
      if (!abort.signal.aborted) setDatabaseSetupRun(null)
      if (abort.signal.aborted) return
      if (result.generation_blocker) throw new Error(result.generation_blocker)
      const intent = reviewIntent ?? result.intent
      if (intent.assessment_version !== assessmentVersion) throw new Error('Delivery settings belong to an older assessment. Review them before continuing.')
      if (intent.build.branch !== analyzedRevision ||
        intent.connectivity.exposure.kind !== 'internal' || intent.connectivity.replicas !== 1 || intent.connectivity.connections?.length ||
        !intent.build.install_command.length || !intent.build.start_command.length ||
        ['connectivity.port', 'connectivity.health_path'].some(field => intent.field_recommendations[field]?.origin !== 'inferred') ||
        result.application_configuration.some(field => field.required && field.recommended_value === null)) {
        throw new Error('Review the detected commands, port, health path or required configuration before preparing a bundle.')
      }
      const saved = reviewIntent ?? await saveDeliveryIntent(appId, runId, intent, true)
      if (abort.signal.aborted) return
      const plan = await generateDeliveryBundle(appId, runId, saved, true)
      if (abort.signal.aborted) return
      setReview({ runId, intent: saved, plan })
      finish('review')
    }
    void prepare(appId, runId).catch(reason => {
      if (abort.signal.aborted) return
      setPreparationFailure({ runId, message: errorMessage(reason) })
      if (!(reason instanceof DatabaseSetupPending)) finish('connectivity')
    })
    return () => abort.abort()
  }, [analyzedRevision, appId, assessmentVersion, canPrepare, needsRailsProfile, preparing, review.runId, reviewIntent, reviewPlan, runId, setParams])

  useEffect(() => {
    if (!ready || review.runId !== runId || params.has('step')) return
    setParams(previous => {
      const next = new URLSearchParams(previous)
      next.set('step', steps[furthestStep].slug)
      return next
    }, { replace: true })
  }, [furthestStep, params, ready, review.runId, runId, setParams])

  return <>
    <header className="page-header"><div><p className="page-eyeline"><Link to="/services"><ArrowLeft size={13} /> Applications</Link></p><h1>{currentApp?.name || (appId ? 'Application setup' : 'Add application')}</h1></div></header>
    <nav aria-label="Setup steps"><ol className="stepper">{steps.map(({ label, slug }, index) => {
      const current = index === step
      const state = current ? 'active' : completed[index] ? 'complete' : ''
      return <li key={slug} className={state}><button type="button" onClick={() => openStep(index)} disabled={preparing || index > furthestStep} aria-current={current ? 'step' : undefined} aria-description={completed[index] && !current ? 'Complete' : undefined}>
        <span aria-hidden="true">{completed[index] && !current ? <Check size={14} /> : index + 1}</span><b>{label}</b>
      </button>{index < steps.length - 1 && <i aria-hidden="true" />}</li>
    })}</ol></nav>
    <ErrorNotice message={error || runError || (!matchesApplication ? 'This analysis belongs to a different application.' : '')} />
    {(error || runError) && <button className="button button-secondary" onClick={() => { setLoadAttempt(n => n + 1); reload() }}>Retry loading</button>}
    {preparationFailure?.runId === runId && <ErrorNotice message={preparationFailure.message} />}
    {ready && run && currentApp && (step === 1 || step === 2)
      ? <PlanWorkflow key={runId} application={currentApp} run={run} canWrite={canWrite} stage={step === 1 ? 'connectivity' : 'workflow'}
          plan={reviewPlan} onContinue={() => openStep(step + 1)} onIntentChange={setReviewIntent} onPlanChange={setReviewPlan} />
      : <section className="wizard-card"><div className="wizard-step">
        {step === 0 ? <>
          <div className="step-intro"><h2 id="wizard-step-title" tabIndex={-1}>Source</h2><p>DeployFlow reviews a pinned GitHub revision without running its code.</p></div>
          {loading && <p role="status">Loading application…</p>}
          <form onSubmit={analyze}>
            <fieldset disabled={Boolean(appId) || busy || !canWrite} className="source-fields">
              <div className="form-grid">
                <Field label="GitHub repository URL"><input type="url" required maxLength={512} pattern="https://github.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+/?" placeholder="https://github.com/owner/repository" value={source.repository_url} onChange={e => setDraft({ ...draft, repository_url: e.target.value })} /></Field>
                <Field label="Repository revision"><input required maxLength={255} aria-describedby={appId ? undefined : 'source-revision-hint'} value={source.revision} onChange={e => setDraft({ ...draft, revision: e.target.value })} /></Field>
              </div>
              {!appId && <div className="source-hints"><small id="source-revision-hint">Use a branch, tag or commit SHA.</small></div>}
            </fieldset>
            {!runId && <>
              <button className="button button-primary" type="submit" disabled={!canAnalyze}>{busy ? 'Starting analysis…' : 'Prepare delivery'}</button>
              <details className="workflow-details"><summary>Advanced setup</summary>
                {!appId && <Field label="Application name (optional)"><input pattern="[a-z]([a-z0-9\-]*[a-z0-9])?" maxLength={63} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} disabled={busy || !canWrite} /></Field>}
                <button className="button button-secondary" type="submit" value="analysis-only" disabled={!canAnalyze}>Analyze only</button>
              </details>
            </>}
          </form>
          {preparing && preparationFailure?.runId !== runId && <p role="status">{databaseSetupRun === runId ? 'Preparing the test database…' : 'Preparing the infrastructure bundle…'}</p>}
          {runId && matchesApplication && <section className="analysis-banner" aria-label="Analysis summary">
            <div><div><b role="status">{run ? analysisLabels[run.status] : 'Loading analysis…'}</b>
              {active && message && <p>{message}</p>}
              {run && <p>{mode && Object.hasOwn(analysisModes, mode) ? analysisModes[mode] : 'Mode unavailable'}{run.commit_sha && <> · Commit <code title={run.commit_sha}>{run.commit_sha.slice(0, 12)}</code></>}</p>}
            </div></div>
            {active && <div className="analysis-progress"><progress max={100} value={run?.progress ?? 0} aria-label="Analysis progress" /><span>{run?.progress ?? 0}%</span></div>}
          </section>}
          {run?.error_code && <p role="alert">{run.error_message || 'Analysis stopped. Review repository access or start a new analysis.'}</p>}
          {run && matchesApplication && <>
            <div className="inline-actions">{active
              ? <button className="button button-secondary" disabled={busy || !canWrite} onClick={() => void perform(async () => { await command(`/analyses/${run.id}/cancel`); reload() })}>Cancel analysis</button>
              : <button className="button button-secondary" disabled={busy || preparing || !canWrite} onClick={() => void perform(async () => {
                const queued = await command<QueuedAnalysis>(`/analyses/${run.id}/reanalyze`, {})
                setParams({ application: run.application_id, run: queued.run_id, step: 'source' }, { replace: true })
              })}>Reanalyze repository</button>}</div>
            <details className="workflow-details"><summary>Analysis details</summary><p>{connection} · {run.attempts} {run.attempts === 1 ? 'attempt' : 'attempts'}</p><p>Run <code>{run.id}</code></p>
              {run.commit_sha && <p>Source commit <code>{run.commit_sha}</code></p>}{run.error_code && <p>Error code <code>{run.error_code}</code></p>}
            </details>
          </>}
        </> : run && reviewPlan ? <>
          <div className="step-intro"><h2 id="wizard-step-title" tabIndex={-1}>Review</h2></div>
          <div className="review-grid">
            <section className="review-section"><h3>Source</h3><dl><dt>Revision</dt><dd>{run.revision}</dd><dt>Commit</dt><dd><code>{run.commit_sha ?? 'Unavailable'}</code></dd></dl></section>
            <section className="review-section"><h3>Delivery settings</h3><dl><dt>Port</dt><dd>{reviewIntent?.connectivity.port ?? 'Not saved'}</dd><dt>Health path</dt><dd>{reviewIntent?.connectivity.health_path ?? 'Not saved'}</dd><dt>Exposure</dt><dd>{reviewIntent?.connectivity.exposure.kind ?? 'Not saved'}</dd></dl></section>
          </div>
          <section className="plan-preview">
            <h3>{reviewPlan.status === 'stale' ? 'Bundle needs regeneration' : reviewPlan.status === 'invalid' ? 'Bundle invalid' : 'Infrastructure bundle'}</h3>
            <ClusterValidation plan={reviewPlan} onPlanChange={setReviewPlan} canWrite={canWrite} />
            <details className="workflow-details"><summary>Bundle details</summary><p>Digest <code>{reviewPlan.bundle_digest}</code></p><p>Preview image <code>{reviewPlan.validation.image_binding.preview_reference}</code></p><p>Assessment version {run.assessment_version}</p></details>
            <details className="workflow-details"><summary>Inspect generated files</summary>
              <PlanFileReview key={reviewPlan.id} plan={reviewPlan} />
            </details>
            <PullRequestReview plan={reviewPlan} onPlanChange={setReviewPlan} canWrite={canWrite} canDeploy={canDeploy} />
          </section>
        </> : <p role="status">Loading assessment…</p>}
      </div></section>}
    <div className="wizard-actions page-actions">
      {step > 0 && <button type="button" className="button button-ghost" onClick={() => openStep(step - 1)}><ArrowLeft size={16} />Back to {steps[step - 1].label}</button>}
      {step === 0 && ready && !preparing && <button type="button" className="button button-primary" disabled={busy} onClick={() => openStep(1)}>Continue to connectivity</button>}
    </div>
  </>
}
