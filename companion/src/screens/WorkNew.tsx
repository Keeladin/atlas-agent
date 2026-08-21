import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { TaskBrief, WorkDetail } from '../api/types'
import { Inspect } from '../ui/Inspect'
import { Panel } from '../ui/Panel'

const AUTHORITIES = [
  'read',
  'interpret',
  'recommend',
  'modify_internal',
  'communicate',
  'execute_external',
]

export function WorkNew() {
  const navigate = useNavigate()
  const [objective, setObjective] = useState('')
  const [notes, setNotes] = useState('')
  const [brief, setBrief] = useState<TaskBrief | null>(null)
  const [authority, setAuthority] = useState('read')
  const [to, setTo] = useState('')
  const [subject, setSubject] = useState('')
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
      if (!brief) throw new Error('Create a plan first')
      const inputs: Record<string, Record<string, string>> = {}
      if (brief.capabilities.includes('communication.email.send')) {
        inputs['communication.email.send'] = {
          ...(to ? { to } : {}),
          ...(subject ? { subject } : {}),
        }
      }
      return api<WorkDetail>('/api/work', {
        method: 'POST',
        body: JSON.stringify({
          brief,
          authority_scope: authority,
          inputs: Object.keys(inputs).length ? inputs : undefined,
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
    <div className="stack">
      <div className="topbar">
        <div>
          <h1>Plan work</h1>
          <p>
            Describe what Atlas should own. Review the plan, grant the right
            permission, then accept it into durable work.
          </p>
        </div>
      </div>

      <div className="grid-2">
        <Panel title="1 · Intent">
          <form onSubmit={onPlan}>
            <div className="field">
              <label htmlFor="objective">What should Atlas do?</label>
              <textarea
                id="objective"
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="notes">Notes for planning</label>
              <textarea
                id="notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </div>
            <button
              className="primary"
              type="submit"
              disabled={briefMutation.isPending}
            >
              {briefMutation.isPending ? 'Planning…' : 'Create plan'}
            </button>
          </form>
        </Panel>

        <Panel title="2 · Review plan">
          {!brief ? (
            <p className="empty">Create a plan to review it here.</p>
          ) : (
            <>
              <div className="brief-row">
                <span>Objective</span>
                <div>{brief.objective}</div>
              </div>
              <div className="brief-row">
                <span>Will do</span>
                <div>{brief.capabilities.join(', ')}</div>
              </div>
              <div className="brief-row">
                <span>Permission</span>
                <div>{brief.required_authority}</div>
              </div>
              <div className="brief-row">
                <span>Expected result</span>
                <div>{brief.expected_effect}</div>
              </div>
              <div className="brief-row">
                <span>Constraints</span>
                <div>
                  {brief.constraints.length
                    ? brief.constraints.join(' · ')
                    : 'None'}
                </div>
              </div>

              <div className="field" style={{ marginTop: '1rem' }}>
                <label htmlFor="authority">Authority for this work</label>
                <select
                  id="authority"
                  value={authority}
                  onChange={(event) => setAuthority(event.target.value)}
                >
                  {AUTHORITIES.map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </select>
              </div>

              {brief.capabilities.includes('communication.email.send') ? (
                <>
                  <div className="field">
                    <label htmlFor="to">Recipient</label>
                    <input
                      id="to"
                      value={to}
                      onChange={(event) => setTo(event.target.value)}
                      placeholder="ops@example.invalid"
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="subject">Subject</label>
                    <input
                      id="subject"
                      value={subject}
                      onChange={(event) => setSubject(event.target.value)}
                      placeholder="Weekly ops report"
                    />
                  </div>
                </>
              ) : null}

              <div className="actions">
                <button
                  className="primary"
                  type="button"
                  disabled={acceptMutation.isPending}
                  onClick={() => acceptMutation.mutate()}
                >
                  Accept into Work
                </button>
                <button
                  type="button"
                  onClick={() => briefMutation.mutate()}
                  disabled={briefMutation.isPending}
                >
                  Revise plan
                </button>
              </div>
              <Inspect label="Inspect technical plan">
                {JSON.stringify(brief, null, 2)}
              </Inspect>
            </>
          )}
        </Panel>
      </div>
      {error ? <p className="error-text">{error}</p> : null}
    </div>
  )
}
