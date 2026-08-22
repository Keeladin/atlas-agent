import { Panel } from '../ui/Panel'
import { Workspace, WorkspaceRailSection } from '../ui/Workspace'

export function Knowledge() {
  return (
    <Workspace
      title="Knowledge"
      subtitle="Browse and search will live here. Indexing and grounded answers still run as Work — this screen is the library, not a second runtime."
      railLabel="Browse"
      contextLabel="Provenance"
      rail={
        <Panel>
          <div className="workspace-rail-actions">
            <button className="primary" type="button" disabled>
              Search knowledge
            </button>
          </div>
          <WorkspaceRailSection title="Collections">
            <p className="empty" style={{ margin: 0 }}>
              Collections arrive with the knowledge browser.
            </p>
          </WorkspaceRailSection>
          <WorkspaceRailSection title="Filters">
            <p className="meta" style={{ margin: 0 }}>
              Source, date, and topic filters will sit here.
            </p>
          </WorkspaceRailSection>
        </Panel>
      }
      context={
        <div className="stack">
          <Panel title="Provenance">
            <p className="empty" style={{ margin: 0 }}>
              Source and ingest details appear when a document is selected.
            </p>
          </Panel>
          <Panel title="Related">
            <p className="meta" style={{ margin: 0 }}>
              Linked Work and related knowledge will surface here.
            </p>
          </Panel>
          <Panel title="Inspect">
            <p className="meta" style={{ margin: 0 }}>
              Technical identifiers stay behind Inspect once content lands.
            </p>
          </Panel>
        </div>
      }
    >
      <Panel title="Library">
        <p className="empty" style={{ margin: 0 }}>
          Documents, results, and the reader will occupy this centre pane.
        </p>
      </Panel>
    </Workspace>
  )
}

export function Files() {
  return (
    <Workspace
      title="Files"
      subtitle="Upload and attach files for Work inputs. Indexing into knowledge remains a Work action."
      railLabel="Locations"
      contextLabel="Details"
      rail={
        <Panel>
          <div className="workspace-rail-actions">
            <button className="primary" type="button" disabled>
              Upload
            </button>
          </div>
          <WorkspaceRailSection title="Locations">
            <p className="empty" style={{ margin: 0 }}>
              Host folders and attachments will list here.
            </p>
          </WorkspaceRailSection>
          <WorkspaceRailSection title="Recent">
            <p className="meta" style={{ margin: 0 }}>
              Recent files will appear after uploads land.
            </p>
          </WorkspaceRailSection>
        </Panel>
      }
      context={
        <div className="stack">
          <Panel title="Metadata">
            <p className="empty" style={{ margin: 0 }}>
              Select a file to see size, type, and path.
            </p>
          </Panel>
          <Panel title="Actions">
            <p className="meta" style={{ margin: 0 }}>
              Attach to Work or index into Knowledge from here.
            </p>
          </Panel>
          <Panel title="Links">
            <p className="meta" style={{ margin: 0 }}>
              Linked Work and Knowledge stay in this rail.
            </p>
          </Panel>
        </div>
      }
    >
      <Panel title="Browser">
        <p className="empty" style={{ margin: 0 }}>
          File browser and preview will occupy this centre pane.
        </p>
      </Panel>
    </Workspace>
  )
}

export function Settings() {
  return (
    <Workspace
      title="Settings"
      subtitle="Host health and provider inventory will surface here. Mutations stay on the authenticated Atlas API."
      contextLabel="Host"
      context={
        <Panel title="Host">
          <p className="meta" style={{ margin: 0 }}>
            Session, listen address, and provider status will land in this
            detail rail.
          </p>
        </Panel>
      }
    >
      <Panel title="Preferences">
        <p className="empty" style={{ margin: 0 }}>
          Settings sections will occupy this centre pane. Navigation stays in
          the global Atlas rail.
        </p>
      </Panel>
    </Workspace>
  )
}
