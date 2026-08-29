import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { ActionOccurrence, Capability, Decision, MCPServer, PolicyRule, Provider, SourceRoot } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { Panel } from '../ui/Panel'
import { Workspace } from '../ui/Workspace'

type SystemState = {
  version: string
  policy_revision: number
  providers: Provider[]
  mcp_servers: MCPServer[]
  source_roots: SourceRoot[]
  capabilities: Capability[]
  connections: Array<Record<string, unknown>>
  service_bindings: Array<Record<string, unknown>>
  pending_confirmations: ActionOccurrence[]
  host: Record<string, unknown>
}

export function Atlas() {
  const qc = useQueryClient()
  const system = useQuery({ queryKey: ['system'], queryFn: () => api<SystemState>('/api/system'), refetchInterval: 10000 })
  const policy = useQuery({ queryKey: ['policy'], queryFn: () => api<{ revision: number; rules: PolicyRule[] }>('/api/policy') })
  const refresh = async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['system'] }), qc.invalidateQueries({ queryKey: ['policy'] }), qc.invalidateQueries({ queryKey: ['pending-actions'] })]) }

  return <Workspace title="Atlas" subtitle="Runtime truth and control. Capability is infrastructure; authority is live NO / YES / CONFIRM policy.">
    <div className="stack">
      <RuntimeOverview state={system.data} />
      <Pending items={system.data?.pending_confirmations ?? []} onDone={refresh} />
      <PolicyPanel rules={policy.data?.rules ?? []} revision={policy.data?.revision ?? 0} onDone={refresh} />
      <Providers providers={system.data?.providers ?? []} onDone={refresh} />
      <Mcp servers={system.data?.mcp_servers ?? []} onDone={refresh} />
      <MailConnections connections={system.data?.connections ?? []} bindings={system.data?.service_bindings ?? []} servers={system.data?.mcp_servers ?? []} onDone={refresh} />
      <Roots roots={system.data?.source_roots ?? []} onDone={refresh} />
      <Capabilities items={system.data?.capabilities ?? []} />
    </div>
  </Workspace>
}

function RuntimeOverview({ state }: { state?: SystemState }) {
  return <Panel title="Runtime"><div className="grid-3"><div><div className="meta">Version</div><strong>{state?.version ?? '—'}</strong></div><div><div className="meta">Policy revision</div><strong>{state?.policy_revision ?? '—'}</strong></div><div><div className="meta">Capabilities</div><strong>{state?.capabilities.length ?? '—'}</strong></div></div><pre className="mono" style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(state?.host ?? {}, null, 2)}</pre></Panel>
}

function Pending({ items, onDone }: { items: ActionOccurrence[]; onDone: () => Promise<unknown> }) {
  if (!items.length) return null
  return <Panel title="Needs you" tone="decision-confirm"><div className="stack">{items.map(item => <ConfirmationCard key={item.occurrence_id} item={item} onDone={onDone} />)}</div></Panel>
}

function PolicyPanel({ rules, revision, onDone }: { rules: PolicyRule[]; revision: number; onDone: () => Promise<unknown> }) {
  const [scope, setScope] = useState('')
  const [operation, setOperation] = useState('')
  const [decision, setDecision] = useState<Decision>('CONFIRM')
  const save = useMutation({ mutationFn: (payload: { scope: string; operation: string; decision: Decision }) => api('/api/policy', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ scope, operation, decision }) }
  return <Panel title={`Control policy · revision ${revision}`}>
    <p className="meta">Changes apply to the running Atlas immediately. No service restart or MCP reconfiguration is involved.</p>
    <div className="stack">{rules.map(rule => <div className="list-row" key={`${rule.scope}:${rule.operation}`}><strong>{rule.scope}</strong><div className="meta">{rule.operation}</div><select value={rule.decision} onChange={e => save.mutate({ scope: rule.scope, operation: rule.operation, decision: e.target.value as Decision })}><option>NO</option><option>YES</option><option>CONFIRM</option></select></div>)}</div>
    <form className="grid-3" onSubmit={submit} style={{ marginTop: '1rem' }}><input value={scope} onChange={e => setScope(e.target.value)} placeholder="resource scope" /><input value={operation} onChange={e => setOperation(e.target.value)} placeholder="operation" /><div className="actions"><select value={decision} onChange={e => setDecision(e.target.value as Decision)}><option>NO</option><option>YES</option><option>CONFIRM</option></select><button className="primary" type="submit">Set</button></div></form>
  </Panel>
}

function Providers({ providers, onDone }: { providers: Provider[]; onDone: () => Promise<unknown> }) {
  const [key, setKey] = useState(''); const [kind, setKind] = useState('openai_compatible'); const [model, setModel] = useState(''); const [base, setBase] = useState(''); const [secret, setSecret] = useState(''); const [priority, setPriority] = useState(50)
  const save = useMutation({ mutationFn: (payload: object) => api('/api/providers', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: onDone })
  const verify = useMutation({ mutationFn: (providerKey: string) => api(`/api/providers/${providerKey}/verify`, { method: 'POST', body: '{}' }) })
  const remove = useMutation({ mutationFn: (providerKey: string) => api(`/api/providers/${providerKey}`, { method: 'DELETE' }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ key, kind, model, base_url: base || null, api_key: secret || undefined, enabled: true, local: kind === 'openai_compatible' && base.includes('127.0.0.1'), priority }) }
  return <Panel title="Models & providers"><div className="stack">{providers.map(item => <div className="list-row" key={item.key}><strong>{item.key} · {item.model}</strong><span className="chip">{item.enabled ? 'enabled' : 'disabled'}</span><div className="meta">{item.kind} · priority {item.priority} · {item.base_url || 'default endpoint'}</div><div className="actions"><button onClick={() => verify.mutate(item.key)}>Verify</button><button className="danger" onClick={() => remove.mutate(item.key)}>Remove</button></div></div>)}</div>
    <form className="grid-3" onSubmit={submit} style={{ marginTop: '1rem' }}><input value={key} onChange={e => setKey(e.target.value)} placeholder="provider key" /><select value={kind} onChange={e => setKind(e.target.value)}><option value="openai_compatible">OpenAI compatible</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="gemini">Gemini</option></select><input value={model} onChange={e => setModel(e.target.value)} placeholder="model" /><input value={base} onChange={e => setBase(e.target.value)} placeholder="base URL (optional)" /><input type="password" value={secret} onChange={e => setSecret(e.target.value)} placeholder="API key (optional)" /><input type="number" value={priority} onChange={e => setPriority(Number(e.target.value))} /><button className="primary" type="submit">Save provider</button></form>
    {verify.data ? <pre className="mono">{JSON.stringify(verify.data, null, 2)}</pre> : null}
  </Panel>
}

function Mcp({ servers, onDone }: { servers: MCPServer[]; onDone: () => Promise<unknown> }) {
  const [serverId, setServerId] = useState(''); const [name, setName] = useState(''); const [kind, setKind] = useState<'mcp' | 'n8n'>('mcp'); const [url, setUrl] = useState(''); const [token, setToken] = useState('')
  const save = useMutation({ mutationFn: (payload: object) => api('/api/mcp', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: onDone })
  const refresh = useMutation({ mutationFn: (id: string) => api(`/api/mcp/${id}/refresh`, { method: 'POST', body: '{}' }), onSuccess: onDone })
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/mcp/${id}`, { method: 'DELETE' }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ server_id: serverId, display_name: name || serverId, kind, url, token: token || undefined, enabled: true }) }
  return <Panel title="MCP & n8n"><p className="meta">Every tool advertised by an enabled server is registered. Discovery is capability inventory, never authority.</p><div className="stack">{servers.map(item => <div className="list-row" key={item.server_id}><strong>{item.display_name}</strong><span className="chip">{item.kind}</span><span className="chip">{item.discovered_tool_count} tools</span><div className="meta">{item.url}</div>{item.last_error ? <p className="offline-banner">{item.last_error}</p> : null}<div className="actions"><button onClick={() => refresh.mutate(item.server_id)}>Refresh tools</button><button className="danger" onClick={() => remove.mutate(item.server_id)}>Remove</button></div></div>)}</div>
    <form className="grid-3" onSubmit={submit} style={{ marginTop: '1rem' }}><input value={serverId} onChange={e => setServerId(e.target.value)} placeholder="server id" /><input value={name} onChange={e => setName(e.target.value)} placeholder="display name" /><select value={kind} onChange={e => setKind(e.target.value as 'mcp' | 'n8n')}><option value="mcp">MCP</option><option value="n8n">n8n</option></select><input value={url} onChange={e => setUrl(e.target.value)} placeholder="Streamable HTTP URL" /><input type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Bearer token (optional)" /><button className="primary" type="submit">Save server</button></form>
  </Panel>
}

function MailConnections({ connections, bindings, servers, onDone }: { connections: Array<Record<string, unknown>>; bindings: Array<Record<string, unknown>>; servers: MCPServer[]; onDone: () => Promise<unknown> }) {
  const [serverId, setServerId] = useState(''); const [connectionRef, setConnectionRef] = useState('')
  const attest = useMutation({ mutationFn: () => api('/api/mail/attest', { method: 'POST', body: JSON.stringify({ server_id: serverId, connection_ref: connectionRef }) }), onSuccess: onDone })
  return <Panel title="External accounts"><div className="grid-2"><div><h3>Connections</h3>{connections.map((item, i) => <div className="list-row" key={String(item.connection_id ?? i)}><strong>{String(item.display_name ?? item.canonical_address ?? item.connection_id)}</strong><div className="meta">{String(item.provider_id ?? '')} · {String(item.status ?? '')}</div></div>)}</div><div><h3>Technical service bindings</h3>{bindings.map((item, i) => <div className="list-row" key={String(item.binding_id ?? i)}><strong>{String(item.service ?? '')}</strong><div className="meta">{String(item.channel ?? '')}</div><div>{Array.isArray(item.attested_operations) ? item.attested_operations.join(', ') : ''}</div></div>)}</div></div>
    <div className="grid-3" style={{ marginTop: '1rem' }}><select value={serverId} onChange={e => setServerId(e.target.value)}><option value="">n8n server</option>{servers.filter(s => s.kind === 'n8n').map(s => <option key={s.server_id} value={s.server_id}>{s.display_name}</option>)}</select><input value={connectionRef} onChange={e => setConnectionRef(e.target.value)} placeholder="n8n connection_ref" /><button className="primary" onClick={() => attest.mutate()} disabled={!serverId || !connectionRef}>Attest mail account</button></div>
    {attest.isError ? <p className="offline-banner">{attest.error.message}</p> : null}
  </Panel>
}

function Roots({ roots, onDone }: { roots: SourceRoot[]; onDone: () => Promise<unknown> }) {
  const [id, setId] = useState(''); const [path, setPath] = useState(''); const [name, setName] = useState('')
  const save = useMutation({ mutationFn: () => api('/api/sources/roots', { method: 'POST', body: JSON.stringify({ root_id: id, host_path: path, display_name: name || id, enabled: true, quarantine_relative_path: '.atlas-quarantine' }) }), onSuccess: onDone })
  const remove = useMutation({ mutationFn: (rootId: string) => api(`/api/sources/roots/${rootId}`, { method: 'DELETE' }), onSuccess: onDone })
  return <Panel title="Filesystem roots"><p className="meta">Enrollment exposes the kernel capability. Read/write/delete authority is controlled only by the runtime policy above.</p><div className="stack">{roots.map(root => <div className="list-row" key={root.root_id}><strong>{root.display_name}</strong><span className="chip">{root.enabled ? 'enabled' : 'disabled'}</span><div className="meta">{root.host_path}</div><button className="danger" onClick={() => remove.mutate(root.root_id)}>Remove</button></div>)}</div><div className="grid-3" style={{ marginTop: '1rem' }}><input value={id} onChange={e => setId(e.target.value)} placeholder="root id" /><input value={name} onChange={e => setName(e.target.value)} placeholder="display name" /><input value={path} onChange={e => setPath(e.target.value)} placeholder="absolute host path" /><button className="primary" onClick={() => save.mutate()} disabled={!id || !path}>Enroll root</button></div></Panel>
}

function Capabilities({ items }: { items: Capability[] }) {
  const [filter, setFilter] = useState('')
  const shown = items.filter(item => !filter || `${item.id} ${item.description} ${item.source}`.toLowerCase().includes(filter.toLowerCase()))
  return <Panel title={`Capability inventory · ${items.length}`}><input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter capabilities" /><div className="stack" style={{ marginTop: '1rem' }}>{shown.map(item => <div className="list-row" key={item.id}><strong>{item.id}</strong><span className="chip">{item.source}</span><span className="chip">{item.effect_class}</span><span className="chip">{item.available ? 'available' : 'unavailable'}</span><div>{item.description}</div>{!item.available ? <div className="meta">{item.availability_reason}</div> : null}</div>)}</div></Panel>
}
