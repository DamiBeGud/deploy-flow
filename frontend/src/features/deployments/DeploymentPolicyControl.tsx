import { useEffect, useRef, useState } from 'react'
import { errorMessage } from '../../shared/api'
import { ErrorNotice } from '../../shared/ui'

export type Policy = {
  allowed_source_sha?: string | null
  source_compatibility_policy?: 'node-source-compatibility-v1' | 'node-handler-compatibility-v2' | 'reviewed-infrastructure-pr-v1'
  enabled: boolean
  execution_available: boolean
  cleanup_available: boolean
  actor_id: string | null
  authorization_state?: string
}

export type PolicyScope = Pick<Policy, 'allowed_source_sha' | 'source_compatibility_policy'>

export function DeploymentPolicyControl({ policy, applicationName, candidateSource, reviewedPr, busy, onSave }: {
  policy: Policy
  applicationName: string
  candidateSource?: string
  reviewedPr?: string
  busy: boolean
  onSave: (enabled: boolean, scope?: PolicyScope) => Promise<void>
}) {
  const [reviewing, setReviewing] = useState(Boolean(reviewedPr))
  const [scope, setScope] = useState<'single' | 'future' | 'reviewed'>(
    reviewedPr || policy.source_compatibility_policy === 'reviewed-infrastructure-pr-v1'
      ? 'reviewed' : policy.enabled && !policy.allowed_source_sha ? 'future' : 'single',
  )
  const [source, setSource] = useState(reviewedPr ?? policy.allowed_source_sha ?? candidateSource ?? '')
  const [compatibility, setCompatibility] = useState<'node-source-compatibility-v1' | 'node-handler-compatibility-v2'>(
    policy.source_compatibility_policy === 'node-handler-compatibility-v2'
      ? 'node-handler-compatibility-v2' : 'node-source-compatibility-v1',
  )
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState('')
  const title = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    if (reviewing) title.current?.focus()
  }, [reviewing])

  if (!reviewing) return <>
    <ErrorNotice message={error} />
    <button className="button button-secondary" type="button" disabled={busy} onClick={() => { setConfirmed(false); setError(''); setReviewing(true) }}>
      {policy.enabled ? 'Review automatic deployment policy' : 'Enable deployment for this application'}
    </button>
    {policy.enabled && <button className="button button-secondary" type="button" disabled={busy} onClick={() => { setError(''); void onSave(false).catch(reason => setError(errorMessage(reason))) }}>Disable future automatic deployments</button>}
  </>

  const validScope = scope === 'future' || /^[a-f0-9]{40}$/.test(source)

  return <form aria-label="Review deployment authorization" onSubmit={event => {
    event.preventDefault()
    if (!confirmed || !validScope) return
    setError('')
    void onSave(true, {
      allowed_source_sha: scope === 'future' ? null : source,
      source_compatibility_policy: scope === 'reviewed' ? 'reviewed-infrastructure-pr-v1' : compatibility,
    }).then(() => setReviewing(false)).catch(reason => setError(errorMessage(reason)))
  }}>
    <h3 ref={title} tabIndex={-1}>Review deployment authorization</h3>
    <p>Application <b>{applicationName}</b> · Namespace <b>deployflow-test</b></p>
    <ErrorNotice message={error} />
    <label>Source scope <select aria-label="Source scope" value={scope} disabled={busy} onChange={event => { const next = event.target.value as typeof scope; if (next === 'reviewed' || scope === 'reviewed') setSource(''); setScope(next); setConfirmed(false) }}>
      <option value="single">One exact source commit</option>
      <option value="reviewed">One reviewed infrastructure PR after merge</option>
      <option value="future">Future compatible commits on the configured branch</option>
    </select></label>
    {scope !== 'future' && <label>{scope === 'reviewed' ? 'Approved PR head SHA' : 'Approved source SHA'} <input value={source} required pattern="[a-f0-9]{40}" disabled={busy} aria-invalid={source !== '' && !validScope || undefined} aria-describedby={source !== '' && !validScope ? 'scope-sha-hint' : undefined} onChange={event => { setSource(event.target.value); setConfirmed(false) }} /></label>}
    {source && !validScope && <p id="scope-sha-hint">Enter the 40-character lowercase commit SHA.</p>}
    {scope === 'reviewed' ? <p>Only the verified release from this reviewed PR can execute after merge.</p> : <label>Compatibility rules <select aria-label="Compatibility rules" value={compatibility} disabled={busy} onChange={event => { setCompatibility(event.target.value as typeof compatibility); setConfirmed(false) }}>
      <option value="node-source-compatibility-v1">Response data, documentation and tests</option>
      <option value="node-handler-compatibility-v2">Existing Node route handler changes</option>
    </select></label>}
    {scope !== 'reviewed' && <p>Infrastructure, dependency and runtime changes still require review. New routes also require review.</p>}
    <p>An eligible release can be queued immediately. A release already waiting to deploy must be selected separately.</p>
    <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} /> I authorize this application and source scope.</label>
    <div className="lifecycle-confirm">
      <button type="button" className="button button-secondary" disabled={busy} onClick={() => setReviewing(false)}>Cancel policy review</button>
      <button type="submit" className="button button-primary" disabled={busy || !confirmed || !validScope}>Confirm deployment policy</button>
    </div>
  </form>
}
