import type { JsonSchema } from '../api/types'

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function declaredType(schema: JsonSchema): string {
  if (Array.isArray(schema.type)) return schema.type.find(value => value !== 'null') ?? 'string'
  if (typeof schema.type === 'string') return schema.type
  if (schema.properties) return 'object'
  if (schema.items) return 'array'
  if (schema.enum?.length) {
    const value = schema.enum.find(item => item != null)
    return typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : 'string'
  }
  return 'string'
}

export function initialPayload(schema: JsonSchema): Record<string, unknown> {
  if (!isRecord(schema.properties)) return isRecord(schema.default) ? { ...schema.default } : {}
  const value: Record<string, unknown> = {}
  for (const [key, child] of Object.entries(schema.properties)) {
    if (child.default !== undefined) value[key] = child.default
    else if (declaredType(child) === 'object' && child.properties) {
      const nested = initialPayload(child)
      if (Object.keys(nested).length) value[key] = nested
    }
  }
  return value
}

export function requiredErrors(schema: JsonSchema, value: unknown, prefix = ''): string[] {
  if (declaredType(schema) !== 'object' || !schema.properties) return []
  const object = isRecord(value) ? value : {}
  const required = new Set(schema.required ?? [])
  const errors: string[] = []
  for (const [key, child] of Object.entries(schema.properties)) {
    const path = prefix ? `${prefix}.${key}` : key
    const current = object[key]
    if (required.has(key) && (current === undefined || current === null || current === '')) errors.push(path)
    if (current !== undefined) errors.push(...requiredErrors(child, current, path))
  }
  return errors
}
