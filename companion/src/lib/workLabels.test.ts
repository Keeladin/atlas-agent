import { describe, expect, it } from 'vitest'
import { workDetail } from '../test/workFixture'
import {
  humanCapabilityLabel,
  humanWorkStatus,
  isExecutableRun,
  needsAttention,
  runActionLabel,
  stepTone,
} from './workLabels'

describe('work labels and controls', () => {
  it.each([
    ['waiting_confirmation', 'Needs confirmation', 'confirm'],
    ['waiting_authority', 'Needs approval', 'auth'],
    ['running', "I'm on it", 'running'],
  ])('prioritizes phase %s over the stored status', (phase, label, tone) => {
    expect(humanWorkStatus({ status: 'failed', phase })).toEqual({ label, tone })
  })

  it.each([
    ['completed', 'Done', 'done'],
    ['failed', "Couldn't finish", 'failed'],
    ['cancelled', 'Cancelled', 'waiting'],
  ])('renders terminal status %s honestly', (status, label, tone) => {
    expect(humanWorkStatus({ status })).toEqual({ label, tone })
  })

  it('never offers run while a decision is pending', () => {
    const detail = workDetail({
      phase: 'waiting_confirmation',
      actions: ['run'],
    })
    expect(needsAttention(detail)).toBe(true)
    expect(isExecutableRun(detail)).toBe(false)
    expect(runActionLabel(detail)).toBeNull()
  })

  it('labels planned work as Start and paused work as Resume', () => {
    expect(runActionLabel(workDetail({ status: 'planned', phase: 'planned', actions: ['run'] }))).toBe('Start')
    expect(runActionLabel(workDetail({ phase: 'paused', actions: ['run'] }))).toBe('Resume')
  })

  it('maps step and capability labels without inventing unknown names', () => {
    expect(stepTone('failed')).toBe('failed')
    expect(humanCapabilityLabel('communication.email.send')).toBe('Send an email')
    expect(humanCapabilityLabel('custom.operation')).toBe('custom · operation')
  })
})
