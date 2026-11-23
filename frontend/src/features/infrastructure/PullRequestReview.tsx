import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { statusLabel } from '../../shared/deploymentStatus'
import { ReleaseChecklist } from '../../shared/ReleaseChecklist'
import type { ReleaseCheck } from '../../shared/deploymentStatus'
import { command, errorMessage, request } from '../../shared/api'
import type { InfrastructurePlan, InfrastructurePullRequest } from './contracts'
import { DeploymentProgress } from './DeploymentProgress'

export function PullRequestReview({ plan, canWrite, canDeploy, onPlanChange }: {
  plan: InfrastructurePlan
  canWrite: boolean
  canDeploy: boolean
  onPlanChange: (plan: InfrastructurePlan) => void
}) {
  const [polledPullRequest, setPullRequest] = useState<InfrastructurePullRequest | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pullRequest = polledPullRequest?.plan_id === plan.id ? polledPullRequest : plan.pull_request ?? null
  const pullRequestId = pullRequest?.id ?? null
  const pullRequestStatus = pullRequest?.status ?? null

  useEffect(() => {
    if (!pullRequestId || (pullRequestStatus && ['closed', 'blocked'].includes(pullRequestStatus))) return
    const abort = new AbortController()
    const poll = () => {
      request<InfrastructurePullRequest>(`/pull-requests/${pullRequestId}`, { signal: abort.signal })
        .then(setPullRequest)
        .catch(reason => { if (!abort.signal.aborted) setError(errorMessage(reason)) })
    }
    poll()
    const timer = window.setInterval(poll, 10000)
    return () => { abort.abort(); window.clearInterval(timer) }
  }, [pullRequestId, pullRequestStatus])

  async function create() {
    setBusy(true); setError('')
    try {
      const created = await command<InfrastructurePullRequest>(`/infrastructure-plans/${plan.id}/pull-requests`, { expected_digest: plan.bundle_digest })
      setPullRequest(created)
      onPlanChange(await request<InfrastructurePlan>(`/infrastructure-plans/${plan.id}`))
    } catch (reason) { setError(errorMessage(reason)) } finally { setBusy(false) }
  }

  async function refresh() {
    if (!pullRequest) return
    setBusy(true); setError('')
    try {
      await command(`/pull-requests/${pullRequest.id}/refresh`)
      setPullRequest(await request<InfrastructurePullRequest>(`/pull-requests/${pullRequest.id}`))
    } catch (reason) { setError(errorMessage(reason)) } finally { setBusy(false) }
  }

  async function retry() {
    if (!pullRequest) return
    setBusy(true); setError('')
    try {
      setPullRequest(await command<InfrastructurePullRequest>(`/pull-requests/${pullRequest.id}/retry`))
    } catch (reason) { setError(errorMessage(reason)) } finally { setBusy(false) }
  }

  const checks: ReleaseCheck[] = pullRequest ? [
    { label: 'Pull request', result: pullRequest.status === 'queued' ? 'Creating' : pullRequest.number ? `#${pullRequest.number}` : statusLabel(pullRequest.status), tone: pullRequest.number ? 'complete' : pullRequest.status === 'failed' ? 'attention' : undefined },
    { label: 'Merge', result: pullRequest.merged_sha ? 'Merged' : pullRequest.status === 'closed' ? 'Closed' : 'Waiting for review', tone: pullRequest.merged_sha ? 'complete' : pullRequest.status === 'closed' ? 'attention' : undefined },
    { label: 'Image verification', result: pullRequest.status === 'verified' ? 'Verified' : pullRequest.status === 'ci_failed' ? 'CI failed' : pullRequest.status === 'blocked' ? 'Blocked' : pullRequest.merged_sha ? 'Waiting for CI' : 'After merge', tone: pullRequest.status === 'verified' ? 'complete' : ['ci_failed', 'blocked'].includes(pullRequest.status) ? 'attention' : undefined },
  ] : []
  const canApprove = canWrite && !busy && plan.cluster_validation?.status === 'passed' && ['proposed', 'approved'].includes(plan.status)
  const approvalLink = pullRequest?.head_sha && /^[a-f0-9]{40}$/.test(pullRequest.head_sha)
    ? `/applications/${plan.application_id}?reviewedPr=${pullRequest.head_sha}#deployment-controls`
    : `/applications/${plan.application_id}#deployment-controls`

  return <section className="cluster-validation" aria-label="Infrastructure pull request">
    <div className="cluster-validation-header"><b>GitHub pull request</b></div>
    {error && <p role="alert">{error}</p>}
    {pullRequest ? <>
      <ReleaseChecklist label="Pull request progress" checks={checks} />
      {pullRequest.status === 'open' && <p>Review the changes and wait for CI before merging.{canDeploy && ' To deploy on merge, review deployment authorization first.'}</p>}
      {pullRequest.status === 'open' && canDeploy && <Link className="button button-secondary" to={approvalLink}>Review deployment authorization</Link>}
      {pullRequest.url && <a className="button button-secondary" href={pullRequest.url} target="_blank" rel="noreferrer">Open PR #{pullRequest.number} on GitHub</a>}
      {canWrite && pullRequest.number && <button className="button button-ghost" disabled={busy} type="button" onClick={() => void refresh()}>{busy ? 'Checking…' : 'Refresh PR and CI status'}</button>}
      {canWrite && pullRequest.status === 'failed' && !pullRequest.number && <button className="button button-secondary" disabled={busy} type="button" onClick={() => void retry()}>{busy ? 'Retrying…' : 'Retry PR creation'}</button>}
      {(pullRequest.verified_build || pullRequest.error_code) && <details className="workflow-details"><summary>Build details</summary>
        {pullRequest.verified_build && <p>Verified image <code>{pullRequest.verified_build.image_repository}@{pullRequest.verified_build.image_digest}</code></p>}
        {pullRequest.error_code && <p>Error code <code>{pullRequest.error_code}</code></p>}
      </details>}
      {pullRequest.status === 'verified' && <DeploymentProgress key={pullRequest.id} applicationId={plan.application_id} pullRequest={pullRequest} />}
    </> : <>
      <p>{plan.status === 'stale' || plan.status === 'invalid' ? 'Return to Bundle and generate a new bundle.' : plan.cluster_validation?.status !== 'passed' ? 'Kubernetes admission must pass before you can create a PR.' : 'Approve this bundle to open a PR for review on GitHub.'}</p>
      <button className="button button-primary" disabled={!canApprove} type="button" onClick={() => void create()}>{busy ? 'Creating pull request…' : 'Approve and create infrastructure PR'}</button>
    </>}
  </section>
}
