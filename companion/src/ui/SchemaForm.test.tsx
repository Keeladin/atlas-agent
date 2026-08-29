import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import type { JsonSchema } from '../api/types'
import { SchemaForm } from './SchemaForm'
import { requiredErrors } from './schemaFormModel'

const schema: JsonSchema = {
  type: 'object',
  required: ['name', 'mode'],
  properties: {
    name: { type: 'string' },
    mode: { type: 'string', enum: ['safe', 'fast'] },
    count: { type: 'integer' },
    enabled: { type: 'boolean' },
    tags: { type: 'array', items: { type: 'string' } },
    schedule: { type: 'object', properties: { start: { type: 'string', format: 'date-time' } } },
  },
}

function Harness() {
  const [value, setValue] = useState<Record<string, unknown>>({})
  return <><SchemaForm schema={schema} value={value} onChange={setValue} /><pre data-testid="payload">{JSON.stringify(value)}</pre></>
}

describe('SchemaForm', () => {
  it('renders common JSON Schema controls and keeps one payload model', () => {
    render(<Harness />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'demo' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Mode' }), { target: { value: '"safe"' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Count' }), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Enabled' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Tags' }), { target: { value: 'one\ntwo' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Start' }), { target: { value: '2026-08-29T20:00:00Z' } })
    expect(screen.getByRole('textbox', { name: 'Start' })).toHaveAttribute('placeholder', 'RFC 3339 date-time')
    expect(JSON.parse(screen.getByTestId('payload').textContent ?? '{}')).toEqual({ name: 'demo', mode: 'safe', count: 3, enabled: true, tags: ['one', 'two'], schedule: { start: '2026-08-29T20:00:00Z' } })
  })

  it('reports required fields without treating false or zero as missing', () => {
    expect(requiredErrors(schema, { name: 'demo', mode: 'safe', count: 0, enabled: false })).toEqual([])
    expect(requiredErrors(schema, {})).toEqual(['name', 'mode'])
  })
})
