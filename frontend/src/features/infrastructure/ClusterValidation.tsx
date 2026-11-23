import { useEffect, useRef, useState } from 'react'
import { command, errorMessage } from '../../shared/api'
import type { InfrastructurePlan } from './contracts'

export function ClusterValidation({ plan, onPlanChange, canWrite }: {
  plan: InfrastructurePlan; onPlanChange: (plan: InfrastructurePlan) => void; canWrite: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const checkedAutomatically = useRef<string | null>(null)

  async function validate() {
    setBusy(true); setError('')
    try {
      const result = await command<NonNullable<InfrastructurePlan['cluster_validation']>>(
        `/infrastructure-plans/${plan.id}/validations`, { expected_digest: plan.bundle_digest },
      )
      onPlanChange({ ...plan, cluster_validation: result })
    } catch (reason) { setError(errorMessage(reason)) } finally { setBusy(false) }
  }

  useEffect(() => {
    if (!canWrite || plan.cluster_validation || plan.status === 'stale' || plan.status === 'invalid' || checkedAutomatically.current === plan.id) return
    checkedAutomatically.current = plan.id
    void validate()
    // Run once for each newly generated plan; a saved result prevents repeated calls on reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan.id, canWrite])

  const result = plan.cluster_validation
  const label = result?.status === 'passed' ? 'Dry-run passed'
    : result?.status === 'failed' ? 'Dry-run rejected'
    : result?.status === 'unavailable' ? 'Cluster check unavailable' : null
  return <section className="cluster-validation" aria-label="Kubernetes admission validation">
    <div className="cluster-validation-header">
      <div><b>Kubernetes dry-run</b><small>Checks the bundle without creating resources.</small></div>
      {canWrite && <button className="button button-secondary" type="button" disabled={busy || plan.status === 'stale' || plan.status === 'invalid'} onClick={() => void validate()}>{busy ? 'Checking…' : result?.status === 'passed' ? 'Run dry-run again' : 'Retry validation'}</button>}
    </div>
    {error ? <p role="alert">{error}</p> : <p role="status">{busy ? 'Checking Kubernetes admission…' : label ?? 'Dry-run pending'}</p>}
    {result && <details className="workflow-details" open={result.status === 'failed' || undefined}>
      <summary>Admission details</summary>
      {result.observed_at && <p>Checked {new Date(result.observed_at).toLocaleString()}</p>}
      <ul>{result.checks.map((check, index) => <li key={`${check.kind || ''}-${check.name || ''}-${index}`}>{check.kind ? `${check.kind}/${check.name} · ` : ''}{check.code}{check.details?.map(detail => ` · ${detail.field}: ${detail.reason}`).join('')}</li>)}</ul>
    </details>}
  </section>
}
