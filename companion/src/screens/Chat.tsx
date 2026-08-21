import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Conversation } from '../api/types'
import { Panel } from '../ui/Panel'

export function Chat() {
  const queryClient = useQueryClient()
  const [activeId, setActiveId] = useState<string | null>(null)
  const [message, setMessage] = useState('')

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

  const createMutation = useMutation({
    mutationFn: () =>
      api<Conversation>('/api/chat/conversations', {
        method: 'POST',
        body: JSON.stringify({ title: 'Chat' }),
      }),
    onSuccess: (conversation) => {
      setActiveId(conversation.id)
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  const sendMutation = useMutation({
    mutationFn: () =>
      api(`/api/chat/conversations/${activeId}/messages`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      }),
    onSuccess: () => {
      setMessage('')
      void queryClient.invalidateQueries({ queryKey: ['conversation', activeId] })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })

  function onSend(event: FormEvent) {
    event.preventDefault()
    if (!activeId) return
    sendMutation.mutate()
  }

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: '1rem',
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h1 style={{ margin: 0 }}>Chat</h1>
          <p style={{ color: 'var(--text-muted)' }}>
            ChatRuntime only. Use Plan Work to move an intent into Advanced → Work.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button type="button" onClick={() => createMutation.mutate()}>
            New conversation
          </button>
          <Link to="/work/new">
            <button className="primary" type="button">
              Plan Work
            </button>
          </Link>
        </div>
      </div>
      <div
        style={{
          display: 'grid',
          gap: '1rem',
          gridTemplateColumns: 'minmax(180px, 240px) minmax(0, 1fr)',
        }}
      >
        <Panel title="Conversations">
          <div style={{ display: 'grid', gap: '0.4rem' }}>
            {(listQuery.data?.conversations || []).map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveId(item.id)}
                style={{
                  textAlign: 'left',
                  background:
                    item.id === activeId
                      ? 'rgba(91, 140, 255, 0.18)'
                      : 'var(--bg-elevated)',
                }}
              >
                {item.title}
              </button>
            ))}
          </div>
        </Panel>
        <Panel title={conversationQuery.data?.title || 'Transcript'}>
          {!activeId ? (
            <p style={{ color: 'var(--text-muted)' }}>
              Select or create a conversation.
            </p>
          ) : (
            <>
              <div style={{ display: 'grid', gap: '0.65rem', marginBottom: '1rem' }}>
                {(conversationQuery.data?.turns || []).map((turn) => (
                  <div
                    key={turn.id}
                    style={{
                      padding: '0.75rem',
                      borderRadius: 12,
                      background:
                        turn.role === 'user'
                          ? 'rgba(91, 140, 255, 0.12)'
                          : 'var(--bg-elevated)',
                      border: '1px solid var(--border)',
                    }}
                  >
                    <div
                      style={{
                        fontSize: '0.75rem',
                        color: 'var(--text-muted)',
                        marginBottom: '0.35rem',
                      }}
                    >
                      {turn.role}
                    </div>
                    <div style={{ whiteSpace: 'pre-wrap' }}>{turn.content}</div>
                  </div>
                ))}
              </div>
              <form onSubmit={onSend} style={{ display: 'grid', gap: '0.5rem' }}>
                <textarea
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
        </Panel>
      </div>
    </div>
  )
}
