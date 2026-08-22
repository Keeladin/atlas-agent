import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AUTH_EXPIRED_EVENT } from './api/client'
import App from './App'
import { render } from '@testing-library/react'

describe('application authentication state', () => {
  it('returns to sign in when an established session expires', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ authenticated: true, csrf_token: 'token' }), {
        status: 200,
      }),
    ))
    render(<App />)
    expect(await screen.findByText('Owner session')).toBeInTheDocument()

    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })
})
