import { expect, test } from '@playwright/test'

test('redeems an OIDC callback once even when auth configuration loads twice', async ({ page }) => {
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
      authorization_endpoint: 'http://localhost:8180/auth', token_endpoint: 'http://127.0.0.1:5174/oidc/token',
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
  await expect(page.getByText('Authenticated backend')).toBeVisible()
  await expect(page).toHaveURL(/\/services$/)
  expect(exchanges).toBe(1)
})
