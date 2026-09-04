import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Cadence } from '../api/types'
import { StatusLamp } from './OperationsPrimitives'
import { cadenceStateToLamp } from './operationState'
import { scheduleLabel } from './workflowPresentation'

export function CadenceObject({ cadenceId }: { cadenceId: string }) {
  const qc = useQueryClient()
  const rows = useQuery({
    queryKey: ['cadence'],
    queryFn: () => api<{ cadences: Cadence[] }>('/api/cadence'),
    refetchInterval: 10000,
  })
  const cadence = rows.data?.cadences.find(item => item.cadence_id === cadenceId)
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api<Cadence>(`/api/cadence/${cadenceId}/enabled`, { method: 'POST', body: JSON.stringify({ enabled }) }),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ['cadence'] }) },
  })
  if (!cadence) return null
  return <section className="runtime-object cadence-object">
    <header className="work-object-head">
      <StatusLamp tone={cadenceStateToLamp(cadence.enabled, cadence.next_run_at)} />
      <span className="runtime-object-copy"><small>Standing duty</small><strong>{cadence.name}</strong></span>
      <span className="work-object-status">{cadence.enabled ? 'enabled' : 'disabled'}</span>
    </header>
    <div className="cadence-object-body"><p>{cadence.objective}</p><span>{scheduleLabel(cadence.schedule)}{cadence.next_run_at ? ` · next ${new Date(cadence.next_run_at).toLocaleString()}` : ''}</span></div>
    <footer className="runtime-object-footer">
      <Link to="/cadence">Open standing duties</Link>
      <button type="button" disabled={toggle.isPending} onClick={() => toggle.mutate(!cadence.enabled)}>{cadence.enabled ? 'Disable' : 'Enable'}</button>
    </footer>
  </section>
}
