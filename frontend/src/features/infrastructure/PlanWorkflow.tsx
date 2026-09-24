import { useEffect, useState } from 'react'
import { Check, FileCode2, ShieldCheck } from 'lucide-react'
import type { Analysis, Application } from '../../shared/contracts'
import { command, errorMessage, request } from '../../shared/api'
import { ErrorNotice, Field } from '../../shared/ui'
import type { DeploymentIntent, InfrastructurePlan } from './contracts'
import { PlanFileReview } from './PlanFileReview'
import { RecommendationHint } from './RecommendationHint'

type Stage = 'connectivity' | 'workflow'
type Profile = { id: 'local-k3s'; namespace: string; default_image: string; hostname_suffix: string }
const words = (value: string) => value.trim().split(/\s+/).filter(Boolean)
const optionalWords = (value: string) => {
  const result = words(value)
  return result.length ? result : null
}
const commandValue = (value: string[] | null) => value?.join(' ') || ''

type ConfigurationRecommendation = { name: string; required: boolean; recommended_value: string | null; purpose: string; source: string; confidence: number; evidence: { file_path: string; line_start: number | null } | null }
type Recommendation = { intent: DeploymentIntent; recommendations: Record<string, { source: string; confidence: number }>; application_configuration: ConfigurationRecommendation[] }

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

export function PlanWorkflow({ application, run, canWrite, stage, plan, onContinue, onIntentChange, onPlanChange }: {
  application: Application; run: Analysis; canWrite: boolean; stage: Stage; onContinue: () => void
  plan: InfrastructurePlan | null; onIntentChange: (intent: DeploymentIntent) => void; onPlanChange: (plan: InfrastructurePlan) => void
}) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [intent, setIntent] = useState<DeploymentIntent | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [recommendations, setRecommendations] = useState<Recommendation['recommendations']>({})
  const [applicationConfiguration, setApplicationConfiguration] = useState<ConfigurationRecommendation[]>([])
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
      setProfile(selected); setIntent(nextIntent); setRecommendations(recommendation.recommendations); setApplicationConfiguration(recommendation.application_configuration); onIntentChange(nextIntent)
    }).catch(exception => { if (!cancelled) setError(errorMessage(exception)) })
    return () => { cancelled = true }
  }, [application.id, run.id, onIntentChange])

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
      const document = writableIntent(intent)
      const saved = await command<DeploymentIntent>(`/applications/${application.id}/deployment-intent`, { ...document, expected_version: intent.version, analysis_run_id: run.id, expected_assessment_version: intent.assessment_version }, 'PUT')
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
      const generated = await command<InfrastructurePlan>(`/applications/${application.id}/infrastructure-plans`, { analysis_run_id: run.id, expected_assessment_version: saved.assessment_version, intent_version: saved.version, environment_profile_id: 'local-k3s' })
      onPlanChange(generated); setNotice('Review bundle generated. Continue to review it.')
    } catch (exception) { setError(errorMessage(exception)) } finally { setBusy(false) }
  }
  async function approve() {
    if (!plan) return
    setBusy(true); setError('')
    try { const approved = { ...plan, ...(await command<Partial<InfrastructurePlan>>(`/infrastructure-plans/${plan.id}/approve`, { expected_digest: plan.bundle_digest })) }; onPlanChange(approved); setNotice('Bundle approved. No external change has been made.') } catch (exception) { setError(errorMessage(exception)) } finally { setBusy(false) }
  }

  if (!intent || !profile) return <section className="wizard-card"><div className="wizard-step"><p role="status">Loading delivery settings…</p></div></section>
  const controlsDisabled = !canWrite || busy

  return <section className="wizard-card"><div className="wizard-step infrastructure-step">
    <div className="step-intro"><h2>{stage === 'connectivity' ? 'AI Kubernetes recommendation' : 'Kubernetes bundle review'}</h2><p>DeployFlow selected these Kubernetes settings from repository evidence and Kubernetes policy. You only need to edit a value when you want an override.</p></div>
    <ErrorNotice message={error} />{notice && <p className="integration-notice" role="status">{notice}</p>}
    <fieldset className="infrastructure-fields" disabled={controlsDisabled}>
      {stage === 'connectivity' ? <>
        <div className="recommendation-summary"><b>Kubernetes target: {profile.namespace}</b><span>Network: {recommendations.network?.source || 'AI recommendation'} · Resources: {recommendations.resources?.source || 'Kubernetes policy default'}</span></div>
        <div className="section-divider"><span>Service and exposure</span></div>
        <div className="form-grid three"><div className="recommended-field"><Field label="Service port"><input type="number" min="1" max="65535" value={intent.connectivity.port} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, port: Number(event.target.value) } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.port']} effectiveValue={intent.connectivity.port} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, port: Number(current.field_recommendations['connectivity.port'].recommended_value) } }))} /></div><div className="recommended-field"><Field label="Health path"><input required pattern="/[^\s]*" value={intent.connectivity.health_path} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, health_path: event.target.value } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.health_path']} effectiveValue={intent.connectivity.health_path} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, health_path: String(current.field_recommendations['connectivity.health_path'].recommended_value) } }))} /></div><div className="recommended-field"><Field label="Replicas"><input type="number" min="1" max="3" value={intent.connectivity.replicas} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, replicas: Number(event.target.value) } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.replicas']} effectiveValue={intent.connectivity.replicas} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, replicas: Number(current.field_recommendations['connectivity.replicas'].recommended_value) } }))} /></div></div>
        <div className="recommended-field"><Field label="Exposure"><select value={intent.connectivity.exposure.kind} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, kind: event.target.value as 'internal' | 'external' } } }))}><option value="internal">Internal</option><option value="external">External</option></select></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.exposure.kind']} effectiveValue={intent.connectivity.exposure.kind} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, kind: current.field_recommendations['connectivity.exposure.kind'].recommended_value as 'internal' | 'external' } } }))} /></div>
        {intent.connectivity.exposure.kind === 'external' && <div className="form-grid"><div className="recommended-field"><Field label="Hostname"><input required placeholder={`service${profile.hostname_suffix}`} value={intent.connectivity.exposure.hostname || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, hostname: event.target.value } } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.exposure.hostname']} effectiveValue={intent.connectivity.exposure.hostname} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, hostname: String(current.field_recommendations['connectivity.exposure.hostname'].recommended_value) } } }))} /></div><div className="recommended-field"><Field label="Ingress path"><input required pattern="/[^\s]*" value={intent.connectivity.exposure.path} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, path: event.target.value } } }))} /></Field><RecommendationHint recommendation={intent.field_recommendations['connectivity.exposure.path']} effectiveValue={intent.connectivity.exposure.path} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, exposure: { ...current.connectivity.exposure, path: String(current.field_recommendations['connectivity.exposure.path'].recommended_value) } } }))} /></div></div>}
        <div className="section-divider"><span>Resources</span></div><div className="form-grid"><Field label="CPU request"><input value={intent.connectivity.resources.requests.cpu || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: { ...current.connectivity.resources, requests: { ...current.connectivity.resources.requests, cpu: event.target.value } } } }))} /></Field><Field label="Memory request"><input value={intent.connectivity.resources.requests.memory || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: { ...current.connectivity.resources, requests: { ...current.connectivity.resources.requests, memory: event.target.value } } } }))} /></Field><Field label="CPU limit"><input value={intent.connectivity.resources.limits.cpu || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: { ...current.connectivity.resources, limits: { ...current.connectivity.resources.limits, cpu: event.target.value } } } }))} /></Field><Field label="Memory limit"><input value={intent.connectivity.resources.limits.memory || ''} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: { ...current.connectivity.resources, limits: { ...current.connectivity.resources.limits, memory: event.target.value } } } }))} /></Field></div><RecommendationHint recommendation={intent.field_recommendations['connectivity.resources']} effectiveValue={intent.connectivity.resources} onReset={() => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, resources: structuredClone(current.field_recommendations['connectivity.resources'].recommended_value) as DeploymentIntent['connectivity']['resources'] } }))} />
        <div className="section-divider"><span>Application configuration</span></div>{applicationConfiguration.length ? <><p className="section-note">Each value appears once. Service port includes the repository’s <code>PORT</code> setting, so it is not duplicated here.</p><div className="configuration-cards">{applicationConfiguration.map(item => { const fieldPath = `connectivity.environment.${item.name}`; const fieldRecommendation = intent.field_recommendations[fieldPath]; const value = intent.connectivity.environment[item.name] ?? ''; const editing = editingConfiguration.has(item.name); return <article className="configuration-card" key={item.name}><div><b>{item.name}{item.required ? ' · required' : ' · optional'}</b><p>{item.purpose}</p><RecommendationHint recommendation={fieldRecommendation} effectiveValue={intent.connectivity.environment[item.name] ?? null} onReset={() => updateIntent(current => { const environment = { ...current.connectivity.environment }; const recommended = current.field_recommendations[fieldPath].recommended_value; if (recommended === null) delete environment[item.name]; else environment[item.name] = String(recommended); return { ...current, connectivity: { ...current.connectivity, environment } } })} /></div>{editing ? <div className="configuration-override"><Field label={`Override ${item.name}`}><input value={value} onChange={event => updateIntent(current => ({ ...current, connectivity: { ...current.connectivity, environment: { ...current.connectivity.environment, [item.name]: event.target.value } } }))} /></Field><button className="button button-ghost" type="button" onClick={() => setEditingConfiguration(previous => new Set([...previous].filter(name => name !== item.name)))}>Done</button></div> : <div className="configuration-value"><code>{value || 'No value inferred'}</code><button className="button button-secondary" type="button" onClick={() => setEditingConfiguration(previous => new Set([...previous, item.name]))}>Override</button></div>}</article> })}</div></> : <p className="section-note">No additional safe application configuration was detected.</p>}
        <p className="section-note">No approved database, cache, queue, or service connections are configured for this local profile.</p>
      </> : <>
        <div className="section-divider"><span>Container and target</span></div><p className="section-note">Kubernetes namespace: <b>{profile.namespace}</b> · Recommended image: <b>{profile.default_image}</b> · Commands: {recommendations.commands?.source || 'AI recommendation'}</p><div className="form-grid"><RecommendedInput label="Base branch" fieldPath="build.branch" value={intent.build.branch} intent={intent} updateIntent={updateIntent} onChange={(current, value) => ({ ...current, build: { ...current.build, branch: value } })} /><RecommendedInput label="Application image" fieldPath="build.image" value={intent.build.image} intent={intent} updateIntent={updateIntent} onChange={(current, value) => ({ ...current, build: { ...current.build, image: value } })} /><RecommendedInput label="Install command" fieldPath="build.install_command" value={commandValue(intent.build.install_command)} intent={intent} updateIntent={updateIntent} onChange={(current, value) => ({ ...current, build: { ...current.build, install_command: words(value) } })} /><RecommendedInput label="Test command" fieldPath="build.test_command" value={commandValue(intent.build.test_command)} intent={intent} updateIntent={updateIntent} onChange={(current, value) => ({ ...current, build: { ...current.build, test_command: optionalWords(value) } })} /><RecommendedInput label="Build command" fieldPath="build.build_command" value={commandValue(intent.build.build_command)} intent={intent} updateIntent={updateIntent} onChange={(current, value) => ({ ...current, build: { ...current.build, build_command: optionalWords(value) } })} /><RecommendedInput label="Start command" fieldPath="build.start_command" value={commandValue(intent.build.start_command)} intent={intent} updateIntent={updateIntent} onChange={(current, value) => ({ ...current, build: { ...current.build, start_command: words(value) } })} /></div>
      </>}
    </fieldset>
    {stage === 'workflow' && plan && <section className="plan-preview"><h3><ShieldCheck size={18} /> Bundle {plan.status}</h3><p>Digest: <code>{plan.bundle_digest}</code></p><p>Image binding: {plan.validation.image_binding.preview_reference} · provisional until CI supplies a verified digest</p><p>Checks: {plan.validation.checks.join(', ')}</p><PlanFileReview key={plan.id} plan={plan} /><button className="button button-primary" disabled={busy || Boolean(plan.approval) || plan.status === 'stale' || plan.status === 'invalid'} type="button" onClick={() => void approve()}>{plan.approval ? <><Check size={16} />Approved</> : 'Approve bundle'}</button><p className="section-note">Approval records review only. GitHub pull-request creation remains unavailable.</p></section>}
    <div className="wizard-actions infrastructure-actions"><button className="button button-secondary" type="button" disabled={controlsDisabled} onClick={() => void save()}>{stage === 'connectivity' ? 'Save connectivity' : 'Save delivery settings'}</button>{stage === 'connectivity' ? <button className="button button-primary" type="button" disabled={controlsDisabled} onClick={() => void saveAndContinue()}>Save and continue</button> : <><button className="button button-primary" type="button" disabled={controlsDisabled} onClick={() => void generate()}><FileCode2 size={16} />Generate bundle</button><button className="button button-secondary" type="button" disabled={controlsDisabled} onClick={() => void saveAndContinue()}>Save and review</button></>}</div>
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
  const effectiveValue = fieldPath.endsWith('_command') ? optionalWords(value) : value
  return <div className="recommended-field"><Field label={label}><input required={fieldPath !== 'build.test_command' && fieldPath !== 'build.build_command'} value={value} onChange={event => updateIntent(current => onChange(current, event.target.value))} /></Field><RecommendationHint recommendation={recommendation} effectiveValue={effectiveValue} onReset={() => updateIntent(current => { const recommended = current.field_recommendations[fieldPath].recommended_value; const nextValue = Array.isArray(recommended) ? recommended.join(' ') : String(recommended ?? ''); return onChange(current, nextValue) })} /></div>
}
