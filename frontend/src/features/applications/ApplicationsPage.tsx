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
  return <>
    <header className="page-header"><div><p className="page-eyeline">Workspace</p><h1>Applications</h1><p className="page-subtitle">Repositories registered in your backend.</p></div>{canWrite && <Link className="button button-primary" to="/deploy"><Plus size={17} />Add application</Link>}</header>
    <ErrorNotice message={error} />
    <section className="services-table-wrap"><div className="table-heading"><div><h2>Registered applications</h2><p>Latest {apps.length} applications · Stored in PostgreSQL</p></div><button className="button button-secondary" onClick={() => setRevision(n => n + 1)} disabled={loading}>Refresh</button></div>
      {loading ? <p className="empty-state" role="status">Loading applications…</p> : apps.length === 0 ? <p className="empty-state">{error ? 'Applications could not be loaded.' : 'No applications yet. Add a repository to start an analysis.'}</p> : <table><thead><tr><th>Application</th><th>Repository</th><th>Branch / release progress</th><th>Lifecycle</th><th>Workflow</th></tr></thead><tbody>{apps.map(application => <tr key={application.id}><td><div className="service-name"><span className="service-icon"><Box size={16} /></span><b>{application.name}</b></div></td><td><span className="repository-url">{application.repository_url}</span></td><td><code>{application.revision}</code><small className="lifecycle-subline">Automatic policy: {application.deployment_policy_enabled ? 'enabled' : 'disabled'} · Execution: {application.deployment_execution_available == null ? 'unknown' : application.deployment_execution_available ? 'available' : 'disabled'}</small>{application.allowed_source_sha && <small className="lifecycle-subline">Source bound: <code>{application.allowed_source_sha.slice(0, 12)}</code></small>}{application.last_successful_deployment && <small className="lifecycle-subline">Last successful source: <code>{application.last_successful_deployment.source_sha.slice(0, 12)}</code> · historical result</small>}{application.latest_deployment && <><small className="lifecycle-subline">Latest release: {application.latest_deployment.status}{application.latest_deployment.error_code ? ` · ${application.latest_deployment.error_code}` : ''}</small><small className="lifecycle-subline"><code>{application.latest_deployment.source_sha.slice(0, 12)}</code></small></>}{application.source_verification && <small className="lifecycle-subline">Build verification: {application.source_verification.status}{application.source_verification.error_code ? ` · ${application.source_verification.error_code}` : ''}</small>}</td><td><Link className="button button-ghost" to={`/applications/${application.id}`}>View <ArrowRight size={15} /></Link></td><td><Link className="button button-ghost" to={`/deploy?application=${application.id}`}>Open <ArrowRight size={15} /></Link></td></tr>)}</tbody></table>}
    </section>
  </>
}
