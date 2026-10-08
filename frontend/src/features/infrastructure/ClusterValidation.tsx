import { useEffect, useState } from 'react'
import { command, errorMessage } from '../../shared/api'
import type { InfrastructurePlan } from './contracts'

export function ClusterValidation({ plan, onPlanChange, canWrite }: {
  plan: InfrastructurePlan; onPlanChange: (plan: InfrastructurePlan) => void; canWrite: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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
    if (!canWrite || plan.cluster_validation || plan.status === 'stale' || plan.status === 'invalid') return
    void validate()
    // Run once for each newly generated plan; a saved result prevents repeated calls on reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan.id, canWrite])

  const result = plan.cluster_validation
  return <section className="cluster-validation" aria-label="Kubernetes admission validation">
    <div className="cluster-validation-header"><div><b>Cluster admission check</b><small>Server-side dry-run only · no resources are saved</small></div><button className="button button-secondary" type="button" disabled={!canWrite || busy || plan.status === 'stale' || plan.status === 'invalid'} onClick={() => void validate()}>{busy ? 'Validating…' : 'Retry validation'}</button></div>
    {error ? <p role="alert">{error}</p> : busy ? <p role="status">Checking Kubernetes admission…</p> : result ? <><p role="status">Result: {result.status}{result.observed_at ? ` · ${new Date(result.observed_at).toLocaleString()}` : ''}</p><ul>{result.checks.map((check, index) => <li key={`${check.kind || ''}-${check.name || ''}-${index}`}>{check.kind ? `${check.kind}/${check.name}: ` : ''}{check.code}{check.details?.map(detail => ` · ${detail.field}: ${detail.reason}`).join('')}</li>)}</ul></> : <p>Not validated against the cluster yet.</p>}
  </section>
}
