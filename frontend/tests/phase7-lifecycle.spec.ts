import { expect, test } from '@playwright/test'

const application = { id: '11111111-1111-4111-8111-111111111111', name: 'fixture', repository_url: 'https://github.com/example/fixture', revision: 'main', latest_analysis: null }

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
