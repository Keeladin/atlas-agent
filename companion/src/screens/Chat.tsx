import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, uploadArtifact } from '../api/client'
import type { ActionOccurrence, Cadence, ChatFocus, ChatTurn, Conversation, WorkItem } from '../api/types'
import { ArtifactObject } from '../ui/ArtifactObject'
import { StatusLamp } from '../ui/OperationsPrimitives'
import type { LampTone } from '../ui/operationState'
import { workStateToLamp } from '../ui/operationState'
import { RuntimeObject, type RuntimeObjectDescriptor } from '../ui/RuntimeObject'
import { WorkflowCard } from '../ui/WorkflowCard'
import { readFocus, workflowVariant } from '../ui/workflowPresentation'

type ConversationList = { conversations: Conversation[] }
type Health = { ok: boolean; service: string; version: string }
type Attachment = { artifact_id: string; display_name: string; media_type?: string | null; created_at?: string | null }
type SendRequest = { conversationId: string; text: string; focus?: ChatFocus | null; attachments: Attachment[] }
type UploadResponse = { artifact: Attachment }

function when(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const today = new Date()
  const sameDay = date.toDateString() === today.toDateString()
  return sameDay ? date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function actionForTurn(turn?: ChatTurn | null) {
  const value = turn?.metadata?.action
  return value && typeof value === 'object' ? value as ActionOccurrence : null
}

function objectsForTurn(turn: ChatTurn): RuntimeObjectDescriptor[] {
  const raw = Array.isArray(turn.metadata?.objects) ? turn.metadata.objects : []
  return raw.flatMap(value => {
    if (!value || typeof value !== 'object') return []
    const row = value as Record<string, unknown>
    const kind = row.kind
    const id = typeof row.id === 'string' ? row.id : ''
    return id && (kind === 'work' || kind === 'artifact' || kind === 'cadence') ? [{ kind, id } as RuntimeObjectDescriptor] : []
  })
}

function attachmentsForTurn(turn: ChatTurn): Attachment[] {
  const raw = Array.isArray(turn.metadata?.attachments) ? turn.metadata.attachments : []
  return raw.flatMap(value => {
    if (!value || typeof value !== 'object') return []
    const row = value as Record<string, unknown>
    return typeof row.artifact_id === 'string' && typeof row.display_name === 'string' ? [{
      artifact_id: row.artifact_id,
      display_name: row.display_name,
      media_type: typeof row.media_type === 'string' ? row.media_type : null,
      created_at: typeof row.created_at === 'string' ? row.created_at : null,
    }] : []
  })
}

export function Chat() {
  const qc = useQueryClient()
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: () => api<ConversationList>('/api/chat/conversations') })
  const work = useQuery({ queryKey: ['work'], queryFn: () => api<{ work: WorkItem[] }>('/api/work'), refetchInterval: 6000 })
  const cadence = useQuery({ queryKey: ['cadence'], queryFn: () => api<{ cadences: Cadence[] }>('/api/cadence'), refetchInterval: 12000 })
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/api/health'), refetchInterval: 15000 })
  const [selected, setSelected] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [pendingFocus, setPendingFocus] = useState<ChatFocus | null>(null)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [runtimeOpen, setRuntimeOpen] = useState(false)
  const [conversationFilter, setConversationFilter] = useState('')
  const [traceTurn, setTraceTurn] = useState<string | null>(null)
  const threadEndRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()

  useEffect(() => {
    const focus = readFocus(searchParams)
    const ask = searchParams.get('ask')
    if (!focus && !ask) return
    if (focus) setPendingFocus(focus)
    if (ask) setMessage(ask)
    setSearchParams(new URLSearchParams(), { replace: true })
  }, [searchParams, setSearchParams])

  const createConversation = useMutation({
    mutationFn: () => api<Conversation>('/api/chat/conversations', { method: 'POST', body: JSON.stringify({ title: 'Conversation' }) }),
    onSuccess: item => {
      qc.setQueryData<ConversationList>(['conversations'], current => ({ conversations: [item, ...(current?.conversations ?? []).filter(row => row.conversation_id !== item.conversation_id)] }))
      setSelected(item.conversation_id); setHistoryOpen(false); setAttachments([])
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
      await qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const items = useMemo(() => conversations.data?.conversations ?? [], [conversations.data])
  useEffect(() => {
    if (selected && items.some(item => item.conversation_id === selected)) return
    setSelected(items[0]?.conversation_id ?? null)
  }, [items, selected])
  useEffect(() => {
    if (!selected && conversations.isSuccess && items.length === 0 && !createConversation.isPending) createConversation.mutate()
  }, [selected, conversations.isSuccess, items.length, createConversation])

  const selectedValid = Boolean(selected && items.some(item => item.conversation_id === selected))
  const detail = useQuery({
    queryKey: ['conversation', selected],
    queryFn: () => api<{ conversation: Conversation; turns: ChatTurn[] }>(`/api/chat/conversations/${selected}`),
    enabled: selectedValid,
  })
  const send = useMutation({
    mutationFn: ({ conversationId, text, focus, attachments: attached }: SendRequest) => api<{ turn: ChatTurn }>(`/api/chat/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message: text, ...(focus ? { focus } : {}), ...(attached.length ? { attachments: attached.map(item => item.artifact_id) } : {}) }),
    }),    onSuccess: async (_data, variables) => {
      setMessage(''); setPendingFocus(null); setAttachments([]); setUploadError(null)
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['conversation', variables.conversationId] }),
        qc.invalidateQueries({ queryKey: ['conversations'] }),
        qc.invalidateQueries({ queryKey: ['work'] }),
        qc.invalidateQueries({ queryKey: ['cadence'] }),
      ])
    },
  })

  async function addFiles(files: FileList | File[]) {
    const batch = Array.from(files).slice(0, Math.max(0, 8 - attachments.length))
    if (!batch.length) return
    setUploading(true); setUploadError(null)
    try {
      const added: Attachment[] = []
      for (const file of batch) {
        const response = await uploadArtifact<UploadResponse>(file)
        added.push(response.artifact)
      }
      setAttachments(current => [...current, ...added].slice(0, 8))
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : String(error))
    } finally { setUploading(false) }
  }

  const turns = useMemo(() => detail.data?.turns ?? [], [detail.data?.turns])
  const workRows = work.data?.work ?? []
  const activeWork = workRows.filter(item => ['active', 'queued', 'waiting', 'paused'].includes(item.status)).slice(0, 6)
  const attentionWork = workRows.filter(item => ['failed', 'paused', 'waiting'].includes(item.status))
  const enabledCadence = (cadence.data?.cadences ?? []).filter(item => item.enabled).slice(0, 5)
  const currentConversation = detail.data?.conversation ?? items.find(item => item.conversation_id === selected) ?? null
  const visibleConversations = items.filter(item => !conversationFilter || item.title.toLowerCase().includes(conversationFilter.toLowerCase()))
  const runtimeTone: LampTone = health.isError ? 'red' : health.data?.ok ? 'green' : 'dim'

  useEffect(() => {
    const node = threadEndRef.current
    if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'end' })
  }, [turns.length, send.isPending])

  function submit(event?: FormEvent) {
    event?.preventDefault()
    const text = message.trim()
    if ((text || attachments.length) && selected && selectedValid && !send.isPending && !uploading) {
      send.mutate({ conversationId: selected, text, focus: pendingFocus, attachments })
    }
  }
  function submitFromKeyboard(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault(); submit()
  }
  function onDrop(event: DragEvent<HTMLFormElement>) {
    event.preventDefault()
    if (event.dataTransfer.files?.length) void addFiles(event.dataTransfer.files)
  }
  function removeConversation(item: Conversation) {
    if (window.confirm(`Delete conversation “${item.title}”? This removes its chat history.`)) deleteConversation.mutate(item.conversation_id)
  }

  return <div className="owner-canvas">
    <header className="owner-canvas-head">
      <div className="owner-canvas-title">
        <span className="owner-canvas-kicker"><StatusLamp tone={send.isPending ? 'amber' : runtimeTone} />{send.isPending ? 'Working' : health.data?.ok ? 'Ready' : 'Runtime'}</span>
        <h1>{currentConversation?.title || 'Atlas'}</h1>
      </div>
      <div className="owner-canvas-actions">
        <button type="button" className={runtimeOpen ? 'active' : ''} onClick={() => { setRuntimeOpen(value => !value); setHistoryOpen(false) }}>Runtime{attentionWork.length ? <b>{attentionWork.length}</b> : null}</button>
        <button type="button" className={historyOpen ? 'active' : ''} onClick={() => { setHistoryOpen(value => !value); setRuntimeOpen(false) }}>History</button>
        <button type="button" onClick={() => createConversation.mutate()} disabled={createConversation.isPending}>New conversation</button>
      </div>
    </header>

    {runtimeOpen ? <aside className="owner-popover runtime-popover">
      {attentionWork.length ? <section><span className="owner-popover-label">Needs you</span>{attentionWork.slice(0, 4).map(item => <Link key={item.work_id} to={`/work/${item.work_id}`}><StatusLamp tone="red" /><span><strong>{item.objective}</strong><small>{item.status}</small></span></Link>)}</section> : null}
      {activeWork.length ? <section><span className="owner-popover-label">Active Work</span>{activeWork.map(item => <Link key={item.work_id} to={`/work/${item.work_id}`}><StatusLamp tone={workStateToLamp(item.status)} /><span><strong>{item.objective}</strong><small>{item.status}</small></span></Link>)}</section> : null}
      {enabledCadence.length ? <section><span className="owner-popover-label">Standing duties</span>{enabledCadence.map(item => <Link key={item.cadence_id} to="/cadence"><StatusLamp tone="blue" /><span><strong>{item.name}</strong><small>{item.objective}</small></span></Link>)}</section> : null}
      {!attentionWork.length && !activeWork.length && !enabledCadence.length ? <p>No active responsibilities right now.</p> : null}
      <footer><Link to="/work">Browse Work</Link><Link to="/cadence">Standing duties</Link></footer>
    </aside> : null}

    {historyOpen ? <aside className="owner-popover history-popover">
      <input aria-label="Search conversations" value={conversationFilter} onChange={event => setConversationFilter(event.target.value)} placeholder="Find a conversation" autoFocus />
      <div className="history-list">{visibleConversations.map(item => <div key={item.conversation_id} className={selected === item.conversation_id ? 'active' : ''}>
        <button type="button" aria-label={`${item.title} ${item.updated_at}`} onClick={() => { setSelected(item.conversation_id); setHistoryOpen(false); setAttachments([]) }}><strong>{item.title}</strong><small>{when(item.updated_at)}</small></button>
        <button type="button" aria-label={`Delete ${item.title}`} onClick={() => removeConversation(item)}>×</button>
      </div>)}{!visibleConversations.length ? <p>No matching conversations</p> : null}</div>
    </aside> : null}

    {attentionWork.length ? <div className="owner-attention-strip"><span>Attention</span><strong>{attentionWork[0].objective}</strong><Link to={`/work/${attentionWork[0].work_id}`}>Open Work →</Link>{attentionWork.length > 1 ? <small>+{attentionWork.length - 1} more</small> : null}</div> : null}

    <main className="owner-thread" aria-label="Conversation and runtime objects">
      <div className="owner-thread-inner">        {turns.map(turn => {
          const recorded = actionForTurn(turn)
          const objects = objectsForTurn(turn)
          const attached = attachmentsForTurn(turn)
          const tools = Array.isArray(turn.metadata?.tools_used) ? turn.metadata.tools_used.filter(value => typeof value === 'string') as string[] : []
          const traced = traceTurn === turn.turn_id
          return <article key={turn.turn_id} className={`owner-turn ${turn.role}`}>
            <header><span>{turn.role === 'user' ? 'You' : turn.role === 'assistant' ? 'Atlas' : turn.role}</span><time>{when(turn.created_at)}</time></header>
            <div className="owner-turn-body">
              {turn.content ? <div className="owner-turn-copy">{turn.content}</div> : null}
              {attached.length ? <div className="turn-artifacts">{attached.map(item => <ArtifactObject key={item.artifact_id} artifactId={item.artifact_id} summary={item} />)}</div> : null}
              {objects.length ? <div className="turn-runtime-objects">{objects.map(object => <RuntimeObject key={`${object.kind}:${object.id}`} object={object} />)}</div> : recorded && workflowVariant(recorded.capability_id) ? <WorkflowCard action={recorded} /> : null}
              {recorded || tools.length ? <button type="button" className="turn-trace-button" aria-expanded={traced} onClick={() => setTraceTurn(traced ? null : turn.turn_id)}>{traced ? 'Hide trace' : 'Trace'}</button> : null}
              {traced ? <div className="turn-trace"><dl>
                <div><dt>Turn</dt><dd>{turn.turn_id}</dd></div>
                {recorded ? <><div><dt>Action</dt><dd>{recorded.occurrence_id}</dd></div><div><dt>Authority</dt><dd>{recorded.policy_decision} · revision {recorded.policy_revision}</dd></div><div><dt>Scope</dt><dd>{recorded.scope}</dd></div></> : null}
              </dl>{tools.length ? <p>{tools.join(' · ')}</p> : null}<details><summary>Recorded metadata</summary><pre>{JSON.stringify(turn.metadata, null, 2)}</pre></details></div> : null}
            </div>
          </article>
        })}
        {send.isPending ? <div className="owner-working"><StatusLamp tone="amber" /><span>Atlas is working</span></div> : null}
        {detail.isError ? <p className="owner-error">{detail.error.message}</p> : null}
        <div ref={threadEndRef} aria-hidden />
      </div>
    </main>

    <footer className="owner-composer-wrap">
      <form className="owner-composer" onSubmit={submit} onDragOver={event => event.preventDefault()} onDrop={onDrop}>
        {attachments.length ? <div className="composer-attachments">{attachments.map(item => <ArtifactObject key={item.artifact_id} artifactId={item.artifact_id} summary={item} removable onRemove={() => setAttachments(current => current.filter(row => row.artifact_id !== item.artifact_id))} />)}</div> : null}
        {pendingFocus ? <div className="composer-focus">Focused on {pendingFocus.work_id ? 'Work' : 'standing duty'} <button type="button" onClick={() => setPendingFocus(null)}>Clear</button></div> : null}
        <textarea aria-label="Message" value={message} onChange={event => setMessage(event.target.value)} onKeyDown={submitFromKeyboard} placeholder="Ask Atlas…" />
        <div className="owner-composer-actions">
          <span>
            <input ref={fileInputRef} className="composer-file-input" type="file" multiple onChange={event => { if (event.target.files) void addFiles(event.target.files); event.target.value = '' }} />
            <button type="button" className="attach-button" disabled={uploading || attachments.length >= 8} onClick={() => fileInputRef.current?.click()}>{uploading ? 'Adding…' : '+ Attach'}</button>
            <small>Drop files here · max 30 MB each</small>
          </span>
          <button className="send-button" type="submit" aria-label="Send" disabled={send.isPending || uploading || (!message.trim() && !attachments.length) || !selectedValid}>{send.isPending ? 'Working…' : 'Send ↗'}</button>
        </div>
        {uploadError ? <p className="owner-error">{uploadError}</p> : null}
        {send.isError ? <p className="owner-error">{send.error.message}</p> : null}
      </form>
    </footer>
  </div>
}
