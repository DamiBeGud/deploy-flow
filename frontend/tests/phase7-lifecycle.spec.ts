import { expect, test } from '@playwright/test'

const application = { id: '11111111-1111-4111-8111-111111111111', name: 'fixture', repository_url: 'https://github.com/example/fixture', revision: 'main', lifecycle_version: 2, latest_analysis: null }

function run(id: string, status: string, previous: string | null, kind: 'standard' | 'rollback' = 'standard') {
  return {
    id, application_id: application.id, environment_profile_id: 'local-k3s',
    release_id: `release-${id}`, release_digest: 'a'.repeat(64),
    image_reference: `ghcr.io/example/fixture@sha256:${id[0].repeat(64)}`,
    kind, rollback_target_release_id: null, status, error_code: null, event_sequence: 1,
    previous_release_id: previous, created_at: '2026-10-14T10:00:00Z',
    preflight_validation: { status: 'passed' },
    rollout: { status: 'ready', desired_replicas: 1, ready_replicas: 1, available_replicas: 1 },
    health: { status: 'passed', service: 'fixture', path: '/healthz' },
    execution_job: { id: `job-${id}`, purpose: 'deploy', status: 'completed', error_code: null },
    resources: [],
  }
}

test('shows history and queues an explicit rollback after review', async ({ page }) => {
  const errors: string[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  const previous = run('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'succeeded', null)
  const current = run('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'succeeded', previous.release_id)
  let history = [current, previous]
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (path === '/api/v1/auth/config') return route.fulfill({ json: { mode: 'development' } })
    if (path === '/api/v1/me') return route.fulfill({ json: { actor_id: 'operator', organization_id: 'org', roles: ['deployer'], authentication: 'oidc' } })
    if (path === '/api/v1/applications') return route.fulfill({ json: [application] })
    if (path === `/api/v1/applications/${application.id}`) return route.fulfill({ json: application })
    if (path === `/api/v1/applications/${application.id}/deployments`) return route.fulfill({ json: { deployments: history } })
    if (path === `/api/v1/applications/${application.id}/deployment-policy`) return route.fulfill({ json: { enabled: true, execution_available: true, cleanup_available: true, actor_id: 'operator' } })
    if (path.endsWith('/observation')) return route.fulfill({ json: { status: 'ready', observed_at: new Date().toISOString(), desired_replicas: 1, ready_replicas: 1, available_replicas: 1 } })
    if (path.endsWith('/release-status')) return route.fulfill({ json: { status: 'verified' } })
    if (path.endsWith('/events')) return route.fulfill({ json: { events: [{ sequence: 1, type: 'deployment.succeeded', occurred_at: '2026-10-14T10:00:00Z' }], last_sequence: 1 } })
    if (path === `/api/v1/deployments/${current.id}/rollback` && route.request().method() === 'POST') {
      expect(route.request().headers()['idempotency-key']).toBeTruthy()
      history = [run('dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'checking', current.release_id, 'rollback'), ...history]
      return route.fulfill({ status: 202, json: { deployment_id: history[0].id, status: 'checking' } })
    }
    if (path === '/api/v1/services') return route.fulfill({ json: { cluster_id: 'local-k3s', namespace: 'deployflow-test', observed_at: '2026-10-14T10:00:00Z', workloads: [], services: [] } })
    return route.fulfill({ status: 404, json: { detail: `Unexpected mock route: ${path}` } })
  })
  await page.goto('/')
  await expect(page).toHaveTitle(/DeployFlow/)
  await page.getByRole('button', { name: 'Connect to local backend' }).click()
  await page.getByRole('link', { name: 'View' }).click()
  await expect(page).toHaveURL(new RegExp(`/applications/${application.id}$`))
  await expect(page.getByRole('heading', { name: 'Deployment history' })).toBeVisible()
  await expect(page.getByText('Vite Error')).toHaveCount(0)
  await expect(page.getByText('2 stored releases')).toBeVisible()
  await page.getByRole('button', { name: 'Review rollback' }).click()
  await expect(page.getByText(/Rollback creates a new release/)).toBeVisible()
  await page.screenshot({ path: '/tmp/deployflow-phase7-rollback-review.png', fullPage: false })
  await page.getByRole('button', { name: 'Confirm rollback' }).click()
  await expect(page.getByText('3 stored releases')).toBeVisible()
  await expect(page.getByText('Manual rollback').first()).toBeVisible()
  expect(errors).toEqual([])
  expect(consoleErrors).toEqual([])
})

test('requires review of exact first-deployment inventory before cleanup', async ({ page }) => {
  const first = { ...run('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'reconciling', null),
    error_code: 'resource_apply_conflict',
    execution_job: { id: 'job-first', purpose: 'deploy', status: 'blocked', error_code: 'resource_apply_conflict' },
    resources: [{ kind: 'ConfigMap', name: 'fixture-config', status: 'applied', uid: 'owned-uid', generation: 1, error_code: null }],
  }
  let posted: unknown = null
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/auth/config') return route.fulfill({ json: { mode: 'development' } })
    if (path === '/api/v1/me') return route.fulfill({ json: { actor_id: 'operator', organization_id: 'org', roles: ['deployer'], authentication: 'oidc' } })
    if (path === `/api/v1/applications/${application.id}`) return route.fulfill({ json: application })
    if (path === `/api/v1/applications/${application.id}/deployments`) return route.fulfill({ json: { deployments: [first] } })
    if (path === `/api/v1/applications/${application.id}/deployment-policy`) return route.fulfill({ json: { enabled: true, execution_available: true, cleanup_available: true, actor_id: 'operator' } })
    if (path.endsWith('/observation')) return route.fulfill({ json: { status: 'ready', observed_at: new Date().toISOString(), desired_replicas: 1, ready_replicas: 1, available_replicas: 1 } })
    if (path.endsWith('/release-status')) return route.fulfill({ json: { status: 'verified' } })
    if (path.endsWith('/events')) return route.fulfill({ json: { events: [], last_sequence: 0 } })
    if (path === `/api/v1/deployments/${first.id}/cleanup`) {
      posted = route.request().postDataJSON()
      return route.fulfill({ status: 202, json: { deployment_id: first.id, status: 'cleanup_pending' } })
    }
    return route.fulfill({ status: 404, json: { detail: `Unexpected mock route: ${path}` } })
  })
  await page.goto(`/applications/${application.id}`)
  await page.getByRole('button', { name: 'Connect to local backend' }).click()
  await expect(page.getByRole('heading', { name: 'Clean up a failed first deployment' })).toBeVisible()
  await page.getByRole('button', { name: 'Review cleanup' }).click()
  await expect(page.getByText(/UID owned-uid/)).toBeVisible()
  await page.getByRole('button', { name: 'Confirm inventory cleanup' }).click()
  expect(posted).toEqual({ expected_release_digest: first.release_digest, resources: [{ kind: 'ConfigMap', name: 'fixture-config', uid: 'owned-uid' }] })
})

for (const scenario of ['ready', 'failed', 'unavailable', 'missing', 'stale', 'zero']) {
  test(`fresh lifecycle observations: ${scenario}, retries and session restoration`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    let observed = 0
    const first = run('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'monitoring', null)
    let history = [first]
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/v1/auth/config') return route.fulfill({ json: { mode: 'development' } })
      if (path === '/api/v1/me') return route.fulfill({ json: { actor_id: 'operator', organization_id: 'org', roles: ['deployer'], authentication: 'oidc' } })
      if (path === `/api/v1/applications/${application.id}`) return route.fulfill({ json: application })
      if (path.endsWith('/deployments')) return route.fulfill({ json: { deployments: history } })
      if (path.endsWith('/deployment-policy')) return route.fulfill({ json: { enabled: false, execution_available: true, actor_id: 'operator' } })
      if (path.endsWith('/release-status')) return route.fulfill({ json: { status: 'verified', baseline_sha: 'b'.repeat(40), source_sha: 'c'.repeat(40), build: { source_sha: 'c'.repeat(40), image_reference: first.image_reference, run_id: 20, run_attempt: 1, artifact_id: 80 } } })
      if (path.endsWith('/events')) return route.fulfill({ json: { events: [] } })
      if (path.endsWith('/observation')) {
        observed++
        const status = observed < 3 ? 'pending' : scenario
        const counts = status === 'unavailable' || status === 'missing' ? {} : { desired_replicas: scenario === 'zero' ? 0 : 1, ready_replicas: status === 'ready' ? 1 : 0, available_replicas: status === 'ready' ? 1 : 0 }
        return route.fulfill({ json: { status: status === 'stale' ? 'pending' : status, ...counts, observed_at: scenario === 'stale' ? '2020-01-01T00:00:00Z' : status === 'unavailable' ? null : new Date().toISOString(), code: status === 'unavailable' ? 'cluster_access_expired' : status === 'missing' ? 'deployment_absent' : undefined } })
      }
      return route.fulfill({ status: 404, json: { detail: `Unexpected mock route: ${path}` } })
    })
    await page.goto(`/applications/${application.id}`)
    await page.getByRole('button', { name: 'Connect to local backend' }).click()
    await expect(page).toHaveTitle(/DeployFlow/)
    await expect(page).toHaveURL(new RegExp(`/applications/${application.id}$`))
    const live = page.getByRole('region', { name: 'Current application state' })
    await expect(live.getByText(scenario === 'zero' ? /Replicas: 0\/0 ready/ : /Replicas: 0\/1 ready/)).toBeVisible()
    // The real polling loop performs multiple cycles before the observation changes.
    await expect.poll(() => observed, { timeout: 18000 }).toBeGreaterThanOrEqual(3)
    if (scenario === 'ready') await expect(live.getByText(/Replicas: 1\/1 ready · 1 available/)).toBeVisible()
    if (scenario === 'unavailable') {
      await expect(live.getByText(/cluster_access_expired/)).toBeVisible()
      await expect(live.getByText(/Replicas:/)).toHaveCount(0)
    }
    if (scenario === 'missing') await expect(live.getByText(/deployment_absent/)).toBeVisible()
    if (scenario === 'failed') await expect(live.getByText(/Current cluster observation: failed/)).toBeVisible()
    if (scenario === 'stale') await expect(live.getByText(/Last observed:.*stale/)).toBeVisible()
    await page.screenshot({ path: `/tmp/deployflow-phase7-${scenario}.png`, fullPage: false })
    // A later release must become the default details selection, not remain
    // pinned to the bootstrap PR. Explicit history selection is still available.
    const next = run('dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'checking', first.release_id)
    history = [next, first]
    await page.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Selected deployment' }).getByText(next.id, { exact: false }).first()).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Current application state' })).toBeVisible()
    await expect(page.getByText('2 stored releases')).toBeVisible()
    await expect(live.getByText(/Automatic deployment: disabled/)).toBeVisible()
    await expect(page.locator('vite-error-overlay')).toHaveCount(0)
    expect(errors).toEqual([])
  })
}

for (const failSave of [false, true]) {
  test(`reviews explicit future-source and handler authorization; save failure=${failSave}`, async ({ page }) => {
    const errors: string[] = []
    const consoleErrors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
    const source = 'd'.repeat(40)
    let policy = { enabled: false, allowed_source_sha: source as string | null, source_compatibility_policy: 'node-source-compatibility-v1', execution_available: true, cleanup_available: false, actor_id: 'operator' }
    const changes: unknown[] = []
    let executionRequests = 0
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/v1/auth/config') return route.fulfill({ json: { mode: 'development' } })
      if (path === '/api/v1/me') return route.fulfill({ json: { actor_id: 'operator', organization_id: 'org', roles: ['deployer'], authentication: 'oidc' } })
      if (path === `/api/v1/applications/${application.id}`) return route.fulfill({ json: application })
      if (path.endsWith('/deployment-policy')) {
        if (route.request().method() === 'PUT') {
          const body = route.request().postDataJSON()
          changes.push(body)
          expect(route.request().headers()['idempotency-key']).toBeTruthy()
          if (failSave) return route.fulfill({ status: 409, json: { detail: 'Deployment policy changed; review again' } })
          policy = { ...policy, ...body }
        }
        return route.fulfill({ json: policy })
      }
      if (path.endsWith('/deployments')) return route.fulfill({ json: { deployments: [] } })
      if (path.endsWith('/observation')) return route.fulfill({ json: { status: 'missing', observed_at: new Date().toISOString() } })
      if (path.endsWith('/release-status')) return route.fulfill({ json: { status: 'verified', source_sha: source, baseline_sha: 'c'.repeat(40), deployment: { id: 'candidate-deployment', release_id: 'candidate-release', status: 'waiting_for_executor' } } })
      if (path.endsWith('/execute')) executionRequests++
      return route.fulfill({ status: 404, json: { detail: 'Unexpected mock endpoint' } })
    })
    await page.goto(`/applications/${application.id}`)
    await page.getByRole('button', { name: 'Connect to local backend' }).click()
    await expect(page).toHaveTitle(/DeployFlow/)
    await expect(page.getByRole('heading', { name: 'Current application state' })).toBeVisible()
    await expect(page.getByText(/Candidate deployment: waiting_for_executor/)).toBeVisible()
    await page.getByRole('button', { name: 'Enable deployment for this application' }).click()
    const review = page.getByRole('form', { name: 'Review deployment authorization' })
    await expect(review.getByLabel('Approved source SHA')).toHaveValue(source)
    await expect(review.getByRole('button', { name: 'Confirm deployment policy' })).toBeDisabled()
    expect(changes).toEqual([])
    await review.getByLabel('Source scope', { exact: true }).selectOption('future')
    await review.getByLabel('Compatibility rules').selectOption('node-handler-compatibility-v2')
    await expect(review.getByText(/preflight already in progress/)).toBeVisible()
    await review.getByRole('checkbox').check()
    await page.screenshot({ path: `/tmp/deployflow-automatic-policy-review-${failSave}.png`, fullPage: true })
    await review.getByRole('button', { name: 'Confirm deployment policy' }).click()
    await expect.poll(() => changes.length).toBe(1)
    expect(changes[0]).toEqual({ enabled: true, allowed_source_sha: null, source_compatibility_policy: 'node-handler-compatibility-v2' })
    if (failSave) {
      await expect(page.getByText('Deployment policy changed; review again')).toBeVisible()
      await expect(review).toBeVisible()
      await expect(review.getByRole('button', { name: 'Confirm deployment policy' })).toBeEnabled()
    } else {
      await expect(review).toHaveCount(0)
      await expect(page.getByText('Policy authorizes future compatible commits on the configured branch.')).toBeVisible()
      await page.reload()
      await expect(page.getByText('Policy authorizes future compatible commits on the configured branch.')).toBeVisible()
      await expect(page.getByText(/Candidate deployment: waiting_for_executor/)).toBeVisible()
      await page.getByRole('button', { name: 'Disable future automatic deployments' }).click()
      await expect.poll(() => changes.length).toBe(2)
      expect(changes[1]).toEqual({ enabled: false })
    }
    expect(executionRequests).toBe(0)
    expect(errors).toEqual([])
    // A rejected API request is expected to emit a browser network error.
    expect(consoleErrors.filter(message => !failSave || !message.includes('409'))).toEqual([])
    await expect(page.getByText('Vite Error')).toHaveCount(0)
  })
}

test('dashboard distinguishes last success, pending release and disabled execution, then refreshes', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.clock.install()
  let reads = 0
  let progressed = false
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/auth/config') return route.fulfill({ json: { mode: 'development' } })
    if (path === '/api/v1/me') return route.fulfill({ json: { actor_id: 'operator', organization_id: 'org', roles: ['deployer'], authentication: 'oidc' } })
    if (path === '/api/v1/services') return route.fulfill({ json: { namespace: 'deployflow-test', observed_at: new Date().toISOString(), workloads: [], services: [] } })
    if (path === '/api/v1/applications') {
      reads++
      return route.fulfill({ json: [{ ...application, deployment_policy_enabled: true, deployment_execution_available: false,
        last_successful_deployment: { source_sha: 'd'.repeat(40), image_reference: 'ghcr.io/example/fixture@sha256:old' },
        latest_deployment: { source_sha: 'e'.repeat(40), image_reference: 'ghcr.io/example/fixture@sha256:new', status: progressed ? 'checking' : 'waiting_for_executor' },
        source_verification: { status: 'verified' },
      }] })
    }
    return route.fulfill({ status: 404, json: { detail: 'Unexpected mock endpoint' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Connect to local backend' }).click()
  await expect(page.getByText('Automatic policy: enabled · Execution: disabled')).toBeVisible()
  await expect(page.getByText(/Last successful source: dddddddddddd · historical result/)).toBeVisible()
  await expect(page.getByText('Latest release: waiting_for_executor')).toBeVisible()
  progressed = true
  await page.clock.fastForward(31000)
  await expect(page.getByText('Latest release: checking')).toBeVisible()
  expect(reads).toBeGreaterThan(1)
  await page.screenshot({ path: '/tmp/deployflow-automatic-dashboard.png', fullPage: false })
  expect(errors).toEqual([])
})
