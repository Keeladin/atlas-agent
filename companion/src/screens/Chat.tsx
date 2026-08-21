import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, UIEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Conversation } from '../api/types'
import { Inspect } from '../ui/Inspect'
import { Panel } from '../ui/Panel'

const NEAR_BOTTOM_PX = 72

export function Chat() {
  const queryClient = useQueryClient()
  const [activeId, setActiveId] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [stickToBottom, setStickToBottom] = useState(true)
  const [showJump, setShowJump] = useState(false)
  const threadRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const bootstrapped = useRef(false)

  const listQuery = useQuery({
    queryKey: ['conversations'],
    queryFn: () =>
      api<{ conversations: Conversation[] }>('/api/chat/conversations'),
  })

  const conversationQuery = useQuery({
    queryKey: ['conversation', activeId],
    queryFn: () => api<Conversation>(`/api/chat/conversations/${activeId}`),
    enabled: Boolean(activeId),
  })

  const scrollToLatest = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const el = threadRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior })
    setStickToBottom(true)
    setShowJump(false)
  }, [])

  const focusComposer = useCallback(() => {
    requestAnimationFrame(() => composerRef.current?.focus())
  }, [])

  const selectConversation = useCallback(
    (id: string, options?: { focus?: boolean }) => {
      setActiveId(id)
      setStickToBottom(true)
      setShowJump(false)
      if (options?.focus) focusComposer()
    },
    [focusComposer],
  )

  useEffect(() => {
    if (!stickToBottom) {
      setShowJump(true)
      return
    }
    scrollToLatest('auto')
  }, [
    activeId,
    conversationQuery.data?.turns?.length,
    stickToBottom,
    scrollToLatest,
  ])

  const createMutation = useMutation({
    mutationFn: () =>
      api<Conversation>('/api/chat/conversations', {
        method: 'POST',
        body: JSON.stringify({ title: 'Chat' }),
      }),
    onSuccess: (conversation) => {
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      selectConversation(conversation.id, { focus: true })
    },
  })

  const createConversation = createMutation.mutate
  const creating = createMutation.isPending

  // Prefer most recent conversation; otherwise open a blank ready-to-type chat.
  useEffect(() => {
    if (bootstrapped.current || activeId || !listQuery.isSuccess) return
    const list = listQuery.data?.conversations || []
    if (list.length > 0) {
      bootstrapped.current = true
      selectConversation(list[0].id)
      return
    }
    if (creating) return
    bootstrapped.current = true
    createConversation()
  }, [
    activeId,
    creating,
    createConversation,
    listQuery.isSuccess,
    listQuery.data?.conversations,
    selectConversation,
  ])

  function onThreadScroll(event: UIEvent<HTMLDivElement>) {
    const el = event.currentTarget
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    const nearBottom = distance <= NEAR_BOTTOM_PX
    setStickToBottom(nearBottom)
    setShowJump(!nearBottom)
  }

  const sendMutation = useMutation({
    mutationFn: () =>
      api(`/api/chat/conversations/${activeId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      }),
    onSuccess: () => {
      setMessage('')
      setStickToBottom(true)
      void queryClient.invalidateQueries({ queryKey: ['conversation', activeId] })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      focusComposer()
    },
  })

  function onSend(event: FormEvent) {
    event.preventDefault()
    if (!activeId || !message.trim()) return
    sendMutation.mutate()
  }

  const turns = conversationQuery.data?.turns || []
  const ready = Boolean(activeId)

  return (
    <div className="chat-page">
      <div className="chat-page-header">
        <h1>Chat</h1>
        <p>
          Talk with Atlas. Start work from the left rail when talk becomes a
          responsibility.
        </p>
      </div>

      {listQuery.isError || conversationQuery.isError ? (
        <div className="offline-banner">
          Could not reach chat. Check your connection or sign in again.
        </div>
      ) : null}

      <div className="chat-layout">
        <Panel className="chat-rail" title="Conversations">
          <div className="chat-rail-actions">
            <button
              className="primary"
              type="button"
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
            >
              New chat
            </button>
            <Link to="/work/new">
              <button className="primary" type="button">
                Start work from this
              </button>
            </Link>
          </div>
          <h2 className="chat-section-title">Recents</h2>
          <div className="chat-rail-scroll">
            {(listQuery.data?.conversations || []).map((item) => (
              <button
                key={item.id}
                type="button"
                className="ghost chat-recent-item"
                onClick={() => selectConversation(item.id)}
                style={{
                  background:
                    item.id === activeId
                      ? 'rgba(110, 168, 255, 0.12)'
                      : 'transparent',
                }}
              >
                <strong>{item.title}</strong>
                <span className="meta">{item.turn_count} messages</span>
              </button>
            ))}
            {!listQuery.data?.conversations?.length && !createMutation.isPending ? (
              <p className="empty">No conversations yet.</p>
            ) : null}
          </div>
        </Panel>

        <Panel className="chat-thread-pane">
          <div className="chat-thread-body">
            {!ready ? (
              <p className="empty">Opening chat…</p>
            ) : (
              <>
                <div className="chat-thread-title">
                  <strong>
                    {conversationQuery.data?.title || 'Conversation'}
                  </strong>
                </div>
                <div className="chat-thread-wrap">
                  <div
                    className="thread"
                    ref={threadRef}
                    onScroll={onThreadScroll}
                  >
                    {turns.map((turn) => (
                      <div
                        key={turn.id}
                        className={`bubble ${turn.role === 'user' ? 'user' : ''}`}
                      >
                        <div className="who">
                          {turn.role === 'user' ? 'You' : 'Atlas'}
                        </div>
                        {turn.content}
                      </div>
                    ))}
                    {!turns.length ? (
                      <p className="empty">Say hello to start.</p>
                    ) : null}
                    {sendMutation.isError ? (
                      <p className="error-text">
                        {(sendMutation.error as Error).message}
                      </p>
                    ) : null}
                  </div>
                  {showJump ? (
                    <button
                      type="button"
                      className="jump-latest"
                      onClick={() => scrollToLatest('smooth')}
                    >
                      Jump to latest
                    </button>
                  ) : null}
                </div>
                <form className="composer" onSubmit={onSend}>
                  <textarea
                    ref={composerRef}
                    value={message}
                    onChange={(event) => setMessage(event.target.value)}
                    placeholder="Message Atlas…"
                    required
                  />
                  <button
                    className="primary"
                    type="submit"
                    disabled={sendMutation.isPending}
                  >
                    Send
                  </button>
                </form>
              </>
            )}
          </div>
        </Panel>

        <Panel className="chat-context-pane" title="Context">
          <div className="chat-context-scroll">
            <p className="meta" style={{ marginTop: 0 }}>
              Helpful next steps — not runtime noise.
            </p>
            <div className="list-row">
              <div>
                <strong>Suggested next step</strong>
                <div className="meta">Start work when ready</div>
              </div>
            </div>
            <div className="list-row">
              <div>
                <strong>Bridge</strong>
                <div className="meta">Turn this chat into durable Work</div>
              </div>
              <Link to="/work/new">
                <button className="ghost" type="button">
                  Plan
                </button>
              </Link>
            </div>
            <Inspect label="Inspect conversation details">
              {JSON.stringify(
                {
                  conversation_id: activeId,
                  turn_count: turns.length,
                },
                null,
                2,
              )}
            </Inspect>
          </div>
        </Panel>
      </div>
    </div>
  )
}
