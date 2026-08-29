import { useEffect, useState } from 'react'
import type { JsonSchema } from '../api/types'

import { declaredType, isRecord } from './schemaFormModel'

function labelFor(name: string, schema: JsonSchema) {
  return schema.title?.trim() || name.replace(/[_-]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

function JsonEditor({ value, onChange, label }: { value: unknown; onChange: (value: unknown) => void; label: string }) {
  const [draft, setDraft] = useState(() => JSON.stringify(value ?? {}, null, 2))
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { setDraft(JSON.stringify(value ?? {}, null, 2)); setError(null) }, [value])
  function update(text: string) {
    setDraft(text)
    try { onChange(JSON.parse(text)); setError(null) } catch { setError('Invalid JSON') }
  }
  return <div className="schema-json-editor"><textarea aria-label={label} value={draft} onChange={event => update(event.target.value)} />{error ? <div className="schema-error">{error}</div> : null}</div>
}

function PrimitiveArray({ schema, value, onChange, label }: { schema: JsonSchema; value: unknown; onChange: (value: unknown) => void; label: string }) {
  const itemType = declaredType(schema.items ?? {})
  const list = Array.isArray(value) ? value : []
  const text = list.map(item => String(item)).join('\n')
  function update(raw: string) {
    const rows = raw.split('\n').map(row => row.trim()).filter(Boolean)
    if (itemType === 'number' || itemType === 'integer') onChange(rows.map(Number).filter(Number.isFinite))
    else if (itemType === 'boolean') onChange(rows.map(row => row.toLowerCase() === 'true'))
    else onChange(rows)
  }
  return <textarea aria-label={label} value={text} onChange={event => update(event.target.value)} placeholder="One item per line" />
}

function SchemaField({ name, schema, value, required, onChange }: { name: string; schema: JsonSchema; value: unknown; required: boolean; onChange: (value: unknown) => void }) {
  const label = labelFor(name, schema)
  const type = declaredType(schema)
  const help = schema.description?.trim()
  const fieldLabel = <span>{label}{required ? <span className="schema-required"> *</span> : null}</span>

  if (schema.oneOf?.length || schema.anyOf?.length) return <label className="schema-field">{fieldLabel}<JsonEditor value={value} onChange={onChange} label={`${label} JSON`} />{help ? <small>{help}</small> : null}</label>
  if (schema.enum?.length) {
    return <label className="schema-field">{fieldLabel}<select aria-label={label} value={value === undefined ? '' : JSON.stringify(value)} onChange={event => onChange(event.target.value ? JSON.parse(event.target.value) : undefined)}><option value="">Select…</option>{schema.enum.map((item, index) => <option key={index} value={JSON.stringify(item)}>{String(item)}</option>)}</select>{help ? <small>{help}</small> : null}</label>
  }
  if (type === 'boolean') return <label className="schema-field schema-toggle"><input aria-label={label} type="checkbox" checked={Boolean(value)} onChange={event => onChange(event.target.checked)} /><span>{label}{required ? <span className="schema-required"> *</span> : null}</span>{help ? <small>{help}</small> : null}</label>
  if (type === 'number' || type === 'integer') return <label className="schema-field">{fieldLabel}<input aria-label={label} type="number" step={type === 'integer' ? 1 : 'any'} value={typeof value === 'number' ? value : ''} onChange={event => onChange(event.target.value === '' ? undefined : Number(event.target.value))} />{help ? <small>{help}</small> : null}</label>
  if (type === 'array') {
    const itemType = declaredType(schema.items ?? {})
    const primitive = ['string', 'number', 'integer', 'boolean'].includes(itemType) && !schema.items?.properties
    return <label className="schema-field">{fieldLabel}{primitive ? <PrimitiveArray schema={schema} value={value} onChange={onChange} label={label} /> : <JsonEditor value={value ?? []} onChange={onChange} label={`${label} JSON`} />}{help ? <small>{help}</small> : null}</label>
  }
  if (type === 'object') {
    if (!schema.properties) return <label className="schema-field">{fieldLabel}<JsonEditor value={value ?? {}} onChange={onChange} label={`${label} JSON`} />{help ? <small>{help}</small> : null}</label>
    const object = isRecord(value) ? value : {}
    const requiredChildren = new Set(schema.required ?? [])
    return <fieldset className="schema-object"><legend>{label}{required ? <span className="schema-required"> *</span> : null}</legend>{help ? <p className="meta">{help}</p> : null}<div className="schema-grid">{Object.entries(schema.properties).map(([key, child]) => <SchemaField key={key} name={key} schema={child} value={object[key]} required={requiredChildren.has(key)} onChange={next => { const updated = { ...object }; if (next === undefined) delete updated[key]; else updated[key] = next; onChange(updated) }} />)}</div></fieldset>
  }
  const inputType = schema.format === 'date' ? 'date' : schema.format === 'time' ? 'time' : schema.format === 'email' ? 'email' : schema.format === 'uri' || schema.format === 'url' ? 'url' : schema.format === 'password' ? 'password' : 'text'
  const placeholder = schema.format === 'date-time' ? 'RFC 3339 date-time' : undefined
  return <label className="schema-field">{fieldLabel}<input aria-label={label} type={inputType} placeholder={placeholder} value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value || undefined)} />{help ? <small>{help}</small> : null}</label>
}

export function SchemaForm({ schema, value, onChange }: { schema: JsonSchema; value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  const objectSchema = declaredType(schema) === 'object' && schema.properties && !schema.oneOf?.length && !schema.anyOf?.length
  if (!objectSchema) return <JsonEditor value={value} onChange={next => { if (isRecord(next)) onChange(next) }} label="Capability input JSON" />
  const required = new Set(schema.required ?? [])
  const entries = Object.entries(schema.properties ?? {})
  return <div className="schema-form">{entries.length ? <div className="schema-grid">{entries.map(([key, child]) => <SchemaField key={key} name={key} schema={child} value={value[key]} required={required.has(key)} onChange={next => { const updated = { ...value }; if (next === undefined) delete updated[key]; else updated[key] = next; onChange(updated) }} />)}</div> : <div className="empty schema-empty">No input required.</div>}<details className="inspect schema-advanced"><summary>Advanced JSON payload</summary><JsonEditor value={value} onChange={next => { if (isRecord(next)) onChange(next) }} label="Raw capability input JSON" /></details></div>
}
