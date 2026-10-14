import { storage } from '../../shared/api'

export type AuthConfig = {
  mode: 'development' | 'oidc'
  issuer: string | null
  authorization_endpoint: string | null
  token_endpoint: string | null
  client_id: string | null
}

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function callbackUri(): string { return `${window.location.origin}/auth/callback` }

export async function beginSignIn(config: AuthConfig): Promise<void> {
  if (!config.authorization_endpoint || !config.client_id) throw new Error('OIDC sign-in is not configured.')
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const state = base64url(crypto.getRandomValues(new Uint8Array(24)))
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  storage.set('oidc_verifier', verifier)
  storage.set('oidc_state', state)
  storage.set('oidc_return', window.location.pathname === '/auth/callback' ? '/services' : window.location.pathname)
  const url = new URL(config.authorization_endpoint)
  url.searchParams.set('response_type', 'code')
  url.searchParams.set('client_id', config.client_id)
  url.searchParams.set('redirect_uri', callbackUri())
  url.searchParams.set('scope', 'openid profile')
  url.searchParams.set('state', state)
  url.searchParams.set('code_challenge_method', 'S256')
  url.searchParams.set('code_challenge', base64url(new Uint8Array(hash)))
  window.location.assign(url.toString())
}

export async function finishSignIn(config: AuthConfig): Promise<string> {
  const url = new URL(window.location.href)
  const code = url.searchParams.get('code')
  const state = url.searchParams.get('state')
  const verifier = storage.get('oidc_verifier')
  if (!code || !state || state !== storage.get('oidc_state') || !verifier || !config.token_endpoint || !config.client_id) {
    throw new Error('OIDC sign-in could not be verified. Start sign-in again.')
  }
  storage.remove('oidc_verifier')
  storage.remove('oidc_state')
  const response = await fetch(config.token_endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code', code, client_id: config.client_id,
      redirect_uri: callbackUri(), code_verifier: verifier,
    }),
  })
  if (!response.ok) throw new Error('OIDC token exchange failed. Check the provider configuration.')
  const result = await response.json() as { access_token?: string }
  if (!result.access_token) throw new Error('OIDC provider did not return an access token.')
  const returnPath = storage.get('oidc_return') || '/services'
  storage.remove('oidc_return')
  window.history.replaceState({}, '', returnPath.startsWith('/') && !returnPath.startsWith('//') ? returnPath : '/services')
  return result.access_token
}
