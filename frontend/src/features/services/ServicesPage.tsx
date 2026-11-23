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
    {children}
    <section className="services-table-wrap cluster-observations" aria-label="Kubernetes observations" tabIndex={0}>
      <div className="table-heading"><div><h2><ServerCog size={16} /> Kubernetes workloads</h2><p>deployflow-test</p></div><button className="button button-secondary" disabled={loading} onClick={() => { setLoading(true); setRevision(value => value + 1) }}><RefreshCw size={14} />Refresh workloads</button></div>
      {loading && !data ? <p className="empty-state" role="status">Loading cluster workloads…</p> : error ? <p className="empty-state" role="status">Current workloads are unavailable. {error}</p> : data?.workloads.length === 0 ? <p className="empty-state">No workloads observed in deployflow-test.</p> : <table><thead><tr><th>Workload</th><th>Progress</th><th>Service / address</th><th>Image</th></tr></thead><tbody>{data?.workloads.map(workload => <tr key={workload.id}><td><div className="service-name"><b>{workload.name}</b><small>{workload.kind} · {workload.managed ? 'DeployFlow' : 'Unmanaged'}</small></div></td><td className="replicas"><div>{workload.kind === 'Job' ? `${workload.succeeded ?? 0}/${workload.desired_completions ?? 1} completed` : workload.desired_replicas === 0 ? 'Scaled to zero' : `${workload.ready_replicas ?? 'unknown'}/${workload.desired_replicas ?? 'unknown'} ready`}</div>{workload.conditions.length > 0 && <details className="workflow-details"><summary>Conditions</summary><small>{workload.conditions.map(condition => `${condition.type}: ${condition.status}${condition.reason ? ` (${condition.reason})` : ''}`).join(' · ')}</small></details>}</td><td>{workload.services.length ? workload.services.map(name => { const address = data.services.find(service => service.name === name)?.address; return <div key={name}>{name}{address ? <small> · {address}</small> : null}</div> }) : '—'}</td><td>{workload.image ? <details className="workflow-details"><summary>Image reference</summary><code>{workload.image}</code></details> : '—'}</td></tr>)}</tbody></table>}
      {data && <p className="cluster-observed-at">Observed {new Date(data.observed_at).toLocaleString()}{loading ? ' · refreshing…' : ''}</p>}
    </section>
  </>
}
