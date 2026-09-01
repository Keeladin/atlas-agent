import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CadenceList } from './Cadence'

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><CadenceList /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Cadence', () => {
  it('uses schedule controls instead of raw cadence JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = url.includes('/api/capabilities')
        ? { capabilities: [{ id: 'knowledge.search', description: 'Search knowledge', operation: 'search', effect_class: 'none', source: 'knowledge', tags: [], available: true, availability_reason: 'available', policy_decision: 'YES', policy_revision: 1, metadata: {} }] }
        : { cadences: [] }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    renderPage()
    expect(await screen.findByText('No standing duties yet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'New standing duty' }))
    expect(screen.getByLabelText('Repeat')).toBeInTheDocument()
    expect(screen.getByLabelText('Timezone')).toBeInTheDocument()
    expect(screen.queryByText('Schedule JSON')).not.toBeInTheDocument()
    expect(screen.queryByText('Steps JSON')).not.toBeInTheDocument()
  })
})
