import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ComponentProps } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Provider } from '../api/types'
import { AtlasPage, PolicyPanel, Providers, RuntimeOverview, WebProviders } from './Atlas'

type RuntimeState = NonNullable<ComponentProps<typeof RuntimeOverview>['state']>

const state: RuntimeState = {
  version: '3.5.0',
  policy_revision: 36,
  providers: [],
  web_providers: [],
  mcp_servers: [],
  source_roots: [],
  capabilities: [],
  connections: [],
  service_bindings: [],
  host: {
    status: { hostname: 'ubuntuserver', kernel: '7.0.0', pid: 1234, uid: 995, invocation_id: 'abcdef1234567890' },
    resources: { load_1: 1.25, load_5: 1.5, load_15: 1.75, cpu_count: 12, memory: { MemTotal: '32768000 kB', MemAvailable: '16384000 kB', SwapFree: '4096000 kB' } },
    storage: { filesystems: [{ path: '/', total: 1024 ** 4, free: 512 * 1024 ** 3 }] },
  },
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('RuntimeOverview', () => {
  it('shows operator-friendly runtime truth while keeping raw evidence collapsed', () => {
    render(<RuntimeOverview state={state} />)
    expect(screen.getByText('Process')).toBeInTheDocument()
    expect(screen.getByText('Resources')).toBeInTheDocument()
    expect(screen.getByText('Storage')).toBeInTheDocument()
    expect(screen.getByText('ubuntuserver')).toBeInTheDocument()
    expect(screen.getByText('16 GiB')).toBeInTheDocument()
    expect(screen.getByText('Raw host evidence')).toBeInTheDocument()
    expect(screen.getByText(/"hostname": "ubuntuserver"/)).not.toBeVisible()
  })
})

describe('Providers', () => {
  it('lets the operator save an explicit provider priority', async () => {
    const providers: Provider[] = [{ key: 'xai:expert', kind: 'openai_compatible', model: 'grok-test', base_url: 'https://api.x.ai', enabled: true, local: false, priority: 50, credential_configured: true, metadata: {}, updated_at: 'now' }]
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    render(<QueryClientProvider client={client}><Providers providers={providers} onDone={async () => undefined} /></QueryClientProvider>)
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Priority for xai:expert' }), { target: { value: '100' } })
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toMatchObject({ key: 'xai:expert', priority: 100 })
    vi.unstubAllGlobals()
  })
})

describe('WebProviders', () => {
  it('shows the stable web capability boundary without exposing credentials', () => {
    const providers = [{ key: 'web-primary', kind: 'brave' as const, enabled: true, priority: 100, credential_configured: true, metadata: {}, updated_at: 'now' }]
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(<QueryClientProvider client={client}><WebProviders providers={providers} onDone={async () => undefined} /></QueryClientProvider>)
    expect(screen.getByText('web.search')).toBeInTheDocument()
    expect(screen.getByText('web/search')).toBeInTheDocument()
    expect(screen.getByText(/Task-specific reasoning stays in Atlas/)).toBeInTheDocument()
    expect(screen.getByText(/never returned here/)).toBeInTheDocument()
  })

  it('presents a successful verification as readable status instead of raw JSON', async () => {
    const providers = [{ key: 'web-primary', kind: 'brave' as const, enabled: true, priority: 100, credential_configured: true, metadata: {}, updated_at: 'now' }]
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, provider: 'Atlas', kind: 'brave', result_count: 1 }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(<QueryClientProvider client={client}><WebProviders providers={providers} onDone={async () => undefined} /></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Verify web search' }))
    expect(await screen.findByText('Verification successful')).toBeInTheDocument()
    expect(screen.getByText('Results returned')).toBeInTheDocument()
    expect(screen.queryByText(/"result_count"/)).not.toBeInTheDocument()
  })
})


describe('PolicyPanel', () => {
  const server = { server_id: 'google-workspace', display_name: 'Google Workspace', kind: 'mcp', transport: 'stdio', url: null, command: '/usr/bin/python3', args: ['-m', 'atlas_providers.google_workspace_mcp'], cwd: '/srv/atlas-google', enabled: true, credential_configured: false, timeout_sec: 30, read_timeout_sec: 300, discovered_tool_count: 3 } as const
  const makeTool = (id: string, description: string, effect = 'external') => ({ id: `mcp.google-workspace.${id}`, description, operation: 'invoke', effect_class: effect, input_schema: {}, source: 'mcp', tags: ['mcp'], available: true, availability_reason: 'available', policy_decision: 'NO' as const, policy_revision: 36, scope_hint: `mcp/google-workspace/tool/${id}`, metadata: { server_id: 'google-workspace', tool_name: id } })
  const capabilities = [
    { id: 'host.service.restart', description: 'Restart an exact user-systemd service.', operation: 'restart', effect_class: 'external', input_schema: {}, source: 'host', tags: ['host'], available: true, availability_reason: 'available', policy_decision: 'YES' as const, policy_revision: 36, scope_hint: 'host/service', metadata: {} },
    makeTool('gmail_users_messages_send', 'gmail.users.messages.send: Sends a message.'),
    makeTool('gmail_users_messages_delete', 'gmail.users.messages.delete: Permanently deletes a message.', 'destructive'),
    makeTool('drive_files_list', 'drive.files.list: Lists files.', 'none'),
  ]
  const rules = [{ event_id: 'e1', sequence: 1, principal_id: 'owner', scope: 'host/service', operation: '*', decision: 'YES' as const, created_at: 'now' }]

  it('shows coarse principal domains instead of hundreds of per-tool policy controls', () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    render(<QueryClientProvider client={client}><PolicyPanel rules={rules} capabilities={capabilities} servers={[server]} revision={36} onDone={async () => undefined} /></QueryClientProvider>)
    expect(screen.getAllByText('Host services').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Google Workspace')).toBeInTheDocument()
    expect(screen.getByText(/One authority switch per service/)).toBeInTheDocument()
    expect(screen.getByText(/Tool inventory belongs in Capabilities/)).toBeInTheDocument()
    expect(screen.queryByText('gmail.users.messages.send: Sends a message.')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Google Workspace/ })).toHaveTextContent('NO')
  })

  it('grants a connected service once at its coarse parent domain', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ revision: 37 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    render(<QueryClientProvider client={client}><PolicyPanel rules={rules} capabilities={capabilities} servers={[server]} revision={36} onDone={async () => undefined} /></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Google Workspace/ }))
    fireEvent.click(screen.getByRole('button', { name: 'YES' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0][0]).toBe('/api/policy')
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({ scope: 'mcp/google-workspace', operation: '*', decision: 'YES' })
    vi.unstubAllGlobals()
  })
})

describe('Atlas local navigation', () => {
  it('walks back through Atlas pages and stops at the dashboard', () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: '/atlas/policies', state: { atlasBack: { path: '/atlas/connections', parent: { path: '/atlas' } } } }]}>
        <Routes>
          <Route path="/atlas/policies" element={<AtlasPage title="Policies" subtitle="policy"><div>Policy page</div></AtlasPage>} />
          <Route path="/atlas/connections" element={<AtlasPage title="Connections" subtitle="connections"><div>Connections page</div></AtlasPage>} />
          <Route path="/atlas" element={<div>Atlas dashboard</div>} />
        </Routes>
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: /back/i }))
    expect(screen.getByText('Connections page')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /back/i }))
    expect(screen.getByText('Atlas dashboard')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /back/i })).not.toBeInTheDocument()
  })
})
