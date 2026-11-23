import { useEffect, useId, useRef, useState } from 'react'
import { FileCode2, ShieldCheck } from 'lucide-react'
import type { Analysis, Application } from '../../shared/contracts'
import { errorMessage, request } from '../../shared/api'
import { ErrorNotice, Field } from '../../shared/ui'
import type { DeploymentIntent, InfrastructurePlan } from './contracts'
import { PlanFileReview } from './PlanFileReview'
import { ClusterValidation } from './ClusterValidation'
import { RecommendationHint } from './RecommendationHint'
import { generateDeliveryBundle, saveDeliveryIntent } from './deliveryPreparation'
import type { Recommendation } from './deliveryPreparation'

type Stage = 'connectivity' | 'workflow'
type Profile = { id: 'local-k3s'; namespace: string; hostname_suffix: string }
type CommandField = 'install_command' | 'test_command' | 'build_command' | 'start_command'

function commandText(argv: string[] | null) {
  return argv?.some(value => /[\s'"\\]|^\[/.test(value)) ? JSON.stringify(argv) : argv?.join(' ') ?? ''
}

function parseCommand(input: string): string[] | null {
  const value = input.trim()
  if (!value) return []
  if (value.startsWith('[')) {
    try {
      const parsed: unknown = JSON.parse(value)
      return Array.isArray(parsed) && parsed.every(item => typeof item === 'string' && item.length > 0)
        ? parsed
        : null
    } catch {
      return null
    }
  }
  if (/['"\\]/.test(value)) return null
  return value.split(/\s+/)
}

export function PlanWorkflow({ application, run, canWrite, stage, plan, onContinue, onIntentChange, onPlanChange }: {
  application: Application; run: Analysis; canWrite: boolean; stage: Stage; onContinue: () => void
  plan: InfrastructurePlan | null; onIntentChange: (intent: DeploymentIntent) => void; onPlanChange: (plan: InfrastructurePlan) => void
}) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [intent, setIntent] = useState<DeploymentIntent | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [database, setDatabase] = useState<Recommendation['approved_database']>(null)
  const [queue, setQueue] = useState<Recommendation['approved_queue']>(null)
  const [apiToken, setApiToken] = useState(false)
  const [generationBlocker, setGenerationBlocker] = useState<string | null>(null)
  const [commandErrors, setCommandErrors] = useState<Partial<Record<CommandField, boolean>>>({})
  const [applicationConfiguration, setApplicationConfiguration] = useState<Recommendation['application_configuration']>([])
  const [editingConfiguration, setEditingConfiguration] = useState<Set<string>>(() => new Set())
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([request<Profile[]>('/environment-profiles'), request<DeploymentIntent | null>(`/applications/${application.id}/deployment-intent?analysis_run_id=${run.id}`), request<Recommendation>(`/applications/${application.id}/kubernetes-recommendation?analysis_run_id=${run.id}`)]).then(([profiles, saved, recommendation]) => {
      if (cancelled) return
      const selected = profiles[0]
      if (!selected) throw new Error('The local-k3s profile is not configured. Set GHCR_REGISTRY and GHCR_IMAGE.')
      const nextIntent = saved ? {
        ...recommendation.intent,
        ...saved,
        connectivity: {
          ...recommendation.intent.connectivity,
          ...saved.connectivity,
          environment: saved.field_recommendations ? saved.connectivity.environment : { ...recommendation.intent.connectivity.environment, ...saved.connectivity.environment },
          resources: { ...recommendation.intent.connectivity.resources, ...saved.connectivity.resources },
          exposure: { ...recommendation.intent.connectivity.exposure, ...saved.connectivity.exposure },
        },
        build: { ...recommendation.intent.build, ...saved.build },
        field_recommendations: saved.field_recommendations || recommendation.intent.field_recommendations,
      } : recommendation.intent
      setProfile(selected); setIntent(nextIntent); setGenerationBlocker(recommendation.generation_blocker ?? null); setDatabase(recommendation.approved_database ?? null); setQueue(recommendation.approved_queue ?? null); setApiToken(Boolean(recommendation.approved_api_token)); setApplicationConfiguration(recommendation.application_configuration)
    }).catch(exception => { if (!cancelled) setError(errorMessage(exception)) })
    return () => { cancelled = true }
  }, [application.id, run.id])

  function setCommandError(field: CommandField, invalid: boolean) {
    setCommandErrors(current => ({ ...current, [field]: invalid }))
  }

  function updateCommand(field: CommandField, argv: string[]) {
    updateIntent(current => ({
      ...current,
      build: {
        ...current.build,
        [field]: argv.length || field === 'install_command' || field === 'start_command' ? argv : null,
      },
    }))
  }

  function updateIntent(update: (current: DeploymentIntent) => DeploymentIntent) {
    if (!intent) return
    const next = update(intent)
    setIntent(next)
    onIntentChange(next)
    if (plan && plan.status !== 'stale') onPlanChange({ ...plan, status: 'stale', approval: null })
    setNotice('Unsaved changes.')
  }

  async function save(): Promise<DeploymentIntent> {
    if (!intent) throw new Error('Delivery settings are still loading.')
    setBusy(true); setError(''); setNotice('')
    try {
      const saved = await saveDeliveryIntent(application.id, run.id, intent)
      setIntent(saved); onIntentChange(saved); setNotice('Delivery settings saved.')
      return saved
    } catch (exception) { const message = errorMessage(exception); setError(message); throw exception } finally { setBusy(false) }
  }

  async function saveAndContinue() { try { await save(); onContinue() } catch { /* The visible error explains the failed save. */ } }
  async function generate() {
    setError('')
    try {
      const saved = await save()
      setBusy(true)
      const generated = await generateDeliveryBundle(application.id, run.id, saved)
      onPlanChange(generated); setNotice('Bundle generated.')
    } catch (exception) { setError(errorMessage(exception)) } finally { setBusy(false) }
  }
  const title = stage === 'connectivity' ? 'Connectivity' : 'Bundle'
  if (!intent || !profile) return <section className="wizard-card"><div className="wizard-step">
    <div className="step-intro"><h2 id="wizard-step-title" tabIndex={-1}>{title}</h2></div>
    {error ? <ErrorNotice message={error} /> : <p role="status">Loading delivery settings…</p>}
  </div></section>
  const controlsDisabled = !canWrite || busy
  const commandInputInvalid = Object.values(commandErrors).some(Boolean)
  const bundleBlocker = generationBlocker || (!intent.build.install_command.length || !intent.build.start_command.length ? 'Review and fill the missing install or start command before generating a bundle.' : null)

  const blockedBindings = generationBlocker && /approved (?:database|configuration)/i.test(generationBlocker)
  const assessment = run.assessment?.mode !== 'fake' ? run.assessment : undefined
  const requiredSettings = assessment?.configuration.required_variables ?? []
  const canReview = Boolean(plan && ['proposed', 'approved'].includes(plan.status))
  return <section className="wizard-card"><div className="wizard-step infrastructure-step">
    <div className="step-intro"><h2 id="wizard-step-title" tabIndex={-1}>{title}</h2></div>
    <ErrorNotice message={error} />{notice && <p className="integration-notice" role="status">{notice}</p>}
    <fieldset className="infrastructure-fields" disabled={controlsDisabled}>
      {stage === 'connectivity' ? <>
        <p className="section-note">Target namespace <code>{profile.namespace}</code></p>
        <div className="section-divider"><span>Service and exposure</span></div>
        <div className="form-grid three"><div className="recommended-field"><Field label="Service port"><input type="number" min="1" max="65535" value={intent.connectivity.port} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, port: Number(event.target.value) } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.port']} effectiveValue={intent.connectivity.port} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, port: Number(current.field_recommendations['connectivity.port'].recommended_value) } }))} /></div><div className="recommended-field"><Field label="Health path"><input required pattern="/[^\s]*" value={intent.connectivity.health_path} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, health_path: event.target.value } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.health_path']} effectiveValue={intent.connectivity.health_path} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, health_path: String(current.field_recommendations['connectivity.health_path'].recommended_value) } }))} /></div><div className="recommended-field"><Field label="Replicas"><input type="number" min="1" max="3" value={intent.connectivity.replicas} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, replicas: Number(event.target.value) } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.replicas']} effectiveValue={intent.connectivity.replicas} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, replicas: Number(current.field_recommendations['connectivity.replicas'].recommended_value) } }))} /></div></div>
        <div className="recommended-field"><Field label="Exposure"><select value={intent.connectivity.exposure.kind} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, kind: event.target.value as 'internal' | 'external' } } }))}><option value="internal">Internal</option><option value="external">External</option></select></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.exposure.kind']} effectiveValue={intent.connectivity.exposure.kind} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, kind: current.field_recommendations['connectivity.exposure.kind'].recommended_value as 'internal' | 'external' } } }))} /></div>
        {intent.connectivity.exposure.kind === 'external' && <div className="form-grid"><div className="recommended-field"><Field label="Hostname"><input required placeholder={`service${profile.hostname_suffix}`} value={intent.connectivity.exposure.hostname || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, hostname: event.target.value } } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.exposure.hostname']} effectiveValue={intent.connectivity.exposure.hostname} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, hostname: String(current.field_recommendations['connectivity.exposure.hostname'].recommended_value) } } }))} /></div><div className="recommended-field"><Field label="Ingress path"><input required pattern="/[^\s]*" value={intent.connectivity.exposure.path} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, path: event.target.value } } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.exposure.path']} effectiveValue={intent.connectivity.exposure.path} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, path: String(current.field_recommendations['connectivity.exposure.path'].recommended_value) } } }))} /></div></div>}
        <div className="section-divider"><span>Resources</span></div><div className="form-grid"><Field label="CPU request"><input value={intent.connectivity.resources.requests.cpu || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: { ...current.connectivity.resources, requests: { ...current.connectivity.resources.requests, cpu: event.target.value } } } }))} /></Field><Field label="Memory request"><input value={intent.connectivity.resources.requests.memory || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: { ...current.connectivity.resources, requests: { ...current.connectivity.resources.requests, memory: event.target.value } } } }))} /></Field><Field label="CPU limit"><input value={intent.connectivity.resources.limits.cpu || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: { ...current.connectivity.resources, limits: { ...current.connectivity.resources.limits, cpu: event.target.value } } } }))} /></Field><Field label="Memory limit"><input value={intent.connectivity.resources.limits.memory || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: { ...current.connectivity.resources, limits: { ...current.connectivity.resources.limits, memory: event.target.value } } } }))} /></Field></div><RecommendationHint recommendation={intent.field_recommendations['connectivity.resources']} effectiveValue={intent.connectivity.resources} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: structuredClone(current.field_recommendations['connectivity.resources'].recommended_value) as DeploymentIntent['connectivity']['resources'] } }))} />
        <div className="section-divider"><span>Application configuration</span></div>{applicationConfiguration.length ? <><div className="configuration-cards">{applicationConfiguration.map(item => { const fieldPath = `connectivity.environment.${item.name}`; const fieldRecommendation = intent.field_recommendations[fieldPath]; const value = intent.connectivity.environment[item.name] ?? ''; const editing = editingConfiguration.has(item.name); return <article className="configuration-card" key={item.name}><div><b>{item.name}{item.required ? ' · required' : ' · optional'}</b><p>{item.purpose}</p><RecommendationHint recommendation={fieldRecommendation} effectiveValue={intent.connectivity.environment[item.name] ?? null} onReset={() => updateIntent(current => { const environment = { ...current.connectivity.environment }; const recommended = current.field_recommendations[fieldPath].recommended_value; if (recommended === null) delete environment[item.name]; else environment[item.name] = String(recommended); return { ...current, connectivity: { ...current.connectivity, environment } } })} /></div>{editing ? <div className="configuration-override"><Field label={`Override ${item.name}`}><input value={value} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, environment: { ...current.connectivity.environment, [item.name]: event.target.value } } }))} /></Field><button className="button button-ghost" type="button" onClick={() => setEditingConfiguration(previous => new Set([...previous].filter(name => name !== item.name)))}>Done</button></div> : <div className="configuration-value"><code>{value || 'No value inferred'}</code><button className="button button-secondary" type="button" onClick={() => setEditingConfiguration(previous => new Set([...previous, item.name]))}>Override</button></div>}</article> })}</div></> : <p className="section-note">No additional environment settings detected.</p>}
        {database
          ? <p className="section-note">{database.ephemeral
              ? 'DeployFlow provisions a private PostgreSQL sidecar on port 5432 with disposable storage.'
              : 'The approved PostgreSQL binding supplies DATABASE_URL.'} The application applies <code>{database.migration}</code> at startup.</p>
          : queue
            ? <p className="section-note">DeployFlow provisions private Redis on port 6379 with disposable storage and runs <code>{queue.worker}</code> in a worker container.</p>
            : blockedBindings && <p className="section-note">A required service binding is missing. Review the blocker in Bundle.</p>}
        {apiToken && <p className="section-note">An approved Secret supplies the application's API token.</p>}
      </> : <>
        <div className="section-divider"><span>Container and target</span></div><div className="form-grid"><RecommendedInput label="Base branch" fieldPath="build.branch" value={intent.build.branch} intent={intent} updateIntent={updateIntent} onChange={(current, value) => ({ ...current, build: { ...current.build, branch: value } })} /><RecommendedInput label="Application image" fieldPath="build.image" value={intent.build.image} intent={intent} updateIntent={updateIntent} onChange={(current, value) => ({ ...current, build: { ...current.build, image: value } })} /><CommandInput label="Install command" field="install_command" intent={intent} onChange={updateCommand} setError={setCommandError} /><CommandInput label="Test command (optional)" field="test_command" intent={intent} onChange={updateCommand} setError={setCommandError} /><CommandInput label="Build command (optional)" field="build_command" intent={intent} onChange={updateCommand} setError={setCommandError} /><CommandInput label="Start command" field="start_command" intent={intent} onChange={updateCommand} setError={setCommandError} /></div>
      </>}
    </fieldset>
    {stage === 'workflow' && bundleBlocker && <div className="form-error" role="status"><div>
      <b>{blockedBindings ? 'Bundle blocked by missing setup' : 'Bundle needs input'}</b>
      <p>{bundleBlocker}</p>
      {blockedBindings && requiredSettings.length > 0 && <p>Ask the deployment operator to approve bindings for <code>{requiredSettings.join(', ')}</code>.</p>}
    </div></div>}
    {stage === 'workflow' && plan && <section className="plan-preview">
      <h3><ShieldCheck size={18} /> {plan.status === 'approved' ? 'Bundle approved' : canReview ? 'Bundle generated' : 'Bundle needs regeneration'}</h3>
      <ClusterValidation plan={plan} onPlanChange={onPlanChange} canWrite={canWrite} />
      <details className="workflow-details"><summary>Bundle details</summary><p>Digest <code>{plan.bundle_digest}</code></p><p>Preview image <code>{plan.validation.image_binding.preview_reference}</code></p><p>Local checks {plan.validation.checks.join(', ')}</p></details>
      <PlanFileReview key={plan.id} plan={plan} />
    </section>}
    <div className="wizard-actions infrastructure-actions">
      <button className="button button-secondary" type="button" disabled={controlsDisabled || commandInputInvalid} onClick={() => void save()}>Save changes</button>
      {stage === 'connectivity'
        ? <button className="button button-primary" type="button" disabled={controlsDisabled || commandInputInvalid} onClick={() => void saveAndContinue()}>Continue to bundle</button>
        : canReview
          ? <button className="button button-primary" type="button" disabled={busy || commandInputInvalid} onClick={onContinue}>Continue to review</button>
          : <button className="button button-primary" type="button" disabled={controlsDisabled || commandInputInvalid || Boolean(bundleBlocker)} onClick={() => void generate()}><FileCode2 size={16} />{plan ? 'Regenerate bundle' : 'Generate bundle'}</button>}
    </div>
  </div></section>
}

function RecommendedInput({ label, fieldPath, value, intent, updateIntent, onChange }: {
  label: string
  fieldPath: string
  value: string
  intent: DeploymentIntent
  updateIntent: (update: (current: DeploymentIntent) => DeploymentIntent) => void
  onChange: (current: DeploymentIntent, value: string) => DeploymentIntent
}) {
  const recommendation = intent.field_recommendations[fieldPath]
  return <div className="recommended-field"><Field label={label}><input required value={value} onChange={event => updateIntent(current => onChange(current, event.target.value))} /></Field><RecommendationHint recommendation={recommendation} effectiveValue={value} onReset={() => updateIntent(current => onChange(current, String(current.field_recommendations[fieldPath].recommended_value ?? '')))} /></div>
}

function CommandInput({ label, field, intent, onChange, setError }: {
  label: string
  field: CommandField
  intent: DeploymentIntent
  onChange: (field: CommandField, argv: string[]) => void
  setError: (field: CommandField, invalid: boolean) => void
}) {
  const argv = intent.build[field]
  const value = commandText(argv)
  const [draft, setDraft] = useState(value)
  const emitted = useRef(value)
  const error = parseCommand(draft) === null
  const errorId = useId()
  const recommendation = intent.field_recommendations[`build.${field}`]

  useEffect(() => {
    if (emitted.current !== value) {
      emitted.current = value
      setDraft(value)
    }
  }, [value])

  function change(input: string) {
    setDraft(input)
    const parsed = parseCommand(input)
    setError(field, parsed === null)
    if (parsed) {
      emitted.current = commandText(parsed)
      onChange(field, parsed)
    }
  }

  return <div className="recommended-field">
    <Field label={label}><input aria-invalid={error || undefined} aria-describedby={error ? errorId : undefined} value={draft} onChange={event => change(event.target.value)} /></Field>
    {error && <p id={errorId} role="alert">Enter space-separated arguments, or use a JSON string array when an argument contains spaces or quotes.</p>}
    <RecommendationHint recommendation={!argv?.length && (field === 'test_command' || field === 'build_command') && !['conflict', 'unsupported'].includes(recommendation?.status ?? '') ? undefined : recommendation} effectiveValue={argv} onReset={() => {
      const recommended = recommendation?.recommended_value
      change(Array.isArray(recommended) ? commandText(recommended) : '')
    }} />
  </div>
}
