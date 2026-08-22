import { describe, expect, it } from 'vitest'
import { nextActiveAfterDelete } from './conversationOwnership'

const conversations = [{ id: 'first' }, { id: 'active' }, { id: 'last' }]

describe('nextActiveAfterDelete', () => {
  it('preserves the active conversation when another conversation is deleted', () => {
    expect(nextActiveAfterDelete('first', conversations, 'active')).toBe('active')
  })

  it('falls back to the first remaining conversation when active is deleted', () => {
    expect(nextActiveAfterDelete('active', conversations, 'active')).toBe('first')
  })

  it('clears the active conversation when the last conversation is deleted', () => {
    expect(nextActiveAfterDelete('active', [{ id: 'active' }], 'active')).toBeNull()
  })

  it('keeps a null active selection', () => {
    expect(nextActiveAfterDelete('first', conversations, null)).toBeNull()
  })
})
