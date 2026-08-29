import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ComponentProps } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AtlasPage, PolicyPanel, RuntimeOverview } from './Atlas'
import { capabilityLensFor, policyLensFor } from './policyLens'

type RuntimeState = NonNullable<ComponentProps<typeof RuntimeOverview>['state']>

const state: RuntimeState = {
  version: '3.0.0',
  policy_revision: 36,
  providers: [],
  mcp_servers: [],
  source_roots: [],
  capabilities: [],
  connections: [],
  service_bindings: [],
  pending_confirmations: [],
  host: {
    status: { hostname: 'ubuntuserver', kernel: '7.0.0', pid: 1234, uid: 995, invocation_id: 'abcdef1234567890' },
    resources: { load_1: 1.25, load_5: 1.5, load_15: 1.75, cpu_count: 12, memory: { MemTotal: '32768000 kB', MemAvailable: '16384000 kB', SwapFree: '4096000 kB' } },
    storage: { filesystems: [{ path: '/', total: 1024 ** 4, free: 512 * 1024 ** 3 }] },
  },
}

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


describe('policyLensFor', () => {
  const servers = [
    { server_id: 'mail-n8n', kind: 'n8n' },
    { server_id: 'desktop', kind: 'mcp' },
  ] as Parameters<typeof policyLensFor>[1]

  it('separates native system policy from n8n and MCP tool policy', () => {
    expect(policyLensFor({ scope: 'host/service' }, servers)).toBe('system')
    expect(policyLensFor({ scope: 'mail/connection/google' }, servers)).toBe('system')
    expect(policyLensFor({ scope: 'mcp/mail-n8n/tool/mail_messages_search' }, servers)).toBe('n8n')
    expect(policyLensFor({ scope: 'mcp/desktop/tool/read_file' }, servers)).toBe('mcp')
    expect(policyLensFor({ scope: 'mcp/disconnected/tool/example' }, servers)).toBe('mcp')
  })
})


describe('PolicyPanel', () => {
  const server = { server_id: 'mail-n8n', display_name: 'Atlas n8n mail', kind: 'n8n', transport: 'streamable_http', url: 'http://n8n/mcp', enabled: true, credential_configured: true, timeout_sec: 30, read_timeout_sec: 30, discovered_tool_count: 4 } as const
  const makeTool = (id: string, description: string) => ({ id: `mcp.mail-n8n.${id}`, description, operation: 'invoke', effect_class: 'external', source: 'n8n', tags: ['mcp', 'n8n'], available: true, availability_reason: 'available', policy_decision: 'CONFIRM' as const, policy_revision: 36, scope_hint: `mcp/mail-n8n/tool/${id}`, metadata: { server_id: 'mail-n8n', tool_name: id } })
  const capabilities = [
    { id: 'host.service.restart', description: 'Restart an exact user-systemd service.', operation: 'restart', effect_class: 'external', source: 'host', tags: ['host'], available: true, availability_reason: 'available', policy_decision: 'CONFIRM' as const, policy_revision: 36, scope_hint: 'host/service', metadata: {} },
    makeTool('mail_connection_attest', 'Attest the n8n-held Gmail connection.'),
    makeTool('mail_inbox_count', 'Count unread mail for one n8n-held mail connection.'),
    makeTool('mail_messages_get', 'Read one bound mail message.'),
    makeTool('mail_messages_search', 'Search a bound n8n-held mail account.'),
  ]
  const rules = [{ event_id: 'e1', sequence: 1, principal_id: 'owner', scope: 'host/service', operation: 'restart', decision: 'CONFIRM' as const, created_at: 'now' }]

  it('presents descriptions first and exposes every discovered n8n tool as a policy control', () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    render(<QueryClientProvider client={client}><PolicyPanel rules={rules} capabilities={capabilities} servers={[server]} revision={36} onDone={async () => undefined} /></QueryClientProvider>)
    expect(screen.getByText('Restart an exact user-systemd service.')).toBeInTheDocument()
    expect(screen.getByText('host/service · restart')).toHaveClass('policy-syntax')
    fireEvent.click(screen.getByRole('tab', { name: /n8n Tools/i }))
    expect(screen.getByText('Atlas n8n mail')).toBeInTheDocument()
    expect(screen.getByText('4 tools')).toBeInTheDocument()
    expect(screen.getByText('Attest the n8n-held Gmail connection.')).toBeInTheDocument()
    expect(screen.getByText('Count unread mail for one n8n-held mail connection.')).toBeInTheDocument()
    expect(screen.getByText('Read one bound mail message.')).toBeInTheDocument()
    expect(screen.getByText('Search a bound n8n-held mail account.')).toBeInTheDocument()
  })
})

describe('capabilityLensFor', () => {
  it('puts discovered n8n tools under n8n Tools while semantic mail remains System', () => {
    const servers = [{ server_id: 'mail-n8n', kind: 'n8n' }] as Parameters<typeof capabilityLensFor>[1]
    const base = { description: '', operation: 'invoke', effect_class: 'external', source: 'n8n', tags: [], available: true, availability_reason: 'available', policy_decision: 'CONFIRM' as const, policy_revision: 1, metadata: {} }
    expect(capabilityLensFor({ ...base, id: 'mail.messages.search', scope_hint: 'mail' }, servers)).toBe('system')
    expect(capabilityLensFor({ ...base, id: 'mcp.mail-n8n.search', scope_hint: 'mcp/mail-n8n/tool/search', metadata: { server_id: 'mail-n8n' } }, servers)).toBe('n8n')
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
