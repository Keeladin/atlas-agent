import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { TaskBrief, WorkDetail } from '../api/types'
import { Panel } from '../ui/Panel'

export function WorkNew() {
  const navigate = useNavigate()
  const [objective, setObjective] = useState('')
  const [notes, setNotes] = useState('')
  const [brief, setBrief] = useState<TaskBrief | null>(null)
  const [authority, setAuthority] = useState('read')
  const [inputsJson, setInputsJson] = useState('{}')
  const [error, setError] = useState<string | null>(null)

  const briefMutation = useMutation({
    mutationFn: () =>
      api<TaskBrief>('/api/advanced/brief', {
        method: 'POST',
        body: JSON.stringify({ objective, notes: notes || null }),
      }),
    onSuccess: (data) => {
      setBrief(data)
      setAuthority(data.required_authority)
      setError(null)
    },
    onError: (err: Error) => setError(err.message),
  })

  const acceptMutation = useMutation({
    mutationFn: async () => {
      if (!brief) throw new Error('Create a TaskBrief first')
      const inputs = JSON.parse(inputsJson || '{}')
      return api<WorkDetail>('/api/work', {
        method: 'POST',
        body: JSON.stringify({
          brief,
          authority_scope: authority,
          inputs,
        }),
      })
    },
    onSuccess: (detail) => navigate(`/work/${detail.work_id}`),
    onError: (err: Error) => setError(err.message),
  })

  function onPlan(event: FormEvent) {
    event.preventDefault()
    briefMutation.mutate()
  }

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <div>
        <h1 style={{ margin: 0 }}>Plan Work</h1>
        <p style={{ color: 'var(--text-muted)' }}>
          AdvancedRuntime produces a TaskBrief. Accepting it creates Work — Advanced stops at the brief.
        </p>
      </div>
      <Panel title="Intent">
        <form onSubmit={onPlan} style={{ display: 'grid', gap: '0.75rem' }}>
          <label>
            Objective
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              required
            />
          </label>
          <label>
            Notes
            <textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
          </label>
          <button className="primary" type="submit" disabled={briefMutation.isPending}>
            {briefMutation.isPending ? 'Planning…' : 'Create TaskBrief'}
          </button>
        </form>
      </Panel>
      {brief ? (
        <Panel title="TaskBrief review">
          <pre
            style={{
              whiteSpace: 'pre-wrap',
              fontFamily: 'var(--mono)',
              fontSize: '0.85rem',
            }}
          >
            {JSON.stringify(brief, null, 2)}
          </pre>
          <label>
            Authority scope for accept
            <input value={authority} onChange={(event) => setAuthority(event.target.value)} />
          </label>
          <label>
            Capability inputs (JSON)
            <textarea
              value={inputsJson}
              onChange={(event) => setInputsJson(event.target.value)}
            />
          </label>
          <button
            className="primary"
            type="button"
            disabled={acceptMutation.isPending}
            onClick={() => acceptMutation.mutate()}
          >
            Accept into Work
          </button>
        </Panel>
      ) : null}
      {error ? <p style={{ color: 'var(--danger)' }}>{error}</p> : null}
    </div>
  )
}
