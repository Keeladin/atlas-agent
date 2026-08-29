import type { MCPServer, PolicyRule } from '../api/types'

export type PolicyLens = 'system' | 'n8n' | 'mcp'

export function policyLensFor(
  rule: Pick<PolicyRule, 'scope'>,
  servers: MCPServer[],
): PolicyLens {
  if (!rule.scope.startsWith('mcp/')) return 'system'
  const server = servers.find((item) =>
    rule.scope.startsWith(`mcp/${item.server_id}/`),
  )
  return server?.kind === 'n8n' ? 'n8n' : 'mcp'
}
