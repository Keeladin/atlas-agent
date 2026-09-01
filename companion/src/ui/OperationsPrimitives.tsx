import type { ReactNode } from 'react'
import type { LampTone } from './operationState'

export function StatusLamp({ tone, label, title }: { tone: LampTone; label?: ReactNode; title?: string }) {
  return <span className="status-lamp-wrap" title={title}>
    <span className={`status-lamp ${tone}`} aria-hidden="true" />
    {label ? <span>{label}</span> : null}
  </span>
}

export function InspectorPanel({ title, eyebrow, status, children, actions }: {
  title: ReactNode
  eyebrow?: ReactNode
  status?: ReactNode
  children: ReactNode
  actions?: ReactNode
}) {
  return <section className="ops-inspector">
    <header className="ops-inspector-head">
      <div>{eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}<h2>{title}</h2></div>
      {status ? <div className="ops-inspector-status">{status}</div> : null}
    </header>
    <div className="ops-inspector-body">{children}</div>
    {actions ? <footer className="ops-inspector-actions actions">{actions}</footer> : null}
  </section>
}

export function InspectorSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="ops-inspector-section"><h3>{title}</h3>{children}</section>
}

export function OperationalRow({ active = false, lamp, label, secondary, meta, status, onClick }: {
  active?: boolean
  lamp?: LampTone
  label: ReactNode
  secondary?: ReactNode
  meta?: ReactNode
  status?: ReactNode
  onClick?: () => void
}) {
  const content = <>
    <span className="ops-row-leading">{lamp ? <StatusLamp tone={lamp} /> : null}<span className="ops-row-copy"><strong>{label}</strong>{secondary ? <small>{secondary}</small> : null}</span></span>
    {meta ? <span className="ops-row-meta">{meta}</span> : null}
    {status ? <span className="ops-row-status">{status}</span> : null}
  </>
  return onClick
    ? <button type="button" className={`ops-row ${active ? 'active' : ''}`} onClick={onClick}>{content}</button>
    : <div className={`ops-row ${active ? 'active' : ''}`}>{content}</div>
}

export function OperationalRibbon({ items }: { items: Array<{ label: string; value: ReactNode; tone?: LampTone; detail?: ReactNode }> }) {
  return <div className="ops-ribbon">{items.map(item => <div className="ops-ribbon-item" key={item.label}>
    {item.tone ? <StatusLamp tone={item.tone} /> : null}
    <span><small>{item.label}</small><strong>{item.value}</strong>{item.detail ? <em>{item.detail}</em> : null}</span>
  </div>)}</div>
}

export function FactList({ items }: { items: Array<{ label: string; value: ReactNode; mono?: boolean }> }) {
  return <dl className="ops-facts">{items.map(item => <div key={item.label}><dt>{item.label}</dt><dd className={item.mono ? 'mono' : ''}>{item.value}</dd></div>)}</dl>
}
