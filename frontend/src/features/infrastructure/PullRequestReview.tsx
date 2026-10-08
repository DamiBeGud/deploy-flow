import { useEffect, useState } from 'react'
import { command, errorMessage, request } from '../../shared/api'
import type { InfrastructurePlan, InfrastructurePullRequest } from './contracts'

export function PullRequestReview({ plan, canWrite, onPlanChange }: {
  plan: InfrastructurePlan
  canWrite: boolean
  onPlanChange: (plan: InfrastructurePlan) => void
}) {
  const [polledPullRequest, setPullRequest] = useState<InfrastructurePullRequest | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pullRequest = polledPullRequest?.plan_id === plan.id ? polledPullRequest : plan.pull_request ?? null
  const pullRequestId = pullRequest?.id ?? null
  const pullRequestStatus = pullRequest?.status ?? null

  useEffect(() => {
    if (!pullRequestId || (pullRequestStatus && ['verified', 'closed', 'blocked'].includes(pullRequestStatus))) return
    const abort = new AbortController()
    const poll = () => {
      request<InfrastructurePullRequest>(`/pull-requests/${pullRequestId}`, { signal: abort.signal })
        .then(setPullRequest)
        .catch(reason => { if (!abort.signal.aborted) setError(errorMessage(reason)) })
    }
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

  return <section className="cluster-validation" aria-label="Infrastructure pull request">
    <div className="cluster-validation-header"><div><b>GitHub pull request</b><small>Review and merge on GitHub. Creating a PR does not deploy anything.</small></div></div>
    {error && <p role="alert">{error}</p>}
    {pullRequest ? <>
      <p role="status">Status: {pullRequest.status}{pullRequest.error_code ? ` · ${pullRequest.error_code}` : ''}</p>
      {pullRequest.url && <p><a className="button button-secondary" href={pullRequest.url} target="_blank" rel="noreferrer">Open PR #{pullRequest.number} on GitHub</a></p>}
      {pullRequest.verified_build && <p>Verified image: <code>{pullRequest.verified_build.image_repository}@{pullRequest.verified_build.image_digest}</code></p>}
      {canWrite && pullRequest.number && <button className="button button-secondary" disabled={busy} type="button" onClick={() => void refresh()}>{busy ? 'Checking…' : 'Refresh PR and CI status'}</button>}
      {canWrite && pullRequest.status === 'failed' && !pullRequest.number && <button className="button button-secondary" disabled={busy} type="button" onClick={() => void retry()}>{busy ? 'Retrying…' : 'Retry PR creation'}</button>}
    </> : <>
      <p>Approval will queue one feature-branch PR for this exact bundle. It will not merge or deploy.</p>
      <button className="button button-primary" disabled={!canWrite || busy || plan.cluster_validation?.status !== 'passed' || plan.status === 'stale' || plan.status === 'invalid'} type="button" onClick={() => void create()}>{busy ? 'Submitting…' : 'Approve and create infrastructure PR'}</button>
      {plan.cluster_validation?.status !== 'passed' && <p className="section-note">A passing cluster admission check is required first.</p>}
    </>}
  </section>
}
