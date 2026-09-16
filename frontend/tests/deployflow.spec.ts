import { expect, test } from '@playwright/test'

test.setTimeout(45000)

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

test('persists application, streams analysis across reload, saves assessment and reanalyzes', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  const { name, runId, appId } = await start(page)
  await expect(page.getByLabel('Analysis progress')).toBeVisible()
  await expect.poll(() => page.evaluate(id => Number(sessionStorage.getItem('deployflow:v1:events:' + id)), runId)).toBeGreaterThan(0)
  const reconnect = page.waitForRequest(r => r.url().includes('/events?after=') && Number(new URL(r.url()).searchParams.get('after')) > 0)
  await page.reload()
  await reconnect
  await expect(page.getByText('Analysis completed', { exact: true })).toBeVisible({ timeout: 20000 })
  const run = await (await page.request.get('/api/v1/analyses/' + runId)).json()
  expect(run.application_id).toBe(appId)
  expect(run.status).toBe('completed')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('4587')
  await page.getByLabel('Service port').fill('8088')
  await page.getByLabel('Health check path').fill('/ready')
  await page.getByRole('button', { name: 'Save assessment' }).click()
  await expect(page.getByText('Assessment version 2', { exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('8088')
  await expect(page.getByLabel('Health check path')).toHaveValue('/ready')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByLabel('Container image')).toBeDisabled()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByRole('button', { name: 'Create infrastructure PR — coming soon' })).toBeDisabled()
  await page.getByRole('button', { name: 'Source', exact: true }).click()
  await page.getByRole('button', { name: 'Reanalyze repository' }).click()
  await expect(page).not.toHaveURL(new RegExp('run=' + runId))
  await expect(page.getByText('Analysis completed', { exact: true })).toBeVisible({ timeout: 20000 })
  const old = await (await page.request.get('/api/v1/analyses/' + runId)).json()
  expect(old.assessment.port.value).toBe(8088)
  await page.getByRole('link', { name: 'View applications', exact: true }).click()
  await expect(page.getByRole('row').filter({ hasText: name })).toBeVisible()
  expect(errors).toEqual([])
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
  await expect(page.getByText('Analysis completed', { exact: true })).toBeVisible({ timeout: 20000 })
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('4587')
  const changed = await page.request.patch('/api/v1/analyses/' + runId + '/assessment', {
    headers: { 'Idempotency-Key': crypto.randomUUID() }, data: { expected_version: 1, port: 9090 },
  })
  expect(changed.status()).toBe(200)
  await page.getByLabel('Service port').fill('8081')
  await page.getByRole('button', { name: 'Save assessment' }).click()
  await expect(page.getByRole('alert')).toContainText('Assessment version changed')
  await expect(page.getByLabel('Service port')).toHaveValue('8081')
  await page.getByRole('button', { name: 'Reload latest assessment' }).click()
  await expect(page.getByLabel('Service port')).toHaveValue('9090')
})

test('keeps the sidebar fixed while the form scrolls', async ({ page }) => {
  await start(page)
  await expect(page.getByText('Analysis completed', { exact: true })).toBeVisible({ timeout: 20000 })
  await page.getByRole('button', { name: 'Continue' }).click()
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
  await expect(page.getByText('Analysis completed', { exact: true })).toBeVisible({ timeout: 20000 })
  expect(keys).toHaveLength(2)
  expect(keys[0]).toBe(keys[1])
  const apps = await (await page.request.get('/api/v1/applications?limit=100')).json()
  expect(apps.filter((application: { name: string }) => application.name === name)).toHaveLength(1)
})
