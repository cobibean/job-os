import { Check, ChevronRight, CircleAlert, LoaderCircle, Pause } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'

import type { ActivityItem } from './useAgentConversation'
import { ActivityRow } from './ActivityRow'

interface AgentActivityGroupProps {
  activities: ActivityItem[]
  active: boolean
  working: boolean
  state: ActivityItem['state']
  onLayoutChange?: () => void
}

function actionCount(count: number): string {
  return `${count} ${count === 1 ? 'action' : 'actions'}`
}

function activitySummary(activities: ActivityItem[], active: boolean, working: boolean, state: ActivityItem['state']): string {
  const count = actionCount(activities.length)
  const completed = activities.filter(activity => activity.state === 'completed').length
  if (state === 'failed') return `${count} · failed`
  if (state === 'interrupted') return `${count} · interrupted`
  if (state === 'waiting') return `${count} · waiting`
  if (active && !working) return `${count} · stopping`
  if (active) return completed > 0 ? `${count} · ${completed} completed` : count
  return completed === activities.length ? `${count} completed` : `${count} · ${completed} completed`
}

function GroupStateIcon({ active, activitiesComplete, working, state }: Pick<AgentActivityGroupProps, 'active' | 'working' | 'state'> & { activitiesComplete: boolean }) {
  if (working) return <LoaderCircle aria-hidden="true" className="spin" size={14} />
  if (active) return <Pause aria-hidden="true" size={14} />
  if (state === 'failed') return <CircleAlert aria-hidden="true" size={14} />
  if (state === 'completed' && activitiesComplete) return <Check aria-hidden="true" size={14} />
  return <Pause aria-hidden="true" size={14} />
}

export function AgentActivityGroup({ activities, active, working, state, onLayoutChange }: AgentActivityGroupProps) {
  const [expanded, setExpanded] = useState(active)
  const bodyId = useId()
  const interacted = useRef(false)
  const wasActive = useRef(active)
  const activitiesComplete = activities.every(activity => activity.state === 'completed')
  const summary = activitySummary(activities, active, working, state)
  const presentationState = working
    ? 'working'
    : active && state !== 'waiting'
      ? 'stopping'
      : state === 'completed' && activitiesComplete
        ? 'completed'
        : state === 'failed'
          ? 'failed'
          : state === 'interrupted'
            ? 'interrupted'
            : state === 'waiting'
              ? 'waiting'
              : 'paused'

  useEffect(() => {
    if (wasActive.current && !active && !interacted.current) setExpanded(false)
    wasActive.current = active
  }, [active])

  useEffect(() => {
    onLayoutChange?.()
  }, [expanded, onLayoutChange])

  return (
    <section className={`agent-activity-group ${presentationState}`}>
      <button
        aria-controls={bodyId}
        aria-expanded={expanded}
        aria-label={`${expanded ? 'Hide' : 'Show'} agent activity: ${summary}`}
        className="agent-activity-toggle"
        onClick={() => {
          interacted.current = true
          setExpanded(value => !value)
        }}
        type="button"
      >
        <ChevronRight aria-hidden="true" className="activity-group-chevron" size={15} />
        <span className="activity-group-state"><GroupStateIcon active={active} activitiesComplete={activitiesComplete} state={state} working={working} /></span>
        <strong>Agent activity</strong>
        <span>{summary}</span>
      </button>
      {expanded && (
        <div className="agent-activity-list" id={bodyId}>
          {activities.map(activity => <ActivityRow item={activity} key={activity.id} />)}
        </div>
      )}
    </section>
  )
}
