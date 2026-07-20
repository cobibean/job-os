import { FileText, FolderOpen, Globe2, Plus, Search } from 'lucide-react'

export function CenterWorkspace({ activeSurface = 'browser' }: { activeSurface?: 'browser' | 'document' }) {
  return (
    <main className="center-workspace panel-region">
      <div className="workspace-tabs" role="tablist" aria-label="Open workspace surfaces">
        <button aria-label="Browser" aria-selected="true" className="surface-tab active placeholder-control" disabled role="tab" title="Available in a later phase" type="button">
          <Globe2 aria-hidden="true" size={15} strokeWidth={1.5} />
          {activeSurface === 'document' ? 'Document review' : 'Browser research'}
        </button>
        <button aria-label="Open a new surface" className="icon-button tab-add placeholder-control" disabled title="Available in a later phase" type="button">
          <Plus aria-hidden="true" size={16} strokeWidth={1.5} />
        </button>
      </div>

      <div className="surface-toolbar">
        <span className="toolbar-label"><FolderOpen aria-hidden="true" size={15} strokeWidth={1.5} /> Open</span>
        <span className="toolbar-label"><FileText aria-hidden="true" size={15} strokeWidth={1.5} /> Document</span>
      </div>

      <section className="workspace-empty">
        <span className="empty-orbit"><Search aria-hidden="true" size={23} strokeWidth={1.35} /></span>
        <h1>Your workbench is ready</h1>
        <p>Browser and document surfaces will open here without replacing your active context.</p>
        <div className="empty-shortcuts" aria-label="Available surfaces">
          <span>Browser</span>
          <span>Documents</span>
          <span>One persistent workspace</span>
        </div>
      </section>
    </main>
  )
}
