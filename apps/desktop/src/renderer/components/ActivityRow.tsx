import { Check, ChevronRight, CircleAlert, LoaderCircle, Pause } from 'lucide-react'
import { useId, useState } from 'react'

import type { ActivityItem } from '../hooks/useAgentConversation'

const hiddenDetailKeys = new Set(['activity_id', 'phase', 'type', 'redacted', 'redactions'])

function readableKey(key: string): string {
  return key.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

function readableValue(value: ActivityItem['detail'][string]): string {
  if (typeof value === 'string') return value
  if (value === null) return 'None'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function StateIcon({ state }: { state: ActivityItem['state'] }) {
  if (state === 'completed') return <Check aria-hidden="true" size={14} />
  if (state === 'failed') return <CircleAlert aria-hidden="true" size={14} />
  if (state === 'waiting') return <Pause aria-hidden="true" size={14} />
  return <LoaderCircle aria-hidden="true" className="spin" size={14} />
}

export function ActivityRow({ item }: { item: ActivityItem }) {
  const [expanded, setExpanded] = useState(false)
  const detailsId = useId()
  const details = Object.entries(item.detail).filter(([key]) => !hiddenDetailKeys.has(key))
  const redacted = item.detail.redacted === true || Array.isArray(item.detail.redactions)
  return (
    <article className={`activity-row ${item.state}`} data-testid="agent-activity-row">
      <button
        aria-controls={detailsId}
        aria-expanded={expanded}
        aria-label={`${expanded ? 'Hide' : 'Show'} details for ${item.label}`}
        className="activity-summary"
        onClick={() => setExpanded(value => !value)}
        type="button"
      >
        <ChevronRight aria-hidden="true" className="activity-chevron" size={14} />
        <span className="activity-state"><StateIcon state={item.state} /></span>
        <span className="activity-label">{item.label}</span>
        <span className="activity-status">{item.state}</span>
      </button>
      {expanded && (
        <div className="activity-details" id={detailsId}>
          {details.map(([key, value]) => (
            <div className="activity-detail" key={key}>
              <span>{readableKey(key)}</span>
              <code>{readableValue(value)}</code>
            </div>
          ))}
          {redacted && <p className="redaction-note">Sensitive detail was redacted.</p>}
        </div>
      )}
    </article>
  )
}
