import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { ArrowRight, Layers3, LogOut, Plus, ServerCog } from 'lucide-react'
import { errorMessage, request, setAccessToken, setActorScope, storage } from './shared/api'
import { beginSignIn, finishSignIn } from './features/auth/oidc'
import type { AuthConfig } from './features/auth/oidc'
import type { Identity } from './shared/contracts'
import { Brand, ErrorNotice } from './shared/ui'
import { ApplicationsPage } from './features/applications/ApplicationsPage'
import { AnalysisPage } from './features/analysis/AnalysisPage'
import { ServicesPage } from './features/services/ServicesPage'
import './App.css'

function Workspace({ identity, onLogout }: { identity: Identity; onLogout: () => void }) {
  const canWrite = identity.roles.some(role => ['developer', 'deployer', 'administrator'].includes(role))
  const canDeploy = identity.roles.some(role => ['deployer', 'administrator'].includes(role)) && identity.authentication === 'oidc'
  return <div className="app-shell"><aside className="sidebar"><Brand dark /><nav><NavLink to="/services"><Layers3 size={18} />Applications</NavLink>{canWrite && <NavLink to="/deploy"><Plus size={18} />Add application</NavLink>}</nav><div className="sidebar-footer"><div className="cluster-mini"><span><ServerCog size={15} /></span><div><b>{identity.authentication === 'oidc' ? 'Authenticated backend' : 'Local backend'}</b><small>{identity.actor_id}</small></div></div><button onClick={onLogout} className="logout"><LogOut size={16} />Leave workspace</button></div></aside><main className="workspace"><Routes><Route path="/services" element={<ServicesPage><ApplicationsPage canWrite={canWrite} /></ServicesPage>} /><Route path="/deploy" element={<AnalysisPage canWrite={canWrite} canDeploy={canDeploy} />} /><Route path="*" element={<Navigate to="/services" replace />} /></Routes></main></div>
}

function App() {
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null)
  const [connectAttempt, setConnectAttempt] = useState(() => window.location.pathname !== '/auth/callback' && storage.get('workspace') ? 1 : 0)
  useEffect(() => {
    request<AuthConfig>('/auth/config').then(setAuthConfig).catch(reason => setError(errorMessage(reason)))
  }, [])
  useEffect(() => {
    if (window.location.pathname !== '/auth/callback' || authConfig?.mode !== 'oidc') return
    let active = true
    finishSignIn(authConfig).then(token => {
      if (!active) return
      setAccessToken(token)
      setConnectAttempt(value => value + 1)
    }).catch(reason => { if (active) setError(errorMessage(reason)) })
    return () => { active = false }
  }, [authConfig])
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
  return <BrowserRouter>{identity ? <Workspace identity={identity} onLogout={() => { setAccessToken(null); storage.remove('workspace'); setIdentity(null); setConnectAttempt(0) }} /> : <main className="login-page"><section className="login-card"><Brand /><div className="login-heading"><h1>Welcome to DeployFlow</h1><p>{authConfig?.mode === 'oidc' ? 'Sign in to review and deploy applications.' : 'Connect to your local development workspace.'}</p></div><ErrorNotice message={error} /><button className="button button-primary button-full" disabled={busy || !authConfig} onClick={() => { if (authConfig?.mode === 'oidc') void beginSignIn(authConfig).catch(reason => setError(errorMessage(reason))); else setConnectAttempt(n => n + 1) }}>{busy ? 'Connecting…' : authConfig?.mode === 'oidc' ? 'Sign in' : 'Connect to local backend'}<ArrowRight size={16} /></button>{authConfig?.mode === 'development' && <p className="login-footnote">Uses the backend’s development identity.</p>}</section></main>}</BrowserRouter>
}
export default App
