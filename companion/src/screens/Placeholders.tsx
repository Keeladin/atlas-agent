import { Panel } from '../ui/Panel'

export function Knowledge() {
  return (
    <Placeholder
      title="Knowledge"
      body="Browse and search will attach here. Indexing and grounded search run as Work capabilities, not a separate Companion runtime."
    />
  )
}

export function Files() {
  return (
    <Placeholder
      title="Files"
      body="File library and upload will feed Work inputs. Placeholder kept for navigation integrity."
    />
  )
}

export function Settings() {
  return (
    <Placeholder
      title="Settings"
      body="Provider inventory and host health will surface here. Mutations stay on the authenticated Atlas API."
    />
  )
}

function Placeholder({ title, body }: { title: string; body: string }) {
  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <h1 style={{ margin: 0 }}>{title}</h1>
      <Panel>
        <p style={{ color: 'var(--text-muted)', margin: 0 }}>{body}</p>
      </Panel>
    </div>
  )
}
