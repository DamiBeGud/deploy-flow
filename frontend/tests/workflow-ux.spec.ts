import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const app = {
  id: '11111111-1111-4111-8111-111111111111',
  name: 'recipe-index',
  repository_url: 'https://github.com/example/recipe-index',
  revision: 'main',
  lifecycle_version: 2,
}
const run = {
  id: '22222222-2222-4222-8222-222222222222',
  application_id: app.id, status: 'completed', analysis_mode: 'deterministic',
  commit_sha: 'a'.repeat(40), revision: 'main', progress: 100, attempts: 1, assessment_version: 1,
}
const head = 'b'.repeat(40)
const intent = {
  version: 1, assessment_version: 1, environment_profile_id: 'local-k3s', schema_version: 'deployment-intent-v1',
  field_recommendations: { 'connectivity.port': { origin: 'inferred' }, 'connectivity.health_path': { origin: 'inferred' } },
  connectivity: { port: 8401, health_path: '/ready', replicas: 1, resources: { requests: {}, limits: {} }, environment: {}, exposure: { kind: 'internal', path: '/' } },
  build: { branch: 'main', image: 'ghcr.io/example/recipe-index', install_command: ['bundle', 'install'], test_command: null, build_command: null, start_command: ['bundle', 'exec', 'puma'] },
}
const plan = {
  id: 'bundle', application_id: app.id, bundle_digest: 'd'.repeat(64), status: 'approved',
  validation: { image_binding: { preview_reference: 'ghcr.io/example/recipe-index:preview' }, checks: [] },
  cluster_validation: { status: 'passed', checks: [] },
  files: [
    { path: '.dockerignore', hash: 'x'.repeat(64), content: '.git', change: 'unchanged' },
    { path: 'Dockerfile', hash: 'y'.repeat(64), content: 'FROM ruby:3.3', change: 'added' },
  ],
  comparison: { added: ['Dockerfile'], changed: [], removed: ['old.yml'] },
  pull_request: { id: 'pr', plan_id: 'bundle', status: 'open', number: 3, head_sha: head,
    url: 'https://github.com/example/recipe-index/pull/3', merged_sha: null },
}
const failed = {
  id: 'failed-release', status: 'reconciling', error_code: 'application_start_failed', source_sha: run.commit_sha,
  application_id: app.id, kind: 'standard', created_at: '2026-11-22T18:00:00Z', resources: [],
  preflight_validation: { status: 'passed' }, rollout: { status: 'failed' }, health: { status: 'pending' },
}

async function mockWorkflow(page: Page, { viewer = false, blocked = false, editable = false, automatic = false, simulation = false, resume = false } = {}) {
  const commands: string[] = []
  let savedIntent = editable || (automatic && !resume) ? null : intent
  let savedPlan: (Omit<typeof plan, 'pull_request' | 'cluster_validation'> & { pull_request: typeof plan.pull_request | null; cluster_validation: typeof plan.cluster_validation | null }) | null = editable || blocked || automatic ? null : plan
  let savedPort: number | undefined
  const source = automatic ? { ...app, name: 'df-recipe-index-1111111111114111', revision: 'release/next' } : app
  const analysisMode = simulation ? 'fake' : run.analysis_mode
  const recommendation = automatic ? { ...intent, version: resume ? 1 : 0, build: { ...intent.build, branch: source.revision } } : intent
  if (resume) savedIntent = recommendation
  const analysis = automatic ? {
    ...run, revision: source.revision, analysis_mode: analysisMode,
    assessment: {
      mode: analysisMode, completion: 'complete', repository: { commit_sha: run.commit_sha },
      configuration: { required_variables: [], optional_variables: [] },
    },
  } : run
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    if (route.request().method() !== 'GET') {
      commands.push(path)
      if (automatic && path === '/applications') {
        expect(route.request().postDataJSON()).toEqual({ repository_url: source.repository_url, revision: source.revision })
        return route.fulfill({ status: 201, json: source })
      }
      if (automatic && path.endsWith('/analyses')) return route.fulfill({ status: 202, json: { run_id: run.id } })
      if ((editable || automatic) && path.endsWith('/deployment-intent')) {
        const body = route.request().postDataJSON()
        if (automatic) {
          expect(route.request().headers()['idempotency-key']).toBe(`prepare:${run.id}:1:0`)
          expect(body.build.branch).toBe(source.revision)
        }
        savedIntent = { ...intent, build: body.build, connectivity: body.connectivity }
        savedPort = body.connectivity.port
        return route.fulfill({ json: savedIntent })
      }
      if ((editable || automatic) && path.endsWith('/infrastructure-plans')) {
        if (automatic) expect(route.request().headers()['idempotency-key']).toBe(`prepare:${run.id}:1:1`)
        savedPlan = { ...plan, status: 'proposed', pull_request: null, cluster_validation: automatic ? null : plan.cluster_validation }
        return route.fulfill({ status: 201, json: savedPlan })
      }
      if (automatic && savedPlan && path.endsWith('/infrastructure-plans/bundle/validations')) {
        expect(route.request().postDataJSON()).toEqual({ expected_digest: plan.bundle_digest })
        savedPlan.cluster_validation = plan.cluster_validation
        return route.fulfill({ status: 201, json: plan.cluster_validation })
      }
      if (editable && path.endsWith('/infrastructure-plans/bundle/pull-requests')) {
        expect(route.request().postDataJSON().expected_digest).toBe(plan.bundle_digest)
        savedPlan = plan
        return route.fulfill({ status: 202, json: plan.pull_request })
      }
      return route.fulfill({ status: 400, json: { detail: 'Unexpected test command' } })
    }
    if (path === '/auth/config') return route.fulfill({ json: { mode: 'development' } })
    if (path === '/me') return route.fulfill({ json: { actor_id: 'preview', organization_id: 'test', roles: [viewer ? 'viewer' : 'deployer'], authentication: 'oidc' } })
    if (path === '/services') return route.fulfill({ json: { observed_at: new Date().toISOString(), services: [], workloads: [{ id: 'paused', name: 'paused-workload', kind: 'Deployment', desired_replicas: 0, ready_replicas: 0, managed: true, services: [], conditions: [] }] } })
    if (path === '/applications') return route.fulfill({ json: [app] })
    if (path === '/applications/' + app.id) return route.fulfill({ json: { ...source, latest_analysis: analysis } })
    if (path === '/analyses/' + run.id) return route.fulfill({ json: analysis })
    if (path === '/environment-profiles') return route.fulfill({ json: [{ id: 'local-k3s', namespace: 'deployflow-test', hostname_suffix: '.deployflow.local' }] })
    if (path.endsWith('/kubernetes-recommendation')) return route.fulfill({ json: { intent: recommendation, application_configuration: [], generation_blocker: blocked ? 'Detected service dependencies need approved configuration bindings.' : null } })
    if (path.endsWith('/deployment-intent')) return route.fulfill({ json: savedIntent })
    if (path.endsWith('/infrastructure-plans/latest') || path.endsWith('/infrastructure-plans/bundle')) return route.fulfill({ json: savedPlan })
    if (path === '/pull-requests/pr') return route.fulfill({ json: plan.pull_request })
    if (path.endsWith('/deployment-policy')) return route.fulfill({ json: { enabled: false, execution_available: true, cleanup_available: false, actor_id: 'preview' } })
    if (path.endsWith('/deployments')) return route.fulfill({ json: { deployments: [failed] } })
    if (path.endsWith('/observation')) return route.fulfill({ json: { status: 'failed', desired_replicas: 1, ready_replicas: 0, observed_at: new Date().toISOString() } })
    if (path.endsWith('/release-status')) return route.fulfill({ json: { status: 'verified', deployment: failed } })
    if (path.endsWith('/events')) return route.fulfill({ json: { events: [] } })
    return route.fulfill({ status: 404, json: { detail: 'Unexpected test route' } })
  })
  return { commands, savedPort: () => savedPort }
}

async function connect(page: Page, path: string) {
  await page.goto(path)
  await page.getByRole('button', { name: 'Connect to local backend' }).click()
}

async function prepareDelivery(page: Page) {
  await connect(page, '/deploy')
  await page.getByLabel('GitHub repository URL').fill(app.repository_url)
  await page.getByLabel('Repository revision').fill('release/next')
  await page.getByRole('button', { name: 'Prepare delivery' }).click()
}

test('prepares and dry-runs from only a URL and revision, then restores review without writing again', async ({ page }) => {
  const { commands } = await mockWorkflow(page, { automatic: true })
  await prepareDelivery(page)
  await expect(page.getByRole('heading', { name: 'Review', exact: true })).toBeVisible()
  await expect(page.getByText('Dry-run passed', { exact: true })).toBeVisible()
  const expected = [
    '/applications', `/applications/${app.id}/analyses`, `/applications/${app.id}/deployment-intent`,
    `/applications/${app.id}/infrastructure-plans`, '/infrastructure-plans/bundle/validations',
  ]
  expect(commands).toEqual(expected)
  await page.reload()
  await expect(page.getByText('Dry-run passed', { exact: true })).toBeVisible()
  expect(commands).toEqual(expected)
})

test('resumes automatic preparation from saved delivery settings without writing them again', async ({ page }) => {
  const { commands } = await mockWorkflow(page, { automatic: true, resume: true })
  await connect(page, `/deploy?application=${app.id}&run=${run.id}&step=source&prepare=1`)
  await expect(page.getByText('Dry-run passed', { exact: true })).toBeVisible()
  expect(commands).toEqual([`/applications/${app.id}/infrastructure-plans`, '/infrastructure-plans/bundle/validations'])
})

for (const simulation of [false, true]) {
  test(`does not prepare a bundle for ${simulation ? 'simulated analysis' : 'unapproved dependencies'}`, async ({ page }) => {
    const { commands } = await mockWorkflow(page, { automatic: true, blocked: !simulation, simulation })
    await prepareDelivery(page)
    await expect(page.getByRole('heading', { name: 'Connectivity', exact: true })).toBeVisible()
    await expect(page.getByRole('alert').filter({ hasText: simulation ? 'requires a complete, source-pinned assessment' : 'need approved configuration bindings' })).toBeVisible()
    expect(commands).toEqual(['/applications', `/applications/${app.id}/analyses`])
  })
}

test('restores saved review, then starts a blank source draft', async ({ page }) => {
  await mockWorkflow(page)
  await connect(page, `/deploy?application=${app.id}`)
  const stepper = page.getByRole('navigation', { name: 'Setup steps' })
  await expect(stepper.getByRole('button', { name: 'Review' })).toHaveAttribute('aria-current', 'step')
  await page.getByText('Inspect generated files', { exact: true }).click()
  const files = page.getByRole('region', { name: 'Generated file review' })
  await expect(files.getByRole('button', { name: /Dockerfile/ })).toHaveAttribute('aria-pressed', 'true')
  await expect(files.getByRole('button', { name: /dockerignore/ })).toHaveCount(0)
  await files.getByRole('button', { name: /old.yml/ }).click()
  await expect(files.getByText('This bundle removes the file.')).toBeVisible()
  await stepper.getByRole('button', { name: 'Source', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Source' })).toBeFocused()
  await expect(page.getByText(/Repository scan · Commit/)).toBeVisible()
  await page.reload()
  await expect(stepper.getByRole('button', { name: 'Source' })).toHaveAttribute('aria-current', 'step')
  await page.getByRole('navigation', { name: 'Workspace' }).getByRole('link', { name: 'Add application' }).click()
  await expect(page.getByLabel('GitHub repository URL')).toHaveValue('')
  await page.getByText('Advanced setup', { exact: true }).click()
  await expect(page.getByLabel('Application name (optional)')).toHaveValue('')
  await expect(page.getByLabel('Repository revision')).toHaveValue('main')
  await expect(stepper.getByRole('button', { name: 'Review' })).toBeDisabled()
})

test('gives blocked bundle guidance and hides the optional build warning', async ({ page }) => {
  await mockWorkflow(page, { blocked: true })
  await connect(page, `/deploy?application=${app.id}&run=${run.id}&step=bundle`)
  await expect(page.getByRole('heading', { name: 'Bundle', exact: true })).toBeVisible()
  await expect(page.getByText('Bundle blocked by missing setup')).toBeVisible()
  await expect(page.getByLabel('Build command (optional)')).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Generate bundle' })).toBeDisabled()
  await expect(page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: 'Review' })).toBeDisabled()
})

test('opens the exact PR scope for review and keeps a failed rollout separate', async ({ page }) => {
  const { commands } = await mockWorkflow(page)
  await connect(page, `/deploy?application=${app.id}&run=${run.id}&step=review`)
  await page.getByRole('link', { name: 'Review deployment authorization' }).click()
  const form = page.getByRole('form', { name: 'Review deployment authorization' })
  await expect(form.getByLabel('Source scope', { exact: true })).toHaveValue('reviewed')
  await expect(form.getByLabel('Approved PR head SHA')).toHaveValue(head)
  await expect(form.getByRole('button', { name: 'Confirm deployment policy' })).toBeDisabled()
  await expect(page.getByRole('region', { name: 'Latest release attempt' }).getByText('Application failed to start')).toBeVisible()
  await expect(page.getByRole('list', { name: 'Recorded deployment checks' }).getByText('Not reached')).toBeVisible()
  expect(commands).toEqual([])
})

test('keeps narrow screens readable and search available to viewers', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 680 })
  await mockWorkflow(page, { viewer: true })
  await connect(page, '/services')
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Add application' })).toHaveCount(0)
  await expect(page.getByText('Scaled to zero')).toBeVisible()
  await page.getByRole('searchbox', { name: 'Search applications' }).fill('does-not-match')
  await expect(page.getByText('No applications match your search.')).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(320)
  await page.goto(`/deploy?application=${app.id}&run=${run.id}&step=source`)
  await expect(page.getByRole('navigation', { name: 'Setup steps' }).getByRole('button', { name: 'Connectivity' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(320)
})


test('advances from source to the reviewed PR without submitting deployment authorization', async ({ page }) => {
  const { commands, savedPort } = await mockWorkflow(page, { editable: true })
  await connect(page, `/deploy?application=${app.id}&run=${run.id}&step=source`)
  await page.getByRole('button', { name: 'Continue to connectivity' }).click()
  await page.getByLabel('Service port').fill('8402')
  await page.getByRole('button', { name: 'Continue to bundle' }).click()
  await expect(page.getByRole('heading', { name: 'Bundle', exact: true })).toBeFocused()
  await page.getByRole('button', { name: 'Generate bundle' }).click()
  await expect(page.getByText('Dry-run passed')).toBeVisible()
  await page.getByRole('button', { name: 'Continue to review' }).click()
  await expect(page.getByRole('heading', { name: 'Review', exact: true })).toBeFocused()
  await page.getByRole('button', { name: 'Approve and create infrastructure PR' }).click()
  await expect(page.getByRole('link', { name: 'Open PR #3 on GitHub' })).toBeVisible()
  expect(savedPort()).toBe(8402)
  expect(commands).toEqual([
    `/applications/${app.id}/deployment-intent`, `/applications/${app.id}/deployment-intent`,
    `/applications/${app.id}/infrastructure-plans`, '/infrastructure-plans/bundle/pull-requests',
  ])
})
