import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Sources } from './Sources'

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><Sources /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Sources', () => {
  it('renders filesystem observations as browser rows', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      let body: object
      if (url.includes('/api/sources/roots')) body = { roots: [{ root_id: 'proof', provider_namespace: 'local', host_path: '/srv/proof', display_name: 'Proof files', enabled: true, updated_at: 'now' }] }
      else if (url.includes('/api/artifacts')) body = { artifacts: [] }
      else if (url.includes('/api/work')) body = { work: [] }
      else body = { action: { occurrence_id: 'a1', capability_id: 'files.list', operation: 'list', scope: 'files/local/proof', payload_sha256: 'x', policy_decision: 'YES', policy_revision: 1, status: 'succeeded', created_at: 'now', result: { observation: { source_ref: { root_id: 'proof', relative_path: '.', display_locator: 'Proof files' }, observed_at: 'now', object_type: 'directory' }, entries: [{ source_ref: { root_id: 'proof', relative_path: 'report.pdf', display_locator: 'report.pdf' }, observed_at: 'now', object_type: 'regular_file', byte_size: 2048, consistency: 'stable' }], next_cursor: null, entry_errors: [] } } }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    renderPage()
    const browse = await screen.findByRole('button', { name: 'Browse' })
    fireEvent.click(browse)
    expect((await screen.findAllByText('report.pdf')).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('regular file')).toBeInTheDocument()
    expect(screen.getByText('2.0 KiB')).toBeInTheDocument()
    fireEvent.click(screen.getAllByText('report.pdf')[0])
    expect(await screen.findByText('Technical evidence')).toBeInTheDocument()
  })
})
