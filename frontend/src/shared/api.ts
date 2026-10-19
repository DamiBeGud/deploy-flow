export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

const memory = new Map<string, string>()
export const storage = {
  get(key: string): string | null {
    try { return sessionStorage.getItem(`deployflow:v1:${key}`) } catch { return memory.get(key) ?? null }
  },
  set(key: string, value: string) {
    memory.set(key, value)
    try { sessionStorage.setItem(`deployflow:v1:${key}`, value) } catch { /* Storage can be disabled. */ }
  },
  remove(key: string) {
    memory.delete(key)
    try { sessionStorage.removeItem(`deployflow:v1:${key}`) } catch { /* Memory fallback. */ }
  },
}

let actorScope = ''
export function setActorScope(organization: string, actor: string) { actorScope = `${organization}:${actor}` }
let accessToken = storage.get('access_token')
export function setAccessToken(token: string | null) {
  accessToken = token
  if (token) storage.set('access_token', token)
  else storage.remove('access_token')
}

function reportExpiredSession(status: number) {
  if (status === 401 && accessToken) window.dispatchEvent(new Event('deployflow:session-expired'))
}

// Fetch-based SSE permits the same bearer token as the rest of the API.
// A streaming response must not use the normal 15-second request timeout.
export async function openEventStream(path: string, signal: AbortSignal): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, {
      signal,
      headers: { Accept: 'text/event-stream', ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) },
    })
  } catch (error) {
    if (signal.aborted) throw error
    throw new ApiError(0, 'Cannot reach the backend. Check Docker and retry.')
  }
  if (!response.ok) {
    reportExpiredSession(response.status)
    const problem = await response.json().catch(() => ({}))
    throw new ApiError(response.status, problem.detail || `Backend request failed (${response.status}).`)
  }
  if (!response.body) throw new ApiError(0, 'The browser cannot read the analysis event stream.')
  return response
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init, signal: init.signal ? AbortSignal.any([init.signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
      headers: { 'Content-Type': 'application/json', ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}), ...init.headers },
    })
  } catch (error) {
    if (init.signal?.aborted) throw error
    throw new ApiError(0, 'Cannot reach the backend. Check Docker and retry; your request can be retried safely.')
  }
  if (!response.ok) {
    reportExpiredSession(response.status)
    const problem = await response.json().catch(() => ({}))
    throw new ApiError(response.status, problem.detail || `Backend request failed (${response.status}).`)
  }
  return response.json() as Promise<T>
}

// Keep a command key until its successful response has reached this browser.
// Scope includes identity, action, target and body; reloads preserve uncertain requests.
export async function command<T>(path: string, body: object = {}, method = 'POST'): Promise<T> {
  const fingerprint = `command:${actorScope}:${method}:${path}:${JSON.stringify(body)}`
  const key = storage.get(fingerprint) ?? crypto.randomUUID()
  storage.set(fingerprint, key)
  const result = await request<T>(path, {
    method, body: JSON.stringify(body), headers: { 'Idempotency-Key': key },
  })
  storage.remove(fingerprint)
  return result
}

export function errorMessage(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please retry.' }
