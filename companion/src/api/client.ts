export type SessionInfo = {
  authenticated: boolean
  subject?: string
  csrf_token?: string
}

let csrfToken: string | null = null

export function setCsrfToken(token: string | null) {
  csrfToken = token
}

export function getCsrfToken() {
  return csrfToken
}

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, message: string, body: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers || {})
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json')
  }
  const method = (init.method || 'GET').toUpperCase()
  if (method !== 'GET' && method !== 'HEAD' && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: 'include',
  })
  const text = await response.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }
  if (!response.ok) {
    const message =
      typeof body === 'object' &&
      body &&
      'error' in body &&
      typeof (body as { error: unknown }).error === 'string'
        ? (body as { error: string }).error
        : `HTTP ${response.status}`
    throw new ApiError(response.status, message, body)
  }
  return body as T
}

export async function loadSession(): Promise<SessionInfo> {
  const session = await api<SessionInfo>('/api/auth/session')
  setCsrfToken(session.csrf_token || null)
  return session
}

export async function login(password: string): Promise<SessionInfo> {
  const session = await api<SessionInfo>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  })
  setCsrfToken(session.csrf_token || null)
  return session
}

export async function logout(): Promise<void> {
  await api('/api/auth/logout', { method: 'POST', body: '{}' })
  setCsrfToken(null)
}
