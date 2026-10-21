import { useEffect, useState } from 'react'
import { RefreshCw, ServerCog } from 'lucide-react'
import { errorMessage, request } from '../../shared/api'

type Workload = {
  id: string; kind: 'Deployment' | 'StatefulSet' | 'Job'; name: string
  desired_replicas: number | null; ready_replicas: number | null
  desired_completions: number | null; succeeded: number | null
  image: string | null; services: string[]; managed: boolean
  conditions: { type: string; status: string; reason?: string }[]
}
type ObservedService = { name: string; address: string | null }
type Observation = { cluster_id: string; namespace: string; observed_at: string; workloads: Workload[]; services: ObservedService[] }

export function ServicesPage({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<Observation | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const abort = new AbortController()
    let timer = 0
    let failures = 0
    const load = async () => {
      if (document.hidden) { timer = window.setTimeout(() => void load(), 30000); return }
      try {
        const result = await request<Observation>('/services?cluster_id=local-k3s&namespace=deployflow-test', { signal: abort.signal })
        if (!abort.signal.aborted) { setData(result); setError(''); failures = 0 }
      } catch (reason) { if (!abort.signal.aborted) { setError(errorMessage(reason)); failures++ } }
      finally {
        if (!abort.signal.aborted) {
          setLoading(false)
          timer = window.setTimeout(() => void load(), Math.min(15000 * 2 ** failures, 60000))
        }
      }
    }
    void load()
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [revision])

  return <>
    <section className="services-table-wrap cluster-observations" aria-label="Kubernetes observations">
      <div className="table-heading"><div><h2><ServerCog size={16} /> Kubernetes workloads</h2><p>local-k3s · deployflow-test · read-only cluster observations</p></div><button className="button button-secondary" disabled={loading} onClick={() => { setLoading(true); setRevision(value => value + 1) }}><RefreshCw size={14} />Refresh</button></div>
      {loading && !data ? <p className="empty-state" role="status">Loading cluster workloads…</p> : error ? <p className="empty-state" role="status">Cluster observations unavailable: {error}. Registered applications remain available below.</p> : data?.workloads.length === 0 ? <p className="empty-state">No workloads are running in deployflow-test yet. This does not mean registered applications are deployed.</p> : <table><thead><tr><th>Workload</th><th>Progress</th><th>Service / address</th><th>Image</th><th>Source</th></tr></thead><tbody>{data?.workloads.map(workload => <tr key={workload.id}><td><div className="service-name"><b>{workload.name}</b><small>{workload.kind}</small></div></td><td className="replicas"><div>{workload.kind === 'Job' ? `${workload.succeeded ?? 0}/${workload.desired_completions ?? 1} completed` : `${workload.ready_replicas ?? 'unknown'}/${workload.desired_replicas ?? 'unknown'} ready`}</div>{workload.conditions.length > 0 && <small>{workload.conditions.map(condition => `${condition.type}: ${condition.status}${condition.reason ? ` (${condition.reason})` : ''}`).join(' · ')}</small>}</td><td>{workload.services.length ? workload.services.map(name => { const address = data.services.find(service => service.name === name)?.address; return <div key={name}>{name}{address ? <small> · {address}</small> : null}</div> }) : '—'}</td><td className="image-name" title={workload.image || ''}>{workload.image || '—'}</td><td>{workload.managed ? 'DeployFlow' : 'Observed / unmanaged'}</td></tr>)}</tbody></table>}
      {data && <p className="cluster-observed-at">Observed {new Date(data.observed_at).toLocaleString()}{loading ? ' · refreshing…' : ''}</p>}
    </section>
    {children}
  </>
}
