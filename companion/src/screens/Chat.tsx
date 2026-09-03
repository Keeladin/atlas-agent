import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ActionOccurrence, Cadence, ChatTurn, Conversation, WorkItem } from '../api/types'
import { ConfirmationCard } from '../ui/ConfirmationCard'
import { FactList, InspectorSection, StatusLamp } from '../ui/OperationsPrimitives'
import type { LampTone } from '../ui/operationState'
import { cadenceStateToLamp, workStateToLamp } from '../ui/operationState'
import { Workspace } from '../ui/Workspace'

type ConversationList = { conversations: Conversation[] }
type SendRequest = { conversationId: string; text: string }
type Health = { ok: boolean; service: string; version: string }

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const today = new Date()
  const sameDay = date.getFullYear() === today.getFullYear() && date.getMonth() === today.getMonth() && date.getDate() === today.getDate()
  return sameDay ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function actionForTurn(turn?: ChatTurn | null) {
  const value = turn?.metadata?.action
  return value && typeof value === 'object' ? value as ActionOccurrence : null
}

function toolsForTurn(turn?: ChatTurn | null) {
  const tools = Array.isArray(turn?.metadata?.tools_used) ? turn.metadata.tools_used.filter((value): value is string => typeof value === 'string' && Boolean(value)) : []
  const action = actionForTurn(turn)
  return [...new Set([...tools, ...(action?.capability_id ? [action.capability_id] : [])])]
}

function actionTone(status?: string | null): LampTone {
  if (status === 'succeeded') return 'green'
  if (status === 'pending_confirmation' || status === 'executing' || status === 'uncertain') return 'amber'
  if (status === 'failed' || status === 'blocked' || status === 'expired' || status === 'cancelled') return 'red'
  return 'dim'
}

function nextCadence(rows: Cadence[]) {
  return rows
    .filter(item => item.enabled && item.next_run_at)
    .sort((a, b) => String(a.next_run_at).localeCompare(String(b.next_run_at)))[0]
}

export function Chat() {
  const qc = useQueryClient()
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: () => api<ConversationList>('/api/chat/conversations') })
  const pending = useQuery({ queryKey: ['pending-actions'], queryFn: () => api<{ actions: ActionOccurrence[] }>('/api/actions/pending'), refetchInterval: 5000 })
  const work = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work'), refetchInterval: 7000 })
  const cadence = useQuery({ queryKey: ['cadence'], queryFn: () => api<{ cadences: Cadence[] }>('/api/cadence'), refetchInterval: 15000 })
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/api/health'), refetchInterval: 15000 })
  const [selected, setSelected] = useState<string | null>(null)
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null)
  const [conversationFilter, setConversationFilter] = useState('')
  const [message, setMessage] = useState('')
  const threadEndRef = useRef<HTMLDivElement | null>(null)

  const { mutate: createConversation, isPending: createPending } = useMutation({
    mutationFn: () => api<Conversation>('/api/chat/conversations', { method: 'POST', body: JSON.stringify({ title: 'Conversation' }) }),
    onSuccess: item => {
      qc.setQueryData<ConversationList>(['conversations'], current => ({ conversations: [item, ...(current?.conversations ?? []).filter(row => row.conversation_id !== item.conversation_id)] }))
      setSelected(item.conversation_id)
      setSelectedTurnId(null)
      void qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
  const deleteConversation = useMutation({
    mutationFn: (conversationId: string) => api(`/api/chat/conversations/${conversationId}`, { method: 'DELETE' }),
    onSuccess: async (_data, conversationId) => {
      const current = qc.getQueryData<ConversationList>(['conversations'])?.conversations ?? []
      const remaining = current.filter(item => item.conversation_id !== conversationId)
      qc.setQueryData<ConversationList>(['conversations'], { conversations: remaining })
      qc.removeQueries({ queryKey: ['conversation', conversationId] })
      setSelected(value => value === conversationId ? (remaining[0]?.conversation_id ?? null) : value)
      setSelectedTurnId(null)
      await qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const items = useMemo(() => conversations.data?.conversations ?? [], [conversations.data])
  const visibleConversations = useMemo(() => items.filter(item => !conversationFilter || item.title.toLowerCase().includes(conversationFilter.toLowerCase())), [items, conversationFilter])
  useEffect(() => {
    if (selected && items.some(item => item.conversation_id === selected)) return
    setSelected(items[0]?.conversation_id ?? null)
  }, [items, selected])
  useEffect(() => {
    if (!selected && conversations.isSuccess && items.length === 0 && !createPending) createConversation()
  }, [selected, conversations.isSuccess, items.length, createPending, createConversation])

  const selectedValid = Boolean(selected && items.some(item => item.conversation_id === selected))
  const detail = useQuery({ queryKey: ['conversation', selected], queryFn: () => api<{ conversation: Conversation; turns: ChatTurn[] }>(`/api/chat/conversations/${selected}`), enabled: selectedValid })
  const send = useMutation({
    mutationFn: ({ conversationId, text }: SendRequest) => api<{ turn: ChatTurn; action?: Record<string, unknown> }>(`/api/chat/conversations/${conversationId}/messages`, { method: 'POST', body: JSON.stringify({ message: text }) }),
    onSuccess: async (_data, variables) => {
      setMessage('')
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['conversation', variables.conversationId] }),
        qc.invalidateQueries({ queryKey: ['pending-actions'] }),
        qc.invalidateQueries({ queryKey: ['conversations'] }),
        qc.invalidateQueries({ queryKey: ['work'] }),
      ])
    },
  })

  const turns = useMemo(() => detail.data?.turns ?? [], [detail.data?.turns])
  const pendingRows = useMemo(() => pending.data?.actions ?? [], [pending.data?.actions])
  const pendingById = useMemo(() => new Map(pendingRows.map(action => [action.occurrence_id, action])), [pendingRows])
  const conversationPending = useMemo(() => turns.flatMap(turn => {
    const recorded = actionForTurn(turn)
    const live = recorded ? pendingById.get(recorded.occurrence_id) : null
    return live ? [{ turnId: turn.turn_id, action: live }] : []
  }), [turns, pendingById])
  const workRows = work.data?.work ?? []
  const activeWork = workRows.filter(item => ['active', 'running', 'waiting', 'waiting_confirmation', 'paused'].includes(item.status)).slice(0, 5)
  const failedWork = workRows.filter(item => item.status === 'failed')
  const cadenceRows = cadence.data?.cadences ?? []
  const enabledCadence = cadenceRows.filter(item => item.enabled)
  const upcomingCadence = nextCadence(cadenceRows)

  useEffect(() => {
    if (selectedTurnId && turns.some(turn => turn.turn_id === selectedTurnId)) return
    setSelectedTurnId(null)
  }, [turns, selectedTurnId])
  useEffect(() => {
    const node = threadEndRef.current
    if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'end' })
  }, [turns.length, conversationPending.length, send.isPending])

  function submit(event: FormEvent) {
    event.preventDefault()
    const text = message.trim()
    if (text && selected && selectedValid && !send.isPending) send.mutate({ conversationId: selected, text })
  }
  function submitFromKeyboard(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    const text = message.trim()
    if (text && selected && selectedValid && !send.isPending) send.mutate({ conversationId: selected, text })
  }
  function removeConversation(item: Conversation) {
    if (window.confirm(`Delete conversation “${item.title}”? This removes its chat history.`)) deleteConversation.mutate(item.conversation_id)
  }

  const currentConversation = detail.data?.conversation ?? items.find(item => item.conversation_id === selected) ?? null
  const selectedTurn = turns.find(turn => turn.turn_id === selectedTurnId) ?? null
  const runtimeTone: LampTone = health.isError ? 'red' : health.data?.ok ? 'green' : 'dim'

  return <Workspace className="chat-control-workspace" title="Atlas" subtitle={currentConversation?.title ? `Current conversation · ${currentConversation.title}` : 'One living operational surface.'} fillHeight headerActions={<><StatusLamp tone={send.isPending ? 'amber' : runtimeTone} label={send.isPending ? 'Working' : health.data?.ok ? 'Runtime ready' : health.isError ? 'Runtime unavailable' : 'Checking runtime'} /><button type="button" className="compact-button" onClick={() => createConversation()} disabled={createPending}>{createPending ? 'Creating…' : 'New conversation'}</button></>}>
    <nav className="surface-awareness" aria-label="Atlas operational awareness">
      <Link to="/work"><span>Active Work</span><strong>{activeWork.length}</strong><small>{failedWork.length ? `${failedWork.length} failed` : 'No failed Work'}</small></Link>
      <Link to="/cadence"><span>Cadence</span><strong>{enabledCadence.length}</strong><small>{upcomingCadence ? `Next ${when(upcomingCadence.next_run_at)}` : 'No scheduled run'}</small></Link>
      <a href="#needs-you"><span>Needs you</span><strong className={pendingRows.length ? 'attention-value' : ''}>{pendingRows.length}</strong><small>{pendingRows.length ? 'Owner decision pending' : 'Nothing blocked on you'}</small></a>
      <Link to="/atlas"><span>Control</span><strong>{health.data?.version ?? '—'}</strong><small>Policy · providers · connections</small></Link>
    </nav>

    <section className="chat-conversation-switcher" aria-label="Conversations">
      <input aria-label="Search conversations" value={conversationFilter} onChange={event => setConversationFilter(event.target.value)} placeholder="Find a conversation" />
      <div className="chat-conversation-flow">{visibleConversations.map(item => <div className="chat-conversation-tab" key={item.conversation_id}><button type="button" aria-label={`${item.title} ${item.updated_at}`} className={selected === item.conversation_id ? 'active' : ''} onClick={() => { setSelected(item.conversation_id); setSelectedTurnId(null) }}><strong>{item.title}</strong><time>{when(item.updated_at)}</time></button>{selected === item.conversation_id ? <button type="button" className="chat-conversation-delete" title="Delete conversation" aria-label={`Delete ${item.title}`} onClick={() => removeConversation(item)}>×</button> : null}</div>)}{!visibleConversations.length ? <span className="chat-conversation-empty">No matching conversations</span> : null}</div>
      <span className="chat-flow-status"><b>{turns.length}</b> turns <i>·</i> <b>{conversationPending.length}</b> awaiting confirmation</span>
    </section>

    <div className="surface-live-grid">
      <section className="chat-conversation-surface" aria-label="Conversation and execution">
        <div className="chat-thread-body">{turns.map(turn => {
          const recorded = actionForTurn(turn)
          const liveAction = recorded ? pendingById.get(recorded.occurrence_id) ?? null : null
          const tools = toolsForTurn(turn)
          const selectedHere = selectedTurn?.turn_id === turn.turn_id
          const status = liveAction?.status ?? recorded?.status
          const turnError = typeof turn.metadata?.error === 'string' ? turn.metadata.error : null
          return <article key={turn.turn_id} className={`chat-turn ${turn.role} ${selectedHere ? 'selected' : ''}`}><div className="chat-turn-meta"><span className="chat-turn-role">{turn.role === 'user' ? 'You' : turn.role === 'assistant' ? 'Atlas' : turn.role}</span><time>{when(turn.created_at)}</time></div><div className="chat-turn-main"><div className="chat-turn-content">{turn.content}</div>{tools.length ? <div className="chat-turn-tools">{tools.map(tool => <span className="chat-tool-trace" key={tool}><StatusLamp tone={recorded?.capability_id === tool ? actionTone(status) : 'blue'} /><span>{tool}</span></span>)}</div> : null}{liveAction ? <div className="chat-turn-confirmation"><ConfirmationCard item={liveAction} title="Needs your approval" confirmLabel="Approve exact action" cancelLabel="Reject" onDone={async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['conversation', selected] }), qc.invalidateQueries({ queryKey: ['pending-actions'] }), qc.invalidateQueries({ queryKey: ['work'] })]) }} /></div> : null}{turnError ? <p className="offline-banner">{turnError}</p> : null}<button type="button" className="chat-trace-toggle" aria-expanded={selectedHere} onClick={() => setSelectedTurnId(selectedHere ? null : turn.turn_id)}>{selectedHere ? 'Close trace' : 'Trace'}</button>{selectedHere ? <div className="chat-inline-context"><InspectorSection title="Runtime trace"><FactList items={[
            { label: 'Turn', value: turn.turn_id, mono: true },
            { label: 'Created', value: when(turn.created_at), mono: true },
            ...(recorded ? [
              { label: 'Action', value: recorded.occurrence_id, mono: true },
              { label: 'Scope', value: recorded.scope, mono: true },
              { label: 'Authority', value: `${recorded.policy_decision} · revision ${recorded.policy_revision}`, mono: true },
            ] : []),
          ]} /></InspectorSection><details className="inspect chat-turn-evidence"><summary>Evidence and recorded metadata</summary><pre>{JSON.stringify(turn.metadata, null, 2)}</pre></details></div> : null}</div></article>
        })}{send.isPending ? <div className="chat-working"><StatusLamp tone="amber" label="Atlas is working" /></div> : null}{detail.isError ? <p className="offline-banner">{detail.error.message}</p> : null}<div ref={threadEndRef} aria-hidden /></div>
        {send.isError ? <p className="offline-banner chat-send-error">{send.error.message}</p> : null}
        <form className="composer chat-composer" onSubmit={submit}><textarea aria-label="Message" value={message} onChange={e => setMessage(e.target.value)} onKeyDown={submitFromKeyboard} placeholder="Ask Atlas…" /><div className="chat-composer-footer"><span className="composer-hint">Enter to send <i>·</i> Shift+Enter for a new line</span><button className="primary" type="submit" disabled={send.isPending || !message.trim() || !selectedValid} aria-label="Send">{send.isPending ? 'Working…' : 'Send ↗'}</button></div></form>
      </section>

      <aside className="surface-margin" aria-label="Operational awareness">
        <section id="needs-you" className="surface-margin-section"><div className="surface-margin-heading"><span>Needs you</span><strong className={pendingRows.length ? 'attention-value' : ''}>{pendingRows.length}</strong></div>{pendingRows.length ? pendingRows.slice(0, 4).map(item => <Link className="surface-margin-row" key={item.occurrence_id} to={item.work_id ? `/work/${item.work_id}` : '/chat'}><StatusLamp tone="red" /><span><strong>{item.summary || item.capability_id}</strong><small>{item.operation} · {item.scope}</small></span></Link>) : <p className="surface-margin-empty">No owner decision is blocking Atlas.</p>}</section>
        <section className="surface-margin-section"><div className="surface-margin-heading"><span>Active Work</span><Link to="/work">Open Work ↗</Link></div>{activeWork.length ? activeWork.map(item => <Link className="surface-margin-row" key={item.work_id} to={`/work/${item.work_id}`}><StatusLamp tone={workStateToLamp(item.status)} /><span><strong>{item.display_ref ?? item.objective}</strong><small>{item.objective}</small><em>{item.status.replaceAll('_', ' ')}</em></span></Link>) : <p className="surface-margin-empty">No Work is active or waiting.</p>}</section>
        <section className="surface-margin-section"><div className="surface-margin-heading"><span>Cadence</span><Link to="/cadence">Open cadence ↗</Link></div>{enabledCadence.length ? enabledCadence.slice(0, 4).map(item => <Link className="surface-margin-row" key={item.cadence_id} to="/cadence"><StatusLamp tone={cadenceStateToLamp(item.enabled, item.next_run_at)} /><span><strong>{item.name}</strong><small>{item.objective}</small><em>{when(item.next_run_at)}</em></span></Link>) : <p className="surface-margin-empty">No standing duties are enabled.</p>}</section>
        <section className="surface-margin-section surface-plumbing"><div className="surface-margin-heading"><span>Plumbing</span><Link to="/atlas">Control ↗</Link></div><div className="surface-plumbing-links"><Link to="/sources">Sources</Link><Link to="/memory">Memory</Link><Link to="/atlas/policies">Policy</Link><Link to="/atlas/capabilities">Capabilities</Link><Link to="/atlas/connections">Connections</Link></div></section>
      </aside>
    </div>
  </Workspace>
}
