import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, Capability, Decision, MCPServer, PolicyRule, Provider, SourceRoot } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { Panel } from '../ui/Panel'
import { Workspace } from '../ui/Workspace'
import { CapabilityBrowser } from './CapabilityBrowser'
import { capabilityLensFor, policyLensFor, type PolicyLens } from './policyLens'

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
  host: HostState
}

type HostStatus = {
  hostname?: string
  kernel?: string
  pid?: number
  uid?: number
  invocation_id?: string | null
  timestamp?: string
}

type HostResources = {
  load_1?: number
  load_5?: number
  load_15?: number
  cpu_count?: number | null
  memory?: Record<string, string | null | undefined>
  timestamp?: string
}

type HostFilesystem = { path?: string; total?: number; used?: number; free?: number }
type HostStorage = { filesystems?: HostFilesystem[]; timestamp?: string }
type HostState = { status?: HostStatus; resources?: HostResources; storage?: HostStorage }
type AtlasBack = { path: string; parent?: AtlasBack }

function useAtlasControl() {
  const qc = useQueryClient()
  const system = useQuery({ queryKey: ['system'], queryFn: () => api<SystemState>('/api/system'), refetchInterval: 10000 })
  const policy = useQuery({ queryKey: ['policy'], queryFn: () => api<{ revision: number; rules: PolicyRule[] }>('/api/policy') })
  const refresh = async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['system'] }), qc.invalidateQueries({ queryKey: ['policy'] }), qc.invalidateQueries({ queryKey: ['pending-actions'] })]) }
  return { system, policy, refresh }
}

export function AtlasPage({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const back = (location.state as { atlasBack?: AtlasBack } | null)?.atlasBack
  const goBack = () => back ? navigate(back.path, { state: back.parent ? { atlasBack: back.parent } : null }) : navigate('/atlas')
  return <Workspace className="atlas-control-workspace" title={title} subtitle={subtitle} crumb={<button type="button" className="atlas-back" onClick={goBack}><span aria-hidden>←</span> Back</button>}><div className="stack">{children}</div></Workspace>
}

export function Atlas() {
  const { system } = useAtlasControl()
  const state = system.data
  const enabledProviders = state?.providers.filter(provider => provider.enabled).length ?? 0
  const availableCapabilities = state?.capabilities.filter(capability => capability.available).length ?? 0
  const pending = state?.pending_confirmations.length ?? 0
  const cards = [
    { to: '/atlas/runtime', eyebrow: 'Observe', title: 'Runtime', description: 'Process, resources, storage and pending confirmations.', metric: state ? `${state.version} · ${pending} pending` : 'Loading…' },
    { to: '/atlas/policies', eyebrow: 'Control', title: 'Policies', description: 'Owner authority for System, n8n tools and MCP tools.', metric: state ? `revision ${state.policy_revision}` : 'Loading…' },
    { to: '/atlas/models', eyebrow: 'Configure', title: 'Models & Providers', description: 'Model endpoints, credentials, priority and verification.', metric: state ? `${enabledProviders}/${state.providers.length} enabled` : 'Loading…' },
    { to: '/atlas/connections', eyebrow: 'Connect', title: 'Connections', description: 'MCP/n8n servers, external identities and technical bindings.', metric: state ? `${state.mcp_servers.length} services · ${state.connections.length} accounts` : 'Loading…' },
    { to: '/atlas/filesystem', eyebrow: 'Configure', title: 'Filesystem', description: 'Enrolled roots and deterministic source boundaries.', metric: state ? `${state.source_roots.length} roots` : 'Loading…' },
    { to: '/atlas/capabilities', eyebrow: 'Inspect', title: 'Capabilities', description: 'The complete live toolbox Atlas can currently see.', metric: state ? `${availableCapabilities}/${state.capabilities.length} available` : 'Loading…' },
  ]
  return <Workspace title="Atlas" subtitle="Runtime truth and control. Choose a control surface instead of scrolling through one long settings document.">
    <div className="atlas-dashboard-grid">{cards.map(card => <Link className="atlas-dashboard-card" to={card.to} state={{ atlasBack: { path: '/atlas' } }} key={card.to}><span className="eyebrow">{card.eyebrow}</span><div className="atlas-dashboard-card-head"><h2>{card.title}</h2><span aria-hidden>→</span></div><p>{card.description}</p><strong>{card.metric}</strong></Link>)}</div>
  </Workspace>
}

export function AtlasRuntime() {
  const { system, refresh } = useAtlasControl()
  return <AtlasPage title="Runtime" subtitle="Live process and host truth. This page observes Atlas; it does not grant authority."><RuntimeOverview state={system.data} /><Pending items={system.data?.pending_confirmations ?? []} onDone={refresh} /></AtlasPage>
}

export function AtlasPolicies() {
  const { system, policy, refresh } = useAtlasControl()
  return <AtlasPage title="Policies" subtitle="The live owner authority surface. Description first; exact scope and operation remain visible as evidence."><PolicyPanel rules={policy.data?.rules ?? []} capabilities={system.data?.capabilities ?? []} servers={system.data?.mcp_servers ?? []} revision={policy.data?.revision ?? 0} onDone={refresh} /></AtlasPage>
}

export function AtlasModels() {
  const { system, refresh } = useAtlasControl()
  return <AtlasPage title="Models & Providers" subtitle="Technical model capability: endpoints, credentials, ordering and verification."><Providers providers={system.data?.providers ?? []} onDone={refresh} /></AtlasPage>
}

export function AtlasIntegrations() {
  const { system, refresh } = useAtlasControl()
  return <AtlasPage title="Connections" subtitle="Technical connections and account custody. Tool authority is managed under Policies."><Mcp servers={system.data?.mcp_servers ?? []} onDone={refresh} /><ExternalAccounts connections={system.data?.connections ?? []} bindings={system.data?.service_bindings ?? []} /></AtlasPage>
}

export function AtlasFilesystem() {
  const { system, refresh } = useAtlasControl()
  return <AtlasPage title="Filesystem" subtitle="Enroll deterministic roots here; read/write/delete authority remains a runtime policy decision."><Roots roots={system.data?.source_roots ?? []} onDone={refresh} /></AtlasPage>
}

export function AtlasCapabilities() {
  const { system, refresh } = useAtlasControl()
  return <AtlasPage title="Capabilities" subtitle="Live capability discovery drives navigation, input controls, authority and execution."><CapabilityBrowser items={system.data?.capabilities ?? []} servers={system.data?.mcp_servers ?? []} onDone={refresh} /></AtlasPage>
}

function formatBytes(value?: number) {
  if (value == null || !Number.isFinite(value)) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let amount = value
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1 }
  return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

function formatMem(value?: string | null) {
  if (!value) return '—'
  const match = value.match(/^([0-9.]+)\s*kB$/i)
  return match ? formatBytes(Number(match[1]) * 1024) : value
}

export function RuntimeOverview({ state }: { state?: SystemState }) {
  const status = state?.host?.status ?? {}
  const resources = state?.host?.resources ?? {}
  const storage = state?.host?.storage ?? {}
  const filesystems = storage.filesystems ?? []
  const invocation = status.invocation_id ? `${status.invocation_id.slice(0, 12)}…` : '—'

  return <Panel title="Runtime" className="runtime-overview">
    <div className="grid-3 runtime-kpis">
      <div><div className="meta">Version</div><strong>{state?.version ?? '—'}</strong></div>
      <div><div className="meta">Policy revision</div><strong>{state?.policy_revision ?? '—'}</strong></div>
      <div><div className="meta">Capabilities</div><strong>{state?.capabilities.length ?? '—'}</strong></div>
    </div>
    <div className="runtime-grid">
      <section className="runtime-section">
        <div className="runtime-section-head"><h3>Process</h3><span className="chip done">live</span></div>
        <div className="runtime-facts">
          <div><span>Host</span><strong>{status.hostname ?? '—'}</strong></div>
          <div><span>Kernel</span><strong>{status.kernel ?? '—'}</strong></div>
          <div><span>PID / UID</span><strong>{status.pid ?? '—'} / {status.uid ?? '—'}</strong></div>
          <div title={status.invocation_id ?? undefined}><span>Invocation</span><strong>{invocation}</strong></div>
        </div>
      </section>
      <section className="runtime-section">
        <div className="runtime-section-head"><h3>Resources</h3><span className="meta">{resources.cpu_count ?? '—'} CPU</span></div>
        <div className="runtime-facts">
          <div><span>Load 1 / 5 / 15</span><strong>{[resources.load_1, resources.load_5, resources.load_15].map(v => v == null ? '—' : v.toFixed(2)).join(' / ')}</strong></div>
          <div><span>Memory available</span><strong>{formatMem(resources.memory?.MemAvailable)}</strong></div>
          <div><span>Memory total</span><strong>{formatMem(resources.memory?.MemTotal)}</strong></div>
          <div><span>Swap free</span><strong>{formatMem(resources.memory?.SwapFree)}</strong></div>
        </div>
      </section>
      <section className="runtime-section">
        <div className="runtime-section-head"><h3>Storage</h3><span className="meta">{filesystems.length} observed</span></div>
        <div className="runtime-storage">
          {filesystems.length ? filesystems.map((fs, index) => <div className="runtime-storage-row" key={`${fs.path ?? 'fs'}:${index}`}><strong>{fs.path ?? '—'}</strong><span>{formatBytes(fs.free)} free / {formatBytes(fs.total)}</span></div>) : <div className="meta">No storage telemetry</div>}
        </div>
      </section>
    </div>
    <details className="inspect runtime-evidence"><summary>Raw host evidence</summary><pre>{JSON.stringify(state?.host ?? {}, null, 2)}</pre></details>
  </Panel>
}

function Pending({ items, onDone }: { items: ActionOccurrence[]; onDone: () => Promise<unknown> }) {
  if (!items.length) return null
  return <Panel title="Needs you" tone="decision-confirm"><div className="stack">{items.map(item => <ConfirmationCard key={item.occurrence_id} item={item} onDone={onDone} />)}</div></Panel>
}

function policyDescription(rule: PolicyRule, capabilities: Capability[]) {
  const matching = capabilities.filter(capability => capability.operation === rule.operation && capability.scope_hint)
  const exact = matching.find(capability => capability.scope_hint === rule.scope)
  const related = matching.find(capability => rule.scope.startsWith(capability.scope_hint ?? '') || (capability.scope_hint ?? '').startsWith(rule.scope))
  if (exact?.description) return exact.description
  if (related?.description) return related.description
  const operation = rule.operation.replace(/[._-]+/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
  return `${operation} on this resource`
}

export function PolicyPanel({ rules, capabilities, servers, revision, onDone }: { rules: PolicyRule[]; capabilities: Capability[]; servers: MCPServer[]; revision: number; onDone: () => Promise<unknown> }) {
  const [lens, setLens] = useState<PolicyLens>('system')
  const [scope, setScope] = useState('')
  const [operation, setOperation] = useState('')
  const [decision, setDecision] = useState<Decision>('CONFIRM')
  const [toolFilter, setToolFilter] = useState('')
  const save = useMutation({ mutationFn: (payload: { scope: string; operation: string; decision: Decision }) => api('/api/policy', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ scope, operation, decision }) }

  const systemRules = rules.filter(rule => policyLensFor(rule, servers) === 'system')
  const toolCapabilities = capabilities.filter(capability => capabilityLensFor(capability, servers) === lens && capability.scope_hint?.startsWith('mcp/'))
  const filteredToolCapabilities = toolCapabilities.filter(capability => !toolFilter || `${capability.id} ${capability.description} ${String(capability.metadata?.tool_name ?? '')}`.toLowerCase().includes(toolFilter.toLowerCase()))
  const tabs: Array<{ id: PolicyLens; label: string; count: number }> = [
    { id: 'system', label: 'System', count: systemRules.length },
    { id: 'n8n', label: 'n8n Tools', count: capabilities.filter(capability => capabilityLensFor(capability, servers) === 'n8n' && capability.scope_hint?.startsWith('mcp/')).length },
    { id: 'mcp', label: 'MCP Tools', count: capabilities.filter(capability => capabilityLensFor(capability, servers) === 'mcp' && capability.scope_hint?.startsWith('mcp/')).length },
  ]
  const toolGroups = servers.filter(server => server.kind === lens).map(server => ({ server, tools: filteredToolCapabilities.filter(capability => capability.metadata?.server_id === server.server_id) })).filter(group => group.tools.length)
  const disconnectedTools = filteredToolCapabilities.filter(capability => !servers.some(server => server.server_id === capability.metadata?.server_id))

  const decisionSelect = (scopeValue: string, operationValue: string, value: Decision, label: string) => <select className="policy-decision" aria-label={label} value={value} onChange={e => save.mutate({ scope: scopeValue, operation: operationValue, decision: e.target.value as Decision })}><option>NO</option><option>YES</option><option>CONFIRM</option></select>

  return <Panel title="Policies" className="policy-panel">
    <div className="policy-head">
      <p className="meta">Revision {revision} · changes apply immediately to the running Atlas. No service restart or provider reconfiguration.</p>
      <div className="policy-tabs" role="tablist" aria-label="Policy lenses">
        {tabs.map(tab => <button key={tab.id} type="button" role="tab" aria-selected={lens === tab.id} className={lens === tab.id ? 'policy-tab active' : 'policy-tab'} onClick={() => setLens(tab.id)}>{tab.label}<span>{tab.count}</span></button>)}
      </div>
    </div>

    {lens !== 'system' ? <input value={toolFilter} onChange={e => setToolFilter(e.target.value)} placeholder="Filter provider tools" aria-label="Filter provider tools" /> : null}

    {lens === 'system' ? <div className="policy-list">
      {systemRules.map(rule => <div className="policy-item" key={`${rule.scope}:${rule.operation}`}><div className="policy-copy"><strong>{policyDescription(rule, capabilities)}</strong><div className="policy-syntax">{rule.scope} · {rule.operation}</div></div>{decisionSelect(rule.scope, rule.operation, rule.decision, `${rule.scope} ${rule.operation} decision`)}</div>)}
    </div> : <div className="policy-tool-groups">
      {toolGroups.map(({ server, tools }) => <section className="policy-tool-group" key={server.server_id}><div className="policy-tool-group-head"><div><span className="eyebrow">{server.kind}</span><h3>{server.display_name}</h3></div><span className="chip">{tools.length} tools</span></div><div className="policy-list">{tools.map(tool => <div className="policy-item" key={tool.id}><div className="policy-copy"><strong>{tool.description}</strong><div className="policy-syntax">{tool.id}</div><div className="policy-syntax">{tool.scope_hint} · {tool.operation}</div></div>{decisionSelect(tool.scope_hint ?? '', tool.operation, tool.policy_decision, `${tool.id} decision`)}</div>)}</div></section>)}
      {disconnectedTools.length ? <section className="policy-tool-group"><div className="policy-tool-group-head"><div><span className="eyebrow">Stored</span><h3>Disconnected tools</h3></div><span className="chip">{disconnectedTools.length}</span></div><div className="policy-list">{disconnectedTools.map(tool => <div className="policy-item" key={tool.id}><div className="policy-copy"><strong>{tool.description}</strong><div className="policy-syntax">{tool.id}</div><div className="policy-syntax">{tool.scope_hint} · {tool.operation}</div></div>{decisionSelect(tool.scope_hint ?? '', tool.operation, tool.policy_decision, `${tool.id} decision`)}</div>)}</div></section> : null}
      {!toolGroups.length && !disconnectedTools.length ? <div className="empty">No {lens === 'n8n' ? 'n8n' : 'MCP'} tools are currently discovered.</div> : null}
    </div>}

    <details className="inspect policy-advanced"><summary>Advanced policy override</summary><form className="grid-3 policy-add" onSubmit={submit}><input value={scope} onChange={e => setScope(e.target.value)} placeholder="resource scope" /><input value={operation} onChange={e => setOperation(e.target.value)} placeholder="operation" /><div className="actions"><select value={decision} onChange={e => setDecision(e.target.value as Decision)}><option>NO</option><option>YES</option><option>CONFIRM</option></select><button className="primary" type="submit">Set</button></div></form></details>
  </Panel>
}

export function Providers({ providers, onDone }: { providers: Provider[]; onDone: () => Promise<unknown> }) {
  const [key, setKey] = useState(''); const [kind, setKind] = useState('openai_compatible'); const [model, setModel] = useState(''); const [base, setBase] = useState(''); const [secret, setSecret] = useState(''); const [priority, setPriority] = useState(50)
  const [replacementKeys, setReplacementKeys] = useState<Record<string, string>>({})
  const [providerPriorities, setProviderPriorities] = useState<Record<string, number>>({})
  const save = useMutation({ mutationFn: (payload: object) => api('/api/providers', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: onDone })
  const update = useMutation({
    mutationFn: ({ item, apiKey, enabled, priority: nextPriority }: { item: Provider; apiKey?: string; enabled?: boolean; priority?: number }) => api('/api/providers', {
      method: 'POST',
      body: JSON.stringify({ key: item.key, kind: item.kind, model: item.model, base_url: item.base_url ?? null, api_key: apiKey || undefined, enabled: enabled ?? item.enabled, local: item.local, priority: nextPriority ?? item.priority, metadata: item.metadata }),
    }),
    onSuccess: async (_data, variables) => { setReplacementKeys(current => ({ ...current, [variables.item.key]: '' })); await onDone() },
  })
  const verify = useMutation({ mutationFn: (providerKey: string) => api(`/api/providers/${providerKey}/verify`, { method: 'POST', body: '{}' }) })
  const remove = useMutation({ mutationFn: (providerKey: string) => api(`/api/providers/${providerKey}`, { method: 'DELETE' }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ key, kind, model, base_url: base || null, api_key: secret || undefined, enabled: true, local: kind === 'openai_compatible' && base.includes('127.0.0.1'), priority }) }
  const ordered = [...providers].sort((a, b) => b.priority - a.priority || a.key.localeCompare(b.key))
  return <Panel title="Models & providers" className="providers-panel"><p className="meta provider-help">Higher priority runs first. If it fails, Atlas tries the next enabled provider.</p><div className="provider-list">{ordered.map(item => {
    const draftPriority = providerPriorities[item.key] ?? item.priority
    return <div className="provider-row" key={item.key}>
      <div className="provider-copy"><div className="provider-title"><strong>{item.key}</strong><span className={`chip ${item.enabled ? 'done' : ''}`}>{item.enabled ? 'enabled' : 'disabled'}</span></div><div className="meta">{item.model} · {item.kind} · {item.base_url || 'default endpoint'} · {item.credential_configured ? 'credential configured' : 'no credential'}</div></div>
      <div className="provider-priority"><label><span>Priority</span><input type="number" aria-label={`Priority for ${item.key}`} value={draftPriority} onChange={e => setProviderPriorities(current => ({ ...current, [item.key]: Number(e.target.value) }))} /></label><button type="button" disabled={draftPriority === item.priority || update.isPending} onClick={() => update.mutate({ item, priority: draftPriority })}>Save</button></div>
      <div className="provider-actions"><button onClick={() => verify.mutate(item.key)}>Verify</button><button onClick={() => update.mutate({ item, enabled: !item.enabled })}>{item.enabled ? 'Disable' : 'Enable'}</button><button className="danger" onClick={() => remove.mutate(item.key)}>Remove</button></div>
      <details className="provider-credential"><summary>{item.credential_configured ? 'Replace API key' : 'Add API key'}</summary><div className="provider-key-row"><input type="password" aria-label={`Replace API key for ${item.key}`} value={replacementKeys[item.key] ?? ''} onChange={e => setReplacementKeys(current => ({ ...current, [item.key]: e.target.value }))} placeholder="API key" /><button type="button" disabled={!replacementKeys[item.key]?.trim() || update.isPending} onClick={() => update.mutate({ item, apiKey: replacementKeys[item.key].trim(), enabled: true })}>Save key & enable</button></div></details>
    </div>
  })}</div>
    {update.isError ? <p className="offline-banner">{update.error.message}</p> : null}
    {save.isError ? <p className="offline-banner">{save.error.message}</p> : null}
    <details className="inspect provider-add"><summary>Add provider</summary><form className="grid-3" onSubmit={submit} style={{ marginTop: '1rem' }}><input value={key} onChange={e => setKey(e.target.value)} placeholder="provider key" /><select value={kind} onChange={e => setKind(e.target.value)}><option value="openai_compatible">OpenAI compatible</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option><option value="gemini">Gemini</option></select><input value={model} onChange={e => setModel(e.target.value)} placeholder="model" /><input value={base} onChange={e => setBase(e.target.value)} placeholder="base URL (optional)" /><input type="password" value={secret} onChange={e => setSecret(e.target.value)} placeholder="API key (optional)" /><input type="number" value={priority} onChange={e => setPriority(Number(e.target.value))} aria-label="New provider priority" /><button className="primary" type="submit">Save provider</button></form></details>
    {verify.data ? <pre className="mono provider-verify-result">{JSON.stringify(verify.data, null, 2)}</pre> : null}
    {verify.isError ? <p className="offline-banner">{verify.error.message}</p> : null}
  </Panel>
}

function Mcp({ servers, onDone }: { servers: MCPServer[]; onDone: () => Promise<unknown> }) {
  const location = useLocation()
  const parentBack = (location.state as { atlasBack?: AtlasBack } | null)?.atlasBack
  const [serverId, setServerId] = useState(''); const [name, setName] = useState(''); const [kind, setKind] = useState<'mcp' | 'n8n'>('mcp'); const [transport, setTransport] = useState<'streamable-http' | 'stdio'>('streamable-http'); const [url, setUrl] = useState(''); const [token, setToken] = useState(''); const [command, setCommand] = useState(''); const [argsText, setArgsText] = useState(''); const [cwd, setCwd] = useState('')
  const save = useMutation({ mutationFn: (payload: object) => api('/api/mcp', { method: 'POST', body: JSON.stringify(payload) }), onSuccess: onDone })
  const refresh = useMutation({ mutationFn: (id: string) => api(`/api/mcp/${id}/refresh`, { method: 'POST', body: '{}' }), onSuccess: onDone })
  const remove = useMutation({ mutationFn: (id: string) => api(`/api/mcp/${id}`, { method: 'DELETE' }), onSuccess: onDone })
  function submit(event: FormEvent) { event.preventDefault(); save.mutate({ server_id: serverId, display_name: name || serverId, kind, transport, url: transport === 'streamable-http' ? url : null, token: transport === 'streamable-http' ? token || undefined : undefined, command: transport === 'stdio' ? command : null, args: transport === 'stdio' ? argsText.split('\n').map(item => item.trim()).filter(Boolean) : [], cwd: transport === 'stdio' ? cwd || null : null, enabled: true }) }
  return <Panel title="MCP & n8n connections"><p className="meta">Configure technical provider connections here. Discovered tools and NO / YES / CONFIRM controls live under Policies.</p><div className="stack">{servers.map(item => <div className="list-row" key={item.server_id}><div><strong>{item.display_name}</strong><div className="actions" style={{ marginTop: '0.35rem' }}><span className="chip">{item.kind}</span><span className="chip">{item.transport}</span><span className="chip">{item.enabled ? 'enabled' : 'disabled'}</span></div><div className="meta">{item.transport === 'stdio' ? [item.command, ...(item.args ?? [])].filter(Boolean).join(' ') : item.url}</div></div>{item.last_error ? <p className="offline-banner">{item.last_error}</p> : null}<div className="actions"><Link className="settings-link" to="/atlas/policies" state={{ atlasBack: { path: location.pathname, parent: parentBack ?? { path: '/atlas' } } }}>Tools & policy</Link><button onClick={() => refresh.mutate(item.server_id)}>Refresh discovery</button><button className="danger" onClick={() => remove.mutate(item.server_id)}>Remove</button></div></div>)}</div>
    <form className="grid-3" onSubmit={submit} style={{ marginTop: '1rem' }}><input value={serverId} onChange={e => setServerId(e.target.value)} placeholder="server id" /><input value={name} onChange={e => setName(e.target.value)} placeholder="display name" /><select value={kind} onChange={e => setKind(e.target.value as 'mcp' | 'n8n')}><option value="mcp">MCP</option><option value="n8n">n8n</option></select><select value={transport} onChange={e => setTransport(e.target.value as 'streamable-http' | 'stdio')}><option value="streamable-http">Streamable HTTP</option><option value="stdio">stdio</option></select>{transport === 'streamable-http' ? <><input value={url} onChange={e => setUrl(e.target.value)} placeholder="Streamable HTTP URL" /><input type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Bearer token (optional)" /></> : <><input value={command} onChange={e => setCommand(e.target.value)} placeholder="command / executable" /><textarea value={argsText} onChange={e => setArgsText(e.target.value)} placeholder={'one argument per line'} /><input value={cwd} onChange={e => setCwd(e.target.value)} placeholder="working directory (optional)" /></>}<button className="primary" type="submit">Save server</button></form>
  </Panel>
}


function ExternalAccounts({ connections, bindings }: { connections: Array<Record<string, unknown>>; bindings: Array<Record<string, unknown>> }) {
  if (!connections.length && !bindings.length) return null
  return <Panel title="External accounts"><div className="grid-2"><div><h3>Connections</h3>{connections.map((item, i) => <div className="list-row" key={String(item.connection_id ?? i)}><strong>{String(item.display_name ?? item.canonical_address ?? item.connection_id)}</strong><div className="meta">{String(item.provider_id ?? '')} · {String(item.status ?? '')}</div></div>)}</div><div><h3>Technical service bindings</h3>{bindings.map((item, i) => <div className="list-row" key={String(item.binding_id ?? i)}><strong>{String(item.service ?? '')}</strong><div className="meta">{String(item.channel ?? '')}</div><div>{Array.isArray(item.attested_operations) ? item.attested_operations.join(', ') : ''}</div></div>)}</div></div></Panel>
}


function Roots({ roots, onDone }: { roots: SourceRoot[]; onDone: () => Promise<unknown> }) {
  const [id, setId] = useState(''); const [path, setPath] = useState(''); const [name, setName] = useState('')
  const save = useMutation({ mutationFn: () => api('/api/sources/roots', { method: 'POST', body: JSON.stringify({ root_id: id, host_path: path, display_name: name || id, enabled: true, quarantine_relative_path: '.atlas-quarantine' }) }), onSuccess: onDone })
  const remove = useMutation({ mutationFn: (rootId: string) => api(`/api/sources/roots/${rootId}`, { method: 'DELETE' }), onSuccess: onDone })
  return <Panel title="Filesystem roots"><p className="meta">Enrollment exposes the kernel capability. Read/write/delete authority is controlled only by the runtime policy above.</p><div className="stack">{roots.map(root => <div className="list-row" key={root.root_id}><strong>{root.display_name}</strong><span className="chip">{root.enabled ? 'enabled' : 'disabled'}</span><div className="meta">{root.host_path}</div><button className="danger" onClick={() => remove.mutate(root.root_id)}>Remove</button></div>)}</div><div className="grid-3" style={{ marginTop: '1rem' }}><input value={id} onChange={e => setId(e.target.value)} placeholder="root id" /><input value={name} onChange={e => setName(e.target.value)} placeholder="display name" /><input value={path} onChange={e => setPath(e.target.value)} placeholder="absolute host path" /><button className="primary" onClick={() => save.mutate()} disabled={!id || !path}>Enroll root</button></div></Panel>
}
