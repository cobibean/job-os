import { Bot, BriefcaseBusiness, ChevronDown, Send, SlidersHorizontal } from 'lucide-react'

export function AgentPanel() {
  return (
    <aside aria-label="Agent chat" className="agent-panel panel-region">
      <div className="agent-context">
        <span><BriefcaseBusiness aria-hidden="true" size={16} strokeWidth={1.5} /> No active job</span>
        <ChevronDown aria-hidden="true" size={14} strokeWidth={1.5} />
        <button aria-label="Agent context settings" className="icon-button context-settings placeholder-control" disabled title="Available in a later phase" type="button">
          <SlidersHorizontal aria-hidden="true" size={16} strokeWidth={1.5} />
        </button>
      </div>

      <section className="agent-empty">
        <span className="agent-avatar"><Bot aria-hidden="true" size={22} strokeWidth={1.45} /></span>
        <h2>One continuous conversation</h2>
        <p>Your job-hunter conversation will stay here as the active job changes.</p>
      </section>

      <div className="composer" aria-label="Agent message composer">
        <span className="composer-placeholder">Message the agent…</span>
        <button aria-label="Send message" className="send-button placeholder-control" disabled title="Available in Phase 6" type="button">
          <Send aria-hidden="true" size={17} strokeWidth={1.5} />
        </button>
      </div>
    </aside>
  )
}
