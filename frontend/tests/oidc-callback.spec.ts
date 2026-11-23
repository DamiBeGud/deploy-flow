import { expect, test } from '@playwright/test'

test('redeems an OIDC callback once even when auth configuration loads twice', async ({ page, baseURL }) => {
  let exchanges = 0
  await page.addInitScript(() => {
    sessionStorage.setItem('deployflow:v1:oidc_state', 'expected-state')
    sessionStorage.setItem('deployflow:v1:oidc_verifier', 'expected-verifier')
    sessionStorage.setItem('deployflow:v1:oidc_return', '/services')
  })
  await page.route('**/oidc/token', async route => {
    exchanges += 1
    expect(route.request().postData()).toContain('code=single-use-code')
    expect(route.request().postData()).toContain('code_verifier=expected-verifier')
    await route.fulfill({ json: { access_token: 'local-test-token' } })
  })
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/auth/config') return route.fulfill({ json: {
      mode: 'oidc', issuer: 'http://localhost:8180/realms/deployflow-local',
      authorization_endpoint: 'http://localhost:8180/auth', token_endpoint: new URL('/oidc/token', baseURL).toString(),
      client_id: 'deployflow-local-ui',
    } })
    if (path === '/api/v1/me') {
      expect(route.request().headers().authorization).toBe('Bearer local-test-token')
      return route.fulfill({ json: { actor_id: 'local-user', organization_id: 'local-org', roles: ['administrator'], authentication: 'oidc' } })
    }
    if (path === '/api/v1/applications') return route.fulfill({ json: [] })
    if (path === '/api/v1/services') return route.fulfill({ json: { cluster_id: 'local-k3s', namespace: 'deployflow-test', observed_at: '2026-10-14T10:00:00Z', workloads: [], services: [] } })
    return route.fulfill({ status: 404, json: { detail: `Unexpected mock route: ${path}` } })
  })
  await page.goto('/auth/callback?code=single-use-code&state=expected-state')
  await expect(page.getByRole('heading', { name: 'Applications', exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/services$/)
  expect(exchanges).toBe(1)
})

test('restores a saved setup step after sign-in and drops unrelated query parameters', async ({ page, baseURL }) => {
  const appId = '11111111-1111-4111-8111-111111111111'
  const runId = '22222222-2222-4222-8222-222222222222'
  const savedPath = `/deploy?application=${appId}&run=${runId}&step=source&prepare=1`
  await page.route('**/oidc/authorize?**', route => {
    const request = new URL(route.request().url())
    expect(request.searchParams.get('code_challenge')).toBeTruthy()
    const callback = new URL('/auth/callback', baseURL)
    callback.searchParams.set('state', request.searchParams.get('state')!)
    callback.searchParams.set('code', 'localhost-test-code')
    return route.fulfill({ status: 302, headers: { location: callback.toString() } })
  })
  await page.route('**/oidc/token', route => route.fulfill({ json: { access_token: 'localhost-test-token' } }))
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/auth/config') return route.fulfill({ json: {
      mode: 'oidc', client_id: 'local-test',
      authorization_endpoint: new URL('/oidc/authorize', baseURL).toString(),
      token_endpoint: new URL('/oidc/token', baseURL).toString(),
    } })
    if (path === '/api/v1/me') return route.fulfill({ json: {
      actor_id: 'test-user', organization_id: 'test-org', roles: ['viewer'], authentication: 'oidc',
    } })
    if (path === `/api/v1/applications/${appId}`) return route.fulfill({ json: {
      id: appId, name: 'Saved Recipe Index', repository_url: 'https://github.com/example/recipe-index', revision: 'main',
    } })
    if (path === `/api/v1/analyses/${runId}`) return route.fulfill({ json: {
      id: runId, application_id: appId, status: 'completed', analysis_mode: 'deterministic', attempts: 1, revision: 'main',
    } })
    if (path.endsWith('/deployment-intent') || path.endsWith('/infrastructure-plans/latest')) return route.fulfill({ body: 'null', contentType: 'application/json' })
    return route.fulfill({ status: 404, json: { detail: 'Unexpected mock route' } })
  })

  await page.goto(savedPath + '&redirect=https%3A%2F%2Fexample.invalid')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).toHaveURL(new URL(savedPath, baseURL).toString())
  await expect(page.getByRole('heading', { name: 'Saved Recipe Index' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: 'Source' })).toHaveAttribute('aria-current', 'step')
})
