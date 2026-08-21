import { Panel } from '../ui/Panel'

export function Knowledge() {
  return (
    <Placeholder
      title="Knowledge"
      body="Browse and search will live here. Indexing and grounded answers still run as Work — this screen is the library, not a second runtime."
    />
  )
}

export function Files() {
  return (
    <Placeholder
      title="Files"
      body="Upload and attach files for Work inputs. Indexing into knowledge remains a Work action."
    />
  )
}

export function Settings() {
  return (
    <Placeholder
      title="Settings"
      body="Host health and provider inventory will surface here. Mutations stay on the authenticated Atlas API."
    />
  )
}

function Placeholder({ title, body }: { title: string; body: string }) {
  return (
    <div className="stack">
      <div className="topbar">
        <div>
          <h1>{title}</h1>
          <p>{body}</p>
        </div>
      </div>
      <Panel>
        <p className="empty" style={{ margin: 0 }}>
          Coming next — navigation is ready.
        </p>
      </Panel>
    </div>
  )
}
