import { describe, expect, it, vi } from 'vitest'
import { AUTH_EXPIRED_EVENT, ApiError, api, getCsrfToken, setCsrfToken } from './client'

describe('api authentication expiry', () => {
  it('clears CSRF state and announces a non-session 401', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: 'Session expired' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))
    const expired = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, expired)
    setCsrfToken('secret')

    await expect(api('/api/work')).rejects.toEqual(
      expect.objectContaining({ status: 401, message: 'Session expired' }),
    )
    expect(getCsrfToken()).toBeNull()
    expect(expired).toHaveBeenCalledOnce()
  })

  it('does not announce the expected unauthenticated session probe', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 401 })))
    const expired = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, expired, { once: true })

    await expect(api('/api/auth/session')).rejects.toBeInstanceOf(ApiError)
    expect(expired).not.toHaveBeenCalled()
  })
})
