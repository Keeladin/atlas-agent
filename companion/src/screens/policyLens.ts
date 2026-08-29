import type { Capability, MCPServer, PolicyRule } from '../api/types'

export type PolicyLens = 'system' | 'n8n' | 'mcp'

function serverKind(serverId: string | undefined, servers: MCPServer[]) {
  return servers.find(server => server.server_id === serverId)?.kind
}

export function policyLensFor(rule: Pick<PolicyRule, 'scope'>, servers: MCPServer[]): PolicyLens {
  if (!rule.scope.startsWith('mcp/')) return 'system'
  const serverId = rule.scope.split('/')[1]
  return serverKind(serverId, servers) === 'n8n' ? 'n8n' : 'mcp'
}

export function capabilityLensFor(capability: Capability, servers: MCPServer[]): PolicyLens {
  const serverId = typeof capability.metadata?.server_id === 'string' ? capability.metadata.server_id : undefined
  if (!capability.scope_hint?.startsWith('mcp/') && !serverId) return 'system'
  return serverKind(serverId, servers) === 'n8n' ? 'n8n' : 'mcp'
}