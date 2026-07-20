import { ArrowDownUp, BriefcaseBusiness, ChevronDown, UserRound } from 'lucide-react'

export function JobNavigator() {
  return (
    <aside aria-label="Job navigation" className="job-navigator panel-region">
      <div className="sort-row">
        <button aria-label="Job ordering: Manual" className="sort-control" type="button">
          <span>Manual</span>
          <ChevronDown aria-hidden="true" size={14} strokeWidth={1.5} />
        </button>
        <button aria-label="Reverse job order" className="icon-button sort-direction" type="button">
          <ArrowDownUp aria-hidden="true" size={16} strokeWidth={1.5} />
        </button>
      </div>

      <div className="navigator-empty">
        <BriefcaseBusiness aria-hidden="true" size={22} strokeWidth={1.4} />
        <h2>No opportunities yet</h2>
        <p>Jobs will appear here when the shared job source is connected.</p>
      </div>

      <div className="profile-row">
        <span className="profile-avatar"><UserRound aria-hidden="true" size={18} strokeWidth={1.4} /></span>
        <span className="profile-copy">
          <strong>Jacobi Lange</strong>
          <small>Personal workspace</small>
        </span>
        <ChevronDown aria-hidden="true" size={15} strokeWidth={1.5} />
      </div>
    </aside>
  )
}
