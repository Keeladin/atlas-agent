import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActionOccurrence, Capability, MCPServer } from '../api/types'
import { CapabilityBrowser } from './CapabilityBrowser'
import { capabilityCategory } from './capabilityPresentation'

const server: MCPServer = {
  server_id: 'n8n-runtime', display_name: 'n8n Runtime', kind: 'n8n', transport: 'streamable-http',
  url: 'http://127.0.0.1:5678/mcp-server/http', args: [], enabled: true, credential_configured: true,
  timeout_sec: 30, read_timeout_sec: 300, discovered_tool_count: 1,
}

function tool(overrides: Partial<Capability> = {}): Capability {
  return {
    id: 'mcp.n8n-runtime.search_workflows',
    description: 'Search workflows',
    operation: 'invoke',
    effect_class: 'none',
    input_schema: { type: 'object', properties: { query: { type: 'string', description: 'Workflow search text' } }, required: ['query'] },
    source: 'n8n', tags: ['mcp', 'n8n'], available: true, availability_reason: 'available',
    policy_decision: 'YES', policy_revision: 47, scope_hint: 'mcp/n8n-runtime/tool/search_workflows',
    metadata: { server_id: 'n8n-runtime', tool_name: 'search_workflows' },
    ...overrides,
  }
}

function action(): ActionOccurrence {
  return {
    occurrence_id: 'action-1', capability_id: 'mcp.n8n-runtime.search_workflows', operation: 'invoke',
    scope: 'mcp/n8n-runtime/tool/search_workflows', payload_sha256: 'abc', policy_decision: 'YES', policy_revision: 47,
    status: 'succeeded', result: { workflows: [] }, created_at: 'now', completed_at: 'now',
  }
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('capabilityCategory', () => {
  it('derives useful categories from discovered tool names without provider-specific UI code', () => {
    expect(capabilityCategory(tool())).toBe('Workflow')
    expect(capabilityCategory(tool({ id: 'mcp.google.gmail_users_messages_send', metadata: { server_id: 'google', tool_name: 'gmail_users_messages_send' } }))).toBe('Gmail')
  })
})

describe('CapabilityBrowser', () => {
  it('renders discovered input_schema and invokes the capability through the generic endpoint', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ action: action() }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    render(<QueryClientProvider client={client}><CapabilityBrowser items={[tool()]} servers={[server]} onDone={async () => undefined} /></QueryClientProvider>)

    expect(screen.getByText('n8n Runtime')).toBeInTheDocument()
    expect(screen.getByText('Workflow')).toBeInTheDocument()
    const run = screen.getByRole('button', { name: 'Run' })
    expect(run).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: 'Query' }), { target: { value: 'daily' } })
    expect(run).toBeEnabled()
    fireEvent.click(run)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0][0]).toBe('/api/capabilities/mcp.n8n-runtime.search_workflows/invoke')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({ input: { query: 'daily' } })
    expect(await screen.findByText('succeeded')).toBeInTheDocument()
  })

  it('reflects NO policy by disabling execution rather than inventing a second permission check', () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    render(<QueryClientProvider client={client}><CapabilityBrowser items={[tool({ policy_decision: 'NO' })]} servers={[server]} onDone={async () => undefined} /></QueryClientProvider>)
    fireEvent.change(screen.getByRole('textbox', { name: 'Query' }), { target: { value: 'daily' } })
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
    expect(screen.getByText('Principal policy blocks this capability domain.')).toBeInTheDocument()
  })

  it('does not treat a native scope hint as the final authorization decision', () => {
    const native = tool({
      id: 'host.filesystem.read', description: 'Read an exact host path', source: 'host', tags: ['host'],
      input_schema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] },
      scope_hint: 'host/filesystem', policy_decision: 'NO', metadata: {},
    })
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    render(<QueryClientProvider client={client}><CapabilityBrowser items={[native]} servers={[]} onDone={async () => undefined} /></QueryClientProvider>)
    fireEvent.change(screen.getByRole('textbox', { name: 'Path' }), { target: { value: '/tmp/example' } })
    expect(screen.getByRole('button', { name: 'Run' })).toBeEnabled()
    expect(screen.getByText('Policy at this scope hint is NO; the exact resource is resolved at invocation.')).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /Authority for host\.filesystem\.read/ })).not.toBeInTheDocument()
  })

})
