import { useEffect, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ActionOccurrence, Capability, Decision, MCPServer } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { Panel } from '../ui/Panel'
import { SchemaForm } from '../ui/SchemaForm'
import { initialPayload, requiredErrors } from '../ui/schemaFormModel'
import { capabilityCategory, hasExactToolPolicyScope, stringMetadata, titleCase } from './capabilityPresentation'

type Group = {
  key: string
  name: string
  kind: string
  items: Capability[]
  categories: Array<{ name: string; count: number }>
}

function groupCapabilities(items: Capability[], servers: MCPServer[]): Group[] {
  const serverById = new Map(servers.map(server => [server.server_id, server]))
  const buckets = new Map<string, { name: string; kind: string; items: Capability[] }>()
  for (const item of items) {
    const serverId = stringMetadata(item, 'server_id')
    const server = serverId ? serverById.get(serverId) : undefined
    const key = server ? `server:${server.server_id}` : 'native'
    const name = server?.display_name ?? 'Atlas Native'
    const kind = server?.kind ?? 'native'
    const bucket = buckets.get(key) ?? { name, kind, items: [] }
    bucket.items.push(item)
    buckets.set(key, bucket)
  }
  return [...buckets.entries()].map(([key, bucket]) => {
    const counts = new Map<string, number>()
    for (const item of bucket.items) {
      const category = serverById.has(stringMetadata(item, 'server_id') ?? '') ? capabilityCategory(item) : titleCase(item.source || item.id.split(/[._]/)[0] || 'General')
      counts.set(category, (counts.get(category) ?? 0) + 1)
    }
    return { key, ...bucket, categories: [...counts.entries()].map(([name, count]) => ({ name, count })).sort((a, b) => a.name.localeCompare(b.name)) }
  }).sort((a, b) => a.key === 'native' ? 1 : b.key === 'native' ? -1 : a.name.localeCompare(b.name))
}

function categoryForItem(item: Capability, servers: MCPServer[]) {
  const serverId = stringMetadata(item, 'server_id')
  return servers.some(server => server.server_id === serverId) ? capabilityCategory(item) : titleCase(item.source || item.id.split(/[._]/)[0] || 'General')
}

function CapabilityResult({ action, onDone, onResolved }: { action: ActionOccurrence; onDone: () => Promise<unknown>; onResolved: (action: ActionOccurrence) => void }) {
  if (action.status === 'pending_confirmation') return <ConfirmationCard item={action} onDone={onDone} onResolved={onResolved} />
  return <Panel title="Latest action" className="capability-result"><div className="capability-result-head"><span className={`chip ${action.status === 'succeeded' ? 'done' : action.status === 'failed' || action.status === 'blocked' ? 'failed' : ''}`}>{action.status}</span><span className="mono">{action.occurrence_id}</span></div>{action.summary ? <p>{action.summary}</p> : null}{action.error ? <p className="offline-banner">{action.error}</p> : null}{action.result !== undefined ? <details className="inspect"><summary>Result</summary><pre>{JSON.stringify(action.result, null, 2)}</pre></details> : null}{action.receipt ? <details className="inspect"><summary>Receipt</summary><pre>{JSON.stringify(action.receipt, null, 2)}</pre></details> : null}</Panel>
}

function CapabilityDetail({ item, onDone }: { item: Capability; onDone: () => Promise<unknown> }) {
  const [payload, setPayload] = useState<Record<string, unknown>>(() => initialPayload(item.input_schema ?? {}))
  const [action, setAction] = useState<ActionOccurrence | null>(null)
  const schemaKey = JSON.stringify(item.input_schema ?? {})
  useEffect(() => { setPayload(initialPayload(JSON.parse(schemaKey))); setAction(null) }, [item.id, schemaKey])
  const missing = requiredErrors(item.input_schema ?? {}, payload)
  const exactPolicyScope = hasExactToolPolicyScope(item)
  const invoke = useMutation({
    mutationFn: () => api<{ action: ActionOccurrence }>(`/api/capabilities/${encodeURIComponent(item.id)}/invoke`, { method: 'POST', body: JSON.stringify({ input: payload }) }),
    onSuccess: async result => { setAction(result.action); await onDone() },
  })
  const policy = useMutation({
    mutationFn: (decision: Decision) => api('/api/policy', { method: 'POST', body: JSON.stringify({ scope: item.scope_hint, operation: item.operation, decision }) }),
    onSuccess: onDone,
  })
  const runDisabled = !item.available || (exactPolicyScope && item.policy_decision === 'NO') || missing.length > 0 || invoke.isPending
  return <div className="capability-detail-stack">
    <Panel className="capability-detail">
      <div className="capability-detail-heading"><div><span className="eyebrow">{item.source}</span><h2>{item.description}</h2><div className="policy-syntax">{item.id}</div></div><div className="capability-statuses"><span className={`chip ${item.available ? 'done' : 'failed'}`}>{item.available ? 'available' : 'unavailable'}</span><span className="chip">{item.effect_class}</span></div></div>
      {!item.available ? <p className="offline-banner">{item.availability_reason}</p> : null}
      <div className="capability-control-strip"><div><span className="meta">Authority</span>{exactPolicyScope && item.scope_hint ? <select aria-label={`Authority for ${item.id}`} value={item.policy_decision} disabled={policy.isPending} onChange={event => policy.mutate(event.target.value as Decision)}><option>NO</option><option>YES</option><option>CONFIRM</option></select> : <strong>{item.policy_decision}</strong>}</div><div><span className="meta">Operation</span><strong>{item.operation}</strong></div><div><span className="meta">{exactPolicyScope ? 'Policy scope' : 'Scope hint'}</span><strong className="mono">{item.scope_hint ?? 'resolved at invocation'}</strong></div></div>
      <div className="capability-input"><div className="capability-section-head"><h3>Input</h3>{missing.length ? <span className="schema-error">Required: {missing.join(', ')}</span> : null}</div><SchemaForm schema={item.input_schema ?? {}} value={payload} onChange={setPayload} /></div>
      {policy.isError ? <p className="offline-banner">{policy.error.message}</p> : null}
      {invoke.isError ? <p className="offline-banner">{invoke.error.message}</p> : null}
      <div className="capability-run-row"><div className="meta">{exactPolicyScope && item.policy_decision === 'NO' ? 'Exact runtime policy blocks this capability.' : !exactPolicyScope && item.policy_decision === 'NO' ? 'Policy at this scope hint is NO; Atlas resolves the normalized resource at invocation.' : item.policy_decision === 'CONFIRM' ? 'Run creates an exact durable confirmation when the resolved action requires it.' : 'Runtime policy currently allows direct execution at this scope.'}</div><button className="primary" type="button" disabled={runDisabled} onClick={() => invoke.mutate()}>{invoke.isPending ? 'Submitting…' : 'Run'}</button></div>
    </Panel>
    {action ? <CapabilityResult action={action} onDone={onDone} onResolved={setAction} /> : null}
  </div>
}

export function CapabilityBrowser({ items, servers, onDone }: { items: Capability[]; servers: MCPServer[]; onDone: () => Promise<unknown> }) {
  const groups = useMemo(() => groupCapabilities(items, servers), [items, servers])
  const [groupKey, setGroupKey] = useState('all')
  const [category, setCategory] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(items[0]?.id ?? null)
  const visible = useMemo(() => items.filter(item => {
    if (groupKey !== 'all') {
      const group = groups.find(candidate => candidate.key === groupKey)
      if (!group?.items.some(candidate => candidate.id === item.id)) return false
      if (category && categoryForItem(item, servers) !== category) return false
    }
    if (filter && !`${item.id} ${item.description} ${item.source} ${String(item.metadata?.tool_name ?? '')}`.toLowerCase().includes(filter.toLowerCase())) return false
    return true
  }), [items, groups, groupKey, category, filter, servers])
  useEffect(() => {
    if (!visible.some(item => item.id === selectedId)) setSelectedId(visible[0]?.id ?? null)
  }, [visible, selectedId])
  const selected = items.find(item => item.id === selectedId) ?? null

  function chooseGroup(key: string) { setGroupKey(key); setCategory(null) }
  return <Panel title={`Capability browser · ${items.length}`} className="capability-browser-panel">
    <div className="capability-browser">
      <nav className="capability-tree" aria-label="Capability groups">
        <button className={groupKey === 'all' ? 'capability-tree-root active' : 'capability-tree-root'} type="button" onClick={() => chooseGroup('all')}><span>All capabilities</span><strong>{items.length}</strong></button>
        {groups.map(group => <div className="capability-tree-group" key={group.key}><button className={groupKey === group.key && !category ? 'capability-tree-root active' : 'capability-tree-root'} type="button" onClick={() => chooseGroup(group.key)}><span><small>{group.kind}</small>{group.name}</span><strong>{group.items.length}</strong></button><div className="capability-tree-children">{group.categories.map(item => <button key={item.name} className={groupKey === group.key && category === item.name ? 'active' : ''} type="button" onClick={() => { setGroupKey(group.key); setCategory(item.name) }}><span>{item.name}</span><strong>{item.count}</strong></button>)}</div></div>)}
      </nav>
      <section className="capability-list-pane">
        <input aria-label="Filter capabilities" value={filter} onChange={event => setFilter(event.target.value)} placeholder="Filter capabilities" />
        <div className="capability-list-count">{visible.length} shown</div>
        <div className="capability-list">{visible.map(item => <button key={item.id} type="button" className={selectedId === item.id ? 'capability-list-item active' : 'capability-list-item'} onClick={() => setSelectedId(item.id)}><span><strong>{stringMetadata(item, 'tool_name') ?? item.id}</strong><small>{item.description}</small></span><span className={`capability-dot ${item.available ? 'available' : 'unavailable'}`} aria-label={item.available ? 'available' : 'unavailable'} /></button>)}{!visible.length ? <div className="empty">No matching capabilities.</div> : null}</div>
      </section>
      <section className="capability-detail-pane">{selected ? <CapabilityDetail item={selected} onDone={onDone} /> : <div className="empty">Select a capability to inspect or run it.</div>}</section>
    </div>
  </Panel>
}
