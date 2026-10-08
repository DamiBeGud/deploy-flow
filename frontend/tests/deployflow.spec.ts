import { expect, test } from '@playwright/test'

test.setTimeout(120000)

const readyAnalysis = (page: import('@playwright/test').Page) =>
  page.getByText(/Analysis (completed|needs_input)/)

async function connect(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Connect to local backend' }).click()
  await expect(page.getByRole('heading', { name: 'Applications', exact: true })).toBeVisible()
}

async function start(page: import('@playwright/test').Page) {
  await connect(page)
  await page.getByRole('link', { name: 'Add application', exact: true }).last().click()
  const name = 'e2e-' + crypto.randomUUID().slice(0, 8)
  await page.getByLabel('Application name').fill(name)
  const queued = page.waitForResponse(r => /\/applications\/[^/]+\/analyses$/.test(r.url()) && r.request().method() === 'POST')
  await page.getByRole('button', { name: 'Analyze repository' }).click()
  const response = await queued
  expect(response.status()).toBe(202)
  const run = await response.json()
  await expect(page).toHaveURL(new RegExp('run=' + run.run_id))
  return { name, runId: run.run_id as string, appId: new URL(page.url()).searchParams.get('application')! }
}

test('persists application, streams analysis across reload, saves delivery settings and reanalyzes', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  const { name, runId, appId } = await start(page)
  await expect(page.getByLabel('Analysis progress')).toBeVisible()
  await expect.poll(() => page.evaluate(id => Number(sessionStorage.getItem('deployflow:v1:events:' + id)), runId)).toBeGreaterThan(0)
  const reconnect = page.waitForRequest(r => r.url().includes('/events?after=') && Number(new URL(r.url()).searchParams.get('after')) > 0)
  await page.reload()
  await reconnect
  await expect(readyAnalysis(page)).toBeVisible({ timeout: 60000 })
  const run = await (await page.request.get('/api/v1/analyses/' + runId)).json()
  expect(run.application_id).toBe(appId)
  expect(['completed', 'needs_input']).toContain(run.status)
  await page.getByRole('button', { name: 'Continue to connectivity' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('4587')
  await page.getByLabel('Service port').fill('8088')
  const portRecommendation = page.locator('.recommended-field').filter({ has: page.getByLabel('Service port') })
  await expect(portRecommendation.getByText('User override', { exact: true })).toBeVisible()
  await portRecommendation.getByRole('button', { name: 'Reset to recommendation' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('4587')
  await page.getByLabel('Service port').fill('8088')
  await page.getByLabel('Health path').fill('/ready')
  const savedRequest = page.waitForRequest(request => request.method() === 'PUT' && request.url().includes('/deployment-intent'))
  await page.getByRole('button', { name: 'Save connectivity' }).click()
  const savedBody = (await savedRequest).postDataJSON()
  expect(savedBody).not.toHaveProperty('id')
  expect(savedBody).not.toHaveProperty('created_at')
  expect(savedBody).not.toHaveProperty('version')
  expect(savedBody).not.toHaveProperty('assessment_version')
  await expect(page.getByText('Delivery settings saved.', { exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: 'Continue to connectivity' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('8088')
  await expect(page.getByLabel('Health path')).toHaveValue('/ready')
  await page.getByRole('button', { name: 'Save and continue' }).click()
  await expect(page.getByRole('heading', { name: 'Kubernetes bundle review', exact: true })).toBeVisible()
  await expect(page.getByLabel('Application image')).toBeEnabled()
  await page.getByRole('button', { name: 'Source', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Kubernetes recommendation', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Bundle', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Bundle', exact: true }).click()
  await page.getByRole('button', { name: 'Generate bundle' }).click()
  await expect(page.getByRole('region', { name: 'Generated file review' })).toBeVisible()
  await page.getByRole('tab', { name: /deploy\/kubernetes\/service.yaml/ }).click()
  await expect(page.getByRole('tabpanel')).toContainText('kind: Service')
  await page.getByRole('button', { name: 'Save and review' }).click()
  await expect(page.getByRole('heading', { name: 'Review delivery plan', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Bundle proposed' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Generated file review' })).toBeVisible()
  const retryValidation = page.waitForResponse(response => response.request().method() === 'POST' && /\/infrastructure-plans\/[^/]+\/validations$/.test(response.url()))
  await page.getByRole('button', { name: 'Retry validation' }).click()
  expect((await retryValidation).status()).toBe(201)
  await page.getByRole('button', { name: 'Source', exact: true }).click()
  await page.getByRole('button', { name: 'Reanalyze repository' }).click()
  await expect(page).not.toHaveURL(new RegExp('run=' + runId))
  await expect(page.getByText(/Analysis (completed|needs_input|failed)/)).toBeVisible({ timeout: 60000 })
  const old = await (await page.request.get('/api/v1/applications/' + appId + '/deployment-intent?analysis_run_id=' + runId)).json()
  expect(old.connectivity.port).toBe(8088)
  await page.getByRole('link', { name: 'View applications', exact: true }).click()
  await expect(page.getByRole('row').filter({ hasText: name })).toBeVisible()
  expect(errors).toEqual([])
})

test('review approval queues a PR and displays its GitHub status without deploying', async ({ page }) => {
  const errors: string[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  await page.route(/\/api\/v1\/infrastructure-plans\/[^/]+\/validations$/, route => route.fulfill({ status: 201, json: {
    id: 'mock-admission', status: 'passed', observed_at: '2026-10-08T09:00:00Z',
    checks: [{ kind: 'Deployment', name: 'fixture', code: 'admission_passed' }],
  } }))
  const pr = {
    id: 'mock-pr', plan_id: '', status: 'queued', url: null, number: null,
    head_sha: null, merged_sha: null, error_code: null, verified_build: null,
  }
  await page.route(/\/api\/v1\/infrastructure-plans\/[^/]+\/pull-requests$/, route => {
    pr.plan_id = route.request().url().split('/').at(-2) || ''
    return route.fulfill({ status: 202, json: pr })
  })
  await page.route(/\/api\/v1\/infrastructure-plans\/[^/]+$/, async route => {
    if (route.request().method() !== 'GET') return route.continue()
    const response = await route.fetch()
    const plan = await response.json()
    return route.fulfill({ response, json: { ...plan, status: 'approved', pull_request: pr, cluster_validation: {
      status: 'passed', observed_at: '2026-10-08T09:00:00Z',
      checks: [{ kind: 'Deployment', name: 'fixture', code: 'admission_passed' }],
    } } })
  })
  await page.route('**/api/v1/pull-requests/mock-pr/refresh', route => route.fulfill({ status: 202, json: pr }))
  await page.route('**/api/v1/pull-requests/mock-pr', route => route.fulfill({ json: {
    ...pr, status: 'open', number: 7, url: 'https://github.com/example/fixture/pull/7',
  } }))
  await start(page)
  await expect(page).toHaveTitle(/DeployFlow/)
  await expect(readyAnalysis(page)).toBeVisible({ timeout: 60000 })
  await page.getByRole('button', { name: 'Continue to connectivity' }).click()
  await page.getByRole('button', { name: 'Save and continue' }).click()
  await page.getByRole('button', { name: 'Generate bundle' }).click()
  await page.getByRole('button', { name: 'Save and review' }).click()
  await expect(page.getByRole('heading', { name: 'Review delivery plan' })).toBeVisible()
  await expect(page.getByText('Result: passed')).toBeVisible()
  await page.getByRole('button', { name: 'Approve and create infrastructure PR' }).click()
  await expect(page.getByText('Status: queued')).toBeVisible()
  await page.getByRole('button', { name: 'Refresh PR and CI status' }).click()
  await expect(page.getByRole('link', { name: 'Open PR #7 on GitHub' })).toHaveAttribute('href', 'https://github.com/example/fixture/pull/7')
  await expect(page.getByText('Creating a PR does not deploy anything.')).toBeVisible()
  await page.screenshot({ path: '/tmp/deployflow-phase6-review.png', fullPage: false })
  expect(errors).toEqual([])
  expect(consoleErrors).toEqual([])
})

test('cancels a running backend analysis', async ({ page }) => {
  const { runId } = await start(page)
  await page.getByRole('button', { name: 'Cancel analysis' }).click()
  await expect(page.getByText('Analysis cancelled', { exact: true })).toBeVisible()
  const run = await (await page.request.get('/api/v1/analyses/' + runId)).json()
  expect(run.status).toBe('cancelled')
})

test('reports backend unavailable and retries the identity connection', async ({ page }) => {
  await page.route('**/api/v1/me', route => route.fulfill({ status: 503, contentType: 'application/problem+json', body: JSON.stringify({ detail: 'Backend temporarily unavailable' }) }))
  await page.goto('/')
  await page.getByRole('button', { name: 'Connect to local backend' }).click()
  await expect(page.getByRole('alert')).toContainText('Backend temporarily unavailable')
  await page.unroute('**/api/v1/me')
  await page.getByRole('button', { name: 'Connect to local backend' }).click()
  await expect(page.getByRole('heading', { name: 'Applications', exact: true })).toBeVisible()
})

test('handles optimistic concurrency without overwriting a newer assessment', async ({ page }) => {
  const { runId } = await start(page)
  await expect(readyAnalysis(page)).toBeVisible({ timeout: 60000 })
  await page.getByRole('button', { name: 'Continue to connectivity' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('4587')
  const changed = await page.request.patch('/api/v1/analyses/' + runId + '/assessment', {
    headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { expected_version: 1, port: 9090 },
  })
  expect(changed.status()).toBe(200)
  await page.getByLabel('Service port').fill('8081')
  await page.getByRole('button', { name: 'Save connectivity' }).click()
  await expect(page.getByRole('alert')).toContainText('Assessment version changed')
  await expect(page.getByLabel('Service port')).toHaveValue('8081')
  await page.reload()
  await page.getByRole('button', { name: 'Continue to connectivity' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('9090')
})

test('keeps the sidebar fixed while the form scrolls', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 400 })
  await start(page)
  await expect(readyAnalysis(page)).toBeVisible({ timeout: 60000 })
  await page.getByRole('button', { name: 'Continue to connectivity' }).click()
  const sidebar = page.locator('.sidebar')
  await expect(sidebar).toHaveCSS('position', 'fixed')
  const before = await sidebar.boundingBox()
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0)
  const after = await sidebar.boundingBox()
  expect(after?.y).toBe(0)
  expect(after?.height).toBe(before?.height)
})

test('reuses the application command key after a response is lost', async ({ page }) => {
  await connect(page)
  await page.getByRole('link', { name: 'Add application', exact: true }).last().click()
  const name = 'retry-' + crypto.randomUUID().slice(0, 8)
  await page.getByLabel('Application name').fill(name)
  const keys: string[] = []
  await page.route('**/api/v1/applications', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    keys.push(route.request().headers()['idempotency-key'])
    if (keys.length === 1) {
      const committed = await route.fetch()
      expect(committed.status()).toBe(201)
      await route.abort('failed')
    } else await route.continue()
  })
  await page.getByRole('button', { name: 'Analyze repository' }).click()
  await expect(page.getByRole('alert')).toContainText('Cannot reach the backend')
  await page.getByRole('button', { name: 'Analyze repository' }).click()
  await expect(page.getByText(/Analysis (completed|needs_input|failed)/)).toBeVisible({ timeout: 60000 })
  expect(keys).toHaveLength(2)
  expect(keys[0]).toBe(keys[1])
  const apps = await (await page.request.get('/api/v1/applications?limit=100')).json()
  expect(apps.filter((application: { name: string }) => application.name === name)).toHaveLength(1)
})

test('shows observed Kubernetes workloads and preserves applications when cluster access fails', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/me', route => route.fulfill({ json: {
    actor_id: 'e2e-user', organization_id: 'e2e-org', roles: ['developer'], authentication: 'development', clusters: [],
  } }))
  await page.route('**/api/v1/applications?**', route => route.fulfill({ json: [] }))
  let failCluster = false
  await page.route(/\/api\/v1\/services\?/, route => {
    if (failCluster) return route.fulfill({ status: 503, contentType: 'application/problem+json', body: JSON.stringify({ detail: 'cluster_unavailable' }) })
    return route.fulfill({ json: {
      cluster_id: 'local-k3s', namespace: 'deployflow-test', observed_at: '2026-10-07T10:00:00Z',
      workloads: [{
        id: 'deployflow-test:Deployment:fixture', kind: 'Deployment', name: 'fixture',
        desired_replicas: 2, ready_replicas: 1, desired_completions: null, succeeded: null,
        image: 'ghcr.io/example/fixture:abc', services: ['fixture'], managed: false,
        conditions: [{ type: 'Available', status: 'False', reason: 'MinimumReplicasUnavailable' }],
      }], services: [{ name: 'fixture', address: '10.0.0.1' }],
    } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Connect to local backend' }).click()
  const observations = page.getByRole('region', { name: 'Kubernetes observations' })
  await expect(observations.getByRole('row').filter({ hasText: 'fixture' })).toContainText('1/2 ready')
  await expect(observations.getByText('10.0.0.1')).toBeVisible()
  await expect(observations.getByText(/Available: False/)).toBeVisible()
  await expect(observations.getByText('Observed / unmanaged')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Registered applications' })).toBeVisible()
  if (process.env.DF_E2E_SCREENSHOT) await page.screenshot({ path: process.env.DF_E2E_SCREENSHOT })
  failCluster = true
  await observations.getByRole('button', { name: 'Refresh' }).click()
  await expect(observations.getByText(/Cluster observations unavailable: cluster_unavailable/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Registered applications' })).toBeVisible()
  expect(errors).toEqual([])
})
