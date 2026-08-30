import { ChevronDown } from 'lucide-react'
import { type ReactNode, useState } from 'react'

interface SettingsSectionProps {
  children: ReactNode
  className?: string
  description?: string
  id: string
  title: string
}

export function SettingsSection({ children, className = '', description, id, title }: SettingsSectionProps) {
  const [expanded, setExpanded] = useState(false)
  const headingId = `${id}-heading`
  const contentId = `${id}-content`

  return (
    <section
      aria-labelledby={headingId}
      className={`settings-section settings-disclosure${className ? ` ${className}` : ''}`}
    >
      <button
        aria-controls={contentId}
        aria-expanded={expanded}
        className="settings-disclosure-toggle"
        onClick={() => setExpanded(current => !current)}
        type="button"
      >
        <span className="settings-section-title" id={headingId}>{title}</span>
        <ChevronDown
          aria-hidden="true"
          className={`settings-disclosure-chevron${expanded ? ' expanded' : ''}`}
          size={16}
          strokeWidth={1.75}
        />
      </button>
      <div className="settings-disclosure-content" hidden={!expanded} id={contentId}>
        {description ? <p className="settings-section-hint">{description}</p> : null}
        {children}
      </div>
    </section>
  )
}
