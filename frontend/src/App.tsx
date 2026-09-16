import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { ArrowRight, Layers3, LogOut, Plus, ServerCog } from 'lucide-react'
import { errorMessage, request, setActorScope, storage } from './shared/api'
import type { Identity } from './shared/contracts'
import { Brand, ErrorNotice } from './shared/ui'
import { ApplicationsPage } from './features/applications/ApplicationsPage'
import { AnalysisPage } from './features/analysis/AnalysisPage'
import './App.css'

function Workspace({ identity, onLogout }: { identity: Identity; onLogout: () => void }) {
  const canWrite = identity.roles.some(role => ['developer', 'deployer', 'administrator'].includes(role))
  return <div className="app-shell"><aside className="sidebar"><Brand dark /><nav><NavLink to="/services"><Layers3 size={18} />Applications</NavLink>{canWrite && <NavLink to="/deploy"><Plus size={18} />Add application</NavLink>}</nav><div className="sidebar-footer"><div className="cluster-mini"><span><ServerCog size={15} /></span><div><b>Local backend</b><small>{identity.actor_id}</small></div></div><button onClick={onLogout} className="logout"><LogOut size={16} />Leave workspace</button></div></aside><main className="workspace"><Routes><Route path="/services" element={<ApplicationsPage canWrite={canWrite} />} /><Route path="/deploy" element={<AnalysisPage canWrite={canWrite} />} /><Route path="*" element={<Navigate to="/services" replace />} /></Routes></main></div>
}

function App() {
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [connectAttempt, setConnectAttempt] = useState(() => storage.get('workspace') ? 1 : 0)
  useEffect(() => {
    if (!connectAttempt) return
    const abort = new AbortController()
    // This effect starts a new external identity request, including session restoration.
    // eslint-disable-next-line react/set-state-in-effect
    setBusy(true); setError('')
    request<Identity>('/me', { signal: abort.signal }).then(user => {
      if (abort.signal.aborted) return
      setActorScope(user.organization_id, user.actor_id)
      storage.set('workspace', 'open')
      setIdentity(user)
    }).catch(e => { if (!abort.signal.aborted) setError(errorMessage(e)) })
      .finally(() => { if (!abort.signal.aborted) setBusy(false) })
    return () => abort.abort()
  }, [connectAttempt])
  return <BrowserRouter>{identity ? <Workspace identity={identity} onLogout={() => { storage.remove('workspace'); setIdentity(null); setConnectAttempt(0) }} /> : <main className="login-page"><section className="login-card"><Brand /><div className="login-heading"><h1>Welcome to DeployFlow</h1><p>Connect to your local development workspace.</p></div><ErrorNotice message={error} /><button className="button button-primary button-full" disabled={busy} onClick={() => setConnectAttempt(n => n + 1)}>{busy ? 'Connecting…' : 'Connect to local backend'}<ArrowRight size={16} /></button><p className="login-footnote">Uses the backend’s development identity. Email/password and GitHub sign-in are not enabled.</p></section></main>}</BrowserRouter>
}
export default App
