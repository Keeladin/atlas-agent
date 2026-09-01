import { describe, expect, it } from 'vitest'
import { cadenceStateToLamp, reviewStateToLamp, runtimeStateToLamp, workStateToLamp } from './operationState'

describe('operation state lamps', () => {
  it('maps only known runtime states to semantic tones', () => {
    expect(workStateToLamp('completed')).toBe('green')
    expect(workStateToLamp('active')).toBe('amber')
    expect(workStateToLamp('pending_confirmation')).toBe('red')
    expect(workStateToLamp('made-up-risk')).toBe('dim')
    expect(runtimeStateToLamp('succeeded')).toBe('green')
    expect(runtimeStateToLamp('unknown')).toBe('dim')
  })

  it('keeps review state distinct from runtime lifecycle', () => {
    expect(reviewStateToLamp('approved')).toBe('green')
    expect(reviewStateToLamp('reviewed')).toBe('amber')
    expect(reviewStateToLamp('rejected')).toBe('red')
    expect(reviewStateToLamp()).toBe('dim')
  })

  it('uses only enabled state and a known overdue timestamp for cadence', () => {
    expect(cadenceStateToLamp(false)).toBe('dim')
    expect(cadenceStateToLamp(true)).toBe('green')
    expect(cadenceStateToLamp(true, '2000-01-01T00:00:00Z')).toBe('amber')
  })
})
