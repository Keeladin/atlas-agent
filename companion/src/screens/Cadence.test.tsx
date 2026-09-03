import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CadenceList } from './Cadence'

const CADENCE = {
  cadence_id: 'cadence_1',
  name: 'Morning brief',
  objective: 'Prepare the morning brief',
  schedule: { kind: 'daily', hour: 8, minute: 0, timezone: 'Africa/Johannesburg' },
  steps: [{ capability_id: 'knowledge.search', input: { query: 'brief' } }],
  enabled: false,
  kind: 'work_template',
  next_run_at: '2026-09-04T06:00:00+00:00',
  last_run_at: null,
  last_work_id: null,
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><CadenceList /></MemoryRouter></QueryClientProvider>)
}

function stubFetch(handler?: (url: string, init?: RequestInit) => unknown) {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    const override = handler?.(url, init)
    const body = override !== undefined ? override
      : url.includes('/api/work') ? { work: [] }
      : { cadences: [CADENCE] }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  return calls
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Cadence', () => {
  it('sends authoring to Chat instead of exposing a step form', async () => {
    stubFetch()
    renderPage()
    expect(await screen.findByText('Morning brief')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Ask Atlas to add one' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New standing duty' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Repeat')).not.toBeInTheDocument()
  })

  it('runs a disabled standing duty on demand through the cadence.run_now capability', async () => {
    const calls = stubFetch((url) => url.includes('cadence.run_now')
      ? { action: { occurrence_id: 'occ_1', capability_id: 'cadence.run_now', status: 'succeeded', result: { work_id: 'work_1' } } }
      : undefined)
    renderPage()
    expect(await screen.findByText('Morning brief')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(calls.some(call => call.url.includes('/api/capabilities/cadence.run_now/invoke'))).toBe(true))
    expect(await screen.findByText('Ran now. The schedule is unchanged.')).toBeInTheDocument()
  })

  it('carries exact cadence identity when opening Chat', async () => {
    stubFetch()
    renderPage()
    expect(await screen.findByText('Morning brief')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'Open in Chat' })
    expect(link.getAttribute('href')).toContain('cadence_id=cadence_1')
  })

  it('reads run history from Work scoped to this cadence', async () => {
    const calls = stubFetch((url) => url.includes('/api/work')
      ? { work: [{ work_id: 'work_1', objective: 'Morning brief', status: 'completed', owner_principal_id: 'p', created_at: '2026-09-03T06:00:00+00:00', updated_at: '2026-09-03T06:01:00+00:00', metadata: {}, display_ref: 'AA-001' }] }
      : undefined)
    renderPage()
    expect(await screen.findByText('AA-001')).toBeInTheDocument()
    expect(calls.some(call => call.url.includes('/api/work?cadence_id=cadence_1'))).toBe(true)
  })
})
