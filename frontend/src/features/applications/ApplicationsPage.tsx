import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Box, Plus } from 'lucide-react'
import { errorMessage, request } from '../../shared/api'
import type { Application } from '../../shared/contracts'
import { ErrorNotice } from '../../shared/ui'

export function ApplicationsPage({ canWrite }: { canWrite: boolean }) {
  const [apps, setApps] = useState<Application[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const abort = new AbortController()
    // Synchronize the loading indicator with the new network request.
    // eslint-disable-next-line react/set-state-in-effect
    setLoading(true); setError('')
    request<Application[]>('/applications?limit=100', { signal: abort.signal })
      .then(setApps).catch(e => { if (!abort.signal.aborted) setError(errorMessage(e)) })
      .finally(() => { if (!abort.signal.aborted) setLoading(false) })
    return () => abort.abort()
  }, [revision])
  return <>
    <header className="page-header"><div><p className="page-eyeline">Local workspace</p><h1>Applications</h1><p className="page-subtitle">Repositories registered in your backend.</p></div>{canWrite && <Link className="button button-primary" to="/deploy"><Plus size={17} />Add application</Link>}</header>
    <ErrorNotice message={error} />
    <section className="services-table-wrap"><div className="table-heading"><div><h2>Registered applications</h2><p>Latest {apps.length} applications · Stored in PostgreSQL</p></div><button className="button button-secondary" onClick={() => setRevision(n => n + 1)} disabled={loading}>Refresh</button></div>
      {loading ? <p className="empty-state" role="status">Loading applications…</p> : apps.length === 0 ? <p className="empty-state">{error ? 'Applications could not be loaded.' : 'No applications yet. Add a repository to start an analysis.'}</p> : <table><thead><tr><th>Application</th><th>Repository</th><th>Branch / latest release</th><th>Lifecycle</th><th>Workflow</th></tr></thead><tbody>{apps.map(application => <tr key={application.id}><td><div className="service-name"><span className="service-icon"><Box size={16} /></span><b>{application.name}</b></div></td><td><span className="repository-url">{application.repository_url}</span></td><td><code>{application.revision}</code><small className="lifecycle-subline">Automatic deployment: {application.deployment_policy_enabled ? 'enabled' : 'disabled'}</small>{application.latest_deployment && <><small className="lifecycle-subline">{application.latest_deployment.status}{application.latest_deployment.error_code ? ` · ${application.latest_deployment.error_code}` : ''}</small><small className="lifecycle-subline"><code>{application.latest_deployment.source_sha.slice(0, 12)}</code></small></>}</td><td><Link className="button button-ghost" to={`/applications/${application.id}`}>View <ArrowRight size={15} /></Link></td><td><Link className="button button-ghost" to={`/deploy?application=${application.id}`}>Open <ArrowRight size={15} /></Link></td></tr>)}</tbody></table>}
    </section>
  </>
}
