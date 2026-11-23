import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Plus, Search } from 'lucide-react'
import { errorMessage, request } from '../../shared/api'
import type { Application } from '../../shared/contracts'
import { deploymentLabel, statusLabel } from '../../shared/deploymentStatus'
import { ErrorNotice } from '../../shared/ui'

export function ApplicationsPage({ canWrite }: { canWrite: boolean }) {
  const [apps, setApps] = useState<Application[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const [filter, setFilter] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    // Synchronize the loading indicator with the new network request.
    // eslint-disable-next-line react/set-state-in-effect
    setLoading(true); setError('')
    let timer = 0
    let failures = 0
    const load = async () => {
      if (document.hidden) { timer = window.setTimeout(() => void load(), 30000); return }
      let delay = 30000
      try {
        const result = await request<Application[]>('/applications?limit=100', { signal: abort.signal })
        if (!abort.signal.aborted) { setApps(result); setError(''); failures = 0 }
      } catch (reason) {
        if (!abort.signal.aborted) { setError(errorMessage(reason)); failures++; delay = Math.min(30000 * 2 ** failures, 60000) }
      } finally {
        if (!abort.signal.aborted) { setLoading(false); timer = window.setTimeout(() => void load(), delay) }
      }
    }
    void load()
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [revision])
  const query = filter.trim().toLowerCase()
  const matching = apps.filter(app => `${app.name} ${app.repository_url} ${app.revision}`.toLowerCase().includes(query))

  return <>
    <header className="page-header">
      <h1>Applications</h1>
      {canWrite && <Link className="button button-primary" to="/deploy"><Plus size={17} />Add application</Link>}
    </header>
    <ErrorNotice message={error} />
    <section className="services-table-wrap application-list" aria-label="Registered applications">
      <div className="table-heading">
        <label className="application-search">
          <Search size={16} aria-hidden="true" />
          <input type="search" aria-label="Search applications" placeholder="Search by name, repository or branch" value={filter} onChange={event => setFilter(event.target.value)} />
        </label>
        <button className="button button-secondary" type="button" onClick={() => setRevision(n => n + 1)} disabled={loading}>Refresh applications</button>
      </div>
      {loading && !apps.length ? <p className="empty-state" role="status">Loading applications…</p>
        : !matching.length ? <p className="empty-state">{error && !apps.length ? 'Applications could not be loaded.' : query ? 'No applications match your search.' : 'No applications yet. Add a repository to start.'}</p>
        : <ul className="application-rows" aria-label="Apps">
          {matching.map(app => <li key={app.id}>
            <div><Link to={`/applications/${app.id}`}>{app.name}</Link><small className="lifecycle-subline"><code>{app.revision}</code></small></div>
            <span className="repository-url">{app.repository_url.replace(/^https:\/\/github\.com\//, '')}</span>
            <div>{app.latest_deployment
              ? <>{deploymentLabel(app.latest_deployment.status, app.latest_deployment.error_code)}{app.last_successful_deployment && <small className="lifecycle-subline">Last successful source <code>{app.last_successful_deployment.source_sha.slice(0, 12)}</code></small>}</>
              : app.source_verification ? statusLabel(app.source_verification.status) : 'Not deployed yet'}
              {app.deployment_execution_available === false && <small className="lifecycle-subline">Deployment execution unavailable</small>}
            </div>
            <Link aria-label={`Open setup for ${app.name}`} to={`/deploy?application=${app.id}`}>Open setup</Link>
          </li>)}
        </ul>}
    </section>
  </>
}
