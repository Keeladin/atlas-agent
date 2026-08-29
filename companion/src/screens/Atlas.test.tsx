import { render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { describe, expect, it } from 'vitest'
import { RuntimeOverview } from './Atlas'
import { policyLensFor } from './policyLens'

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
