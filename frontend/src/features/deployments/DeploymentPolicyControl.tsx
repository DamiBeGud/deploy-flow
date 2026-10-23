import { useState } from 'react'

export type Policy = {
  allowed_source_sha?: string | null
  source_compatibility_policy?: 'node-source-compatibility-v1' | 'node-handler-compatibility-v2'
  enabled: boolean
  execution_available: boolean
  cleanup_available: boolean
  actor_id: string | null
  authorization_state?: string
}

export type PolicyScope = Pick<Policy, 'allowed_source_sha' | 'source_compatibility_policy'>

export function DeploymentPolicyControl({ policy, applicationName, candidateSource, busy, onSave }: {
  policy: Policy
  applicationName: string
  candidateSource?: string
  busy: boolean
  onSave: (enabled: boolean, scope?: PolicyScope) => Promise<void>
}) {
  const [reviewing, setReviewing] = useState(false)
  const [scope, setScope] = useState<'single' | 'future'>(policy.enabled && !policy.allowed_source_sha ? 'future' : 'single')
  const [source, setSource] = useState(policy.allowed_source_sha ?? candidateSource ?? '')
  const [compatibility, setCompatibility] = useState<NonNullable<Policy['source_compatibility_policy']>>(policy.source_compatibility_policy ?? 'node-source-compatibility-v1')
  const [confirmed, setConfirmed] = useState(false)

  if (!reviewing) return <>
    <button className="button button-secondary" type="button" disabled={busy} onClick={() => { setConfirmed(false); setReviewing(true) }}>
      {policy.enabled ? 'Review automatic deployment policy' : 'Enable deployment for this application'}
    </button>
    {policy.enabled && <button className="button button-secondary" type="button" disabled={busy} onClick={() => void onSave(false).catch(() => {})}>Disable future automatic deployments</button>}
  </>

  return <form aria-label="Review deployment authorization" onSubmit={event => {
    event.preventDefault()
    if (!confirmed || (scope === 'single' && !/^[a-f0-9]{40}$/.test(source))) return
    void onSave(true, { allowed_source_sha: scope === 'future' ? null : source, source_compatibility_policy: compatibility }).then(() => setReviewing(false)).catch(() => {})
  }}>
    <h3>Review deployment authorization</h3>
    <p>This authorizes <b>{applicationName}</b> in <b>deployflow-test</b>. It does not authorize another application or platform upgrades.</p>
    <label>Source scope <select aria-label="Source scope" value={scope} disabled={busy} onChange={event => { setScope(event.target.value as 'single' | 'future'); setConfirmed(false) }}>
      <option value="single">One exact source commit</option>
      <option value="future">Future compatible commits on the configured branch</option>
    </select></label>
    {scope === 'single' && <label>Approved source SHA <input value={source} required pattern="[a-f0-9]{40}" disabled={busy} onChange={event => { setSource(event.target.value); setConfirmed(false) }} /></label>}
    <label>Compatibility rules <select aria-label="Compatibility rules" value={compatibility} disabled={busy} onChange={event => { setCompatibility(event.target.value as NonNullable<Policy['source_compatibility_policy']>); setConfirmed(false) }}>
      <option value="node-source-compatibility-v1">Response data, documentation and tests</option>
      <option value="node-handler-compatibility-v2">Existing Node route handler changes</option>
    </select></label>
    <p>Infrastructure, dependencies, runtime, startup, ports, health and configuration changes still require reassessment. New routes and code outside existing handlers also require review.</p>
    <p>Verification and preflight already in progress may queue an eligible release after this change. Releases already waiting for an executor require explicit selection.</p>
    <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} /> I authorize this application and source scope.</label>
    <div className="lifecycle-confirm">
      <button type="button" className="button button-secondary" disabled={busy} onClick={() => setReviewing(false)}>Cancel policy review</button>
      <button type="submit" className="button button-primary" disabled={busy || !confirmed || (scope === 'single' && !/^[a-f0-9]{40}$/.test(source))}>Confirm deployment policy</button>
    </div>
  </form>
}
