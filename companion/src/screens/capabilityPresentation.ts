import type { Capability } from '../api/types'

const ACTION_WORDS = new Set(['add', 'archive', 'call', 'create', 'delete', 'describe', 'disable', 'discover', 'enable', 'execute', 'fetch', 'find', 'get', 'insert', 'invoke', 'list', 'load', 'move', 'publish', 'read', 'remove', 'rename', 'restore', 'run', 'search', 'send', 'set', 'start', 'stop', 'test', 'unpublish', 'update', 'write'])

export function titleCase(value: string) {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

function categoryToken(value: string) {
  const lower = value.toLowerCase()
  if (lower.endsWith('ies') && lower.length > 4) return `${lower.slice(0, -3)}y`
  if (lower.endsWith('s') && lower.length > 3 && !/(ss|us|is|status)$/.test(lower)) return lower.slice(0, -1)
  return lower
}

export function stringMetadata(item: Capability, key: string) {
  const value = item.metadata?.[key]
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function capabilityCategory(item: Capability): string {
  const explicit = stringMetadata(item, 'category') ?? stringMetadata(item, 'service')
  if (explicit) return titleCase(explicit)
  const toolName = stringMetadata(item, 'tool_name') ?? item.id
  const tokens = toolName.split(/[._:/-]+/).map(token => token.trim()).filter(Boolean)
  if (!tokens.length) return 'General'
  const first = tokens[0].toLowerCase()
  if (!ACTION_WORDS.has(first)) return titleCase(categoryToken(tokens[0]))
  const semantic = tokens.find(token => !ACTION_WORDS.has(token.toLowerCase()))
  return titleCase(categoryToken(semantic ?? 'General'))
}

export function hasExactToolPolicyScope(item: Capability) {
  return Boolean(item.scope_hint?.startsWith('mcp/') && item.scope_hint.includes('/tool/'))
}
