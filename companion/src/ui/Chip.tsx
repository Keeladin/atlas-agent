import type { ReactNode } from 'react'

const TONE: Record<string, string> = {
  waiting: 'waiting',
  running: 'running',
  done: 'done',
  failed: 'failed',
  confirm: 'confirm',
  auth: 'auth',
}

export function Chip({
  tone = '',
  children,
}: {
  tone?: keyof typeof TONE | string
  children: ReactNode
}) {
  const cls = TONE[tone] || ''
  return <span className={`chip ${cls}`.trim()}>{children}</span>
}
