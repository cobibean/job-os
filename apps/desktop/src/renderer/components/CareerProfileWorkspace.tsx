import { BriefcaseBusiness, Check, Clock3, MapPin, RotateCcw, Save, Sparkles } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'

import { CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT, careerProfileAdditionalContextLength, type CareerProfileBridge, type WorkArrangementMode, type WorkArrangementStrength, type WorkArrangementValue } from '../../shared/contracts'
import { useCareerProfile } from '../hooks/useCareerProfile'

interface CareerProfileWorkspaceProps {
  bridge?: CareerProfileBridge
  hasActiveTurn: boolean
  online?: boolean
}

const modeLabels: Record<WorkArrangementMode, string> = {
  remote: 'Remote',
  hybrid: 'Hybrid',
  onsite: 'Onsite',
  flexible: 'Flexible'
}

const strengthLabels: Record<WorkArrangementStrength, string> = {
  requirement: 'Requirement',
  strong_preference: 'Strong preference',
  preference: 'Preference',
  dealbreaker: 'Dealbreaker'
}

function interpretation(value: WorkArrangementValue): string {
  const mode = modeLabels[value.mode].toLowerCase()
  if (value.mode === 'flexible') return 'Keep remote, hybrid, and onsite roles open unless another preference rules them out.'
  if (value.strength === 'preference') return `Prefer ${mode} roles, but keep strong alternatives in consideration.`
  if (value.strength === 'strong_preference') return `Prioritize ${mode} roles and only elevate alternatives when they are an unusually strong fit.`
  return `Only show roles that support ${mode} work.`
}

function example(value: WorkArrangementValue): string {
  if (value.mode === 'flexible') return 'Example: A strong remote role and a strong onsite role can both pass this preference.'
  const label = modeLabels[value.mode]
  if (value.strength === 'preference') return `Example: A ${label.toLowerCase()} role ranks higher, while an excellent alternative can still pass.`
  if (value.strength === 'strong_preference') return `Example: ${label} roles lead the list; alternatives need a significantly stronger overall match.`
  const rejected = value.mode === 'onsite' ? 'fully remote' : 'fully onsite'
  return `Example: A ${rejected} role would be filtered out before it reaches your shortlist.`
}

function preferenceSummary(value: WorkArrangementValue): string {
  const note = value.note ? ` — ${value.note}` : ' — No additional context'
  return `${modeLabels[value.mode]} · ${strengthLabels[value.strength]}${note}`
}

export function CareerProfileWorkspace({ bridge = window.jobos.careerProfile, hasActiveTurn, online = true }: CareerProfileWorkspaceProps) {
  const profile = useCareerProfile(bridge)
  const [validation, setValidation] = useState('')
  const historyDrawer = useRef<HTMLElement>(null)
  const historyTrigger = useRef<HTMLButtonElement>(null)
  const historyWasOpen = useRef(false)

  useEffect(() => {
    if (profile.historyOpen) {
      historyWasOpen.current = true
      const drawer = historyDrawer.current
      const modalLayer = drawer?.closest('.career-history-modal-layer')
      const background = Array.from(document.body.children)
        .filter(element => element !== modalLayer) as HTMLElement[]
      background.forEach(element => { element.inert = true })
      drawer?.querySelector<HTMLButtonElement>('button')?.focus()
      return () => { background.forEach(element => { element.inert = false }) }
    }
    if (historyWasOpen.current) {
      historyWasOpen.current = false
      window.requestAnimationFrame(() => historyTrigger.current?.focus())
    }
  }, [profile.historyOpen])

  const update = <Key extends keyof WorkArrangementValue>(key: Key, value: WorkArrangementValue[Key]) => {
    setValidation('')
    profile.setDraft(current => ({
      ...current,
      [key]: value
    }))
  }

  const submit = () => {
    if (!online) {
      setValidation('Reconnect to JobOS before saving. Your edit will stay here.')
      return
    }
    if (careerProfileAdditionalContextLength(profile.draft.note ?? '') > CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT) {
      setValidation(`Keep the note to ${CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT} characters or fewer.`)
      return
    }
    setValidation('')
    void profile.save(hasActiveTurn ? 'Saved — applies to the next turn.' : 'Saved.')
  }

  const closeHistory = () => {
    profile.setHistoryOpen(false)
  }

  const handleHistoryKeys = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeHistory()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = Array.from(historyDrawer.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], [tabindex]:not([tabindex="-1"])') ?? [])
    if (focusable.length === 0) return
    const first = focusable[0]!
    const last = focusable[focusable.length - 1]!
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  if (profile.status === 'loading') {
    return (
      <main aria-busy="true" aria-label="Career Profile" className="career-profile-workspace">
        <aside className="career-profile-rail"><div className="career-skeleton rail" /></aside>
        <section className="career-profile-main"><div className="career-skeleton title" /><div className="career-skeleton card" role="status">Loading Career Profile…</div></section>
      </main>
    )
  }

  if (!profile.current && profile.status === 'error') {
    return (
      <main className="career-profile-workspace career-profile-centered">
        <section className="career-state-card" role="alert">
          <Sparkles aria-hidden="true" size={22} />
          <h1>Career Profile is unavailable right now</h1>
          <p>Your existing JobOS work is safe. Reconnect to the staging service and try again.</p>
          <button className="career-secondary-button" onClick={() => { void profile.load() }} type="button">Try again</button>
        </section>
      </main>
    )
  }

  const currentValue = profile.current?.record?.value
  const empty = !currentValue

  return (
    <main className="career-profile-workspace">
      <aside aria-label="Career Profile sections" className="career-profile-rail">
        <div className="career-profile-heading">
          <span className="career-eyebrow">Your shared context</span>
          <h1>Career Profile</h1>
          <p>The information JobOS uses to understand your career and preferences.</p>
        </div>
        <nav className="career-profile-nav">
          <button className="career-nav-item" disabled type="button"><BriefcaseBusiness aria-hidden="true" size={17} /><span><strong>My Career</strong><small>Coming later</small></span></button>
          <button aria-current="page" className="career-nav-item active" type="button"><MapPin aria-hidden="true" size={17} /><span><strong>What I’m Looking For</strong><small>1 preference</small></span></button>
          <button className="career-nav-item" disabled type="button"><Sparkles aria-hidden="true" size={17} /><span><strong>My Evidence</strong><small>Coming later</small></span></button>
        </nav>
        <div className="career-staging-note"><span>(FAKE) staging profile</span><p>This preview does not replace your live career context.</p></div>
      </aside>

      <section className="career-profile-main">
        <span className="career-mobile-staging">(FAKE) staging profile</span>
        <header className="career-detail-header">
          <div>
            <span className="career-breadcrumb">What I’m Looking For</span>
            <h2>Work arrangement</h2>
            <p>Tell JobOS where you want to work and how firmly it should apply that choice.</p>
          </div>
          {profile.current?.record ? <span className="career-revision-badge">Revision {profile.current.profileRevision}</span> : null}
        </header>

        {empty ? (
          <section className="career-empty-card">
            <MapPin aria-hidden="true" size={24} />
            <h3>Tell JobOS where you want to work</h3>
            <p>Start with a flexible preference, then make it stronger if location should filter opportunities.</p>
          </section>
        ) : null}

        <div className="career-detail-grid">
          <form className="career-form-card" onSubmit={event => { event.preventDefault(); submit() }}>
            <div className="career-card-heading"><div><span className="career-kicker">Preference</span><h3>Your work arrangement</h3></div>{currentValue ? <span className="career-status"><Check aria-hidden="true" size={13} /> User stated</span> : null}</div>

            <label className="career-field">
              <span>Work arrangement</span>
              <select aria-label="Work arrangement" disabled={profile.status === 'saving'} onChange={event => update('mode', event.target.value as WorkArrangementMode)} value={profile.draft.mode}>
                {Object.entries(modeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>

            <fieldset className="career-strength-field">
              <legend>How important is this?</legend>
              <select aria-label="How important is this?" disabled={profile.status === 'saving'} onChange={event => update('strength', event.target.value as WorkArrangementStrength)} value={profile.draft.strength}>
                {Object.entries(strengthLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <p>{profile.draft.mode === 'flexible' ? 'Flexible can be paired with any strength. JobOS will preserve your choice even when the combination is unusual.' : 'Requirements and dealbreakers filter roles. Preferences change how opportunities are ranked.'}</p>
            </fieldset>

            <label className="career-field">
              <span>Additional context <small>Optional</small></span>
              <textarea aria-describedby="career-note-count" aria-label="Additional context" disabled={profile.status === 'saving'} onChange={event => update('note', event.target.value)} placeholder="(FAKE) Two office days per week are okay…" rows={4} value={profile.draft.note ?? ''} />
              <small id="career-note-count">{careerProfileAdditionalContextLength(profile.draft.note ?? '')}/{CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT}</small>
            </label>

            {validation ? <p className="career-inline-alert" role="alert">{validation}</p> : null}
            {!online ? <p className="career-feedback error" role="status">Offline — your saved preference is still readable. Reconnect before saving or using Undo.</p> : null}
            {profile.message ? <p className={`career-feedback ${profile.status}`} role={profile.status === 'error' || profile.status === 'conflict' ? 'alert' : 'status'}>{profile.message}</p> : null}

            {profile.conflict ? (
              <section className="career-conflict-card">
                <h4>Review before saving</h4>
                <dl>
                  <div><dt>Current saved value</dt><dd>{preferenceSummary(profile.conflict.current.record?.value ?? { mode: 'flexible', strength: 'preference', note: null })}</dd></div>
                  <div><dt>Your proposed value</dt><dd>{preferenceSummary(profile.conflict.proposed)}</dd></div>
                </dl>
                <div className="career-conflict-actions">
                  <button className="career-secondary-button" onClick={profile.keepCurrent} type="button">Keep current</button>
                  <button className="career-secondary-button" disabled={!online || profile.status === 'saving'} onClick={() => { void profile.reapplyConflict(hasActiveTurn ? 'Saved — applies to the next turn.' : 'Saved.') }} type="button">Reapply my change</button>
                </div>
              </section>
            ) : null}

            <div className="career-form-actions">
              <button className="career-primary-button" disabled={!online || profile.status === 'saving'} type="submit"><Save aria-hidden="true" size={15} />{profile.status === 'saving' ? 'Saving…' : 'Save preference'}</button>
              <button className="career-text-button" disabled={profile.status === 'saving'} onClick={() => { void profile.openHistory() }} ref={historyTrigger} type="button"><Clock3 aria-hidden="true" size={15} />View history</button>
            </div>
          </form>

          <aside className="career-interpretation-card">
            <span className="career-kicker">How JobOS uses this</span>
            <h3>{interpretation(profile.draft)}</h3>
            <p>{example(profile.draft)}</p>
            <div className="career-impact-list"><span>Used in</span><ul><li>Job research</li><li>Browse ranking</li><li>Agent recommendations</li></ul></div>
          </aside>
        </div>

        {profile.historyOpen ? createPortal(
          <div className="career-history-modal-layer">
            <div aria-hidden="true" className="career-history-backdrop" onClick={closeHistory} />
            <aside aria-label="Work arrangement history" aria-modal="true" className="career-history-drawer" onKeyDown={handleHistoryKeys} ref={historyDrawer} role="dialog">
            <div className="career-history-heading"><div><span className="career-kicker">Change log</span><h3>Work arrangement history</h3></div><button aria-label="Close history" className="career-text-button" onClick={closeHistory} type="button">Close</button></div>
            {profile.historyError ? <div className="career-history-error" role="alert"><p>{profile.historyError}</p><button className="career-secondary-button" onClick={() => { void profile.openHistory() }} type="button">Try again</button></div> : !profile.history ? <p role="status">Loading history…</p> : (
              <ol className="career-history-list">
                {profile.history.revisions.map(revision => (
                  <li key={revision.revisionId}>
                    <div><strong>Revision {revision.profileRevision}</strong><span>{modeLabels[revision.value.mode]} · {strengthLabels[revision.value.strength]}</span><small>{revision.operation === 'restore' ? 'Restored by you' : 'Changed by you'} · {new Date(revision.createdAt).toLocaleString()}</small></div>
                    {revision.baseProfileRevision >= 1 ? <button aria-label={`Undo to before revision ${revision.profileRevision}`} className="career-icon-action" disabled={!online || profile.status === 'saving'} onClick={() => { void profile.restore(revision.baseProfileRevision) }} title={online ? 'Undo to this previous value' : 'Reconnect before using Undo'} type="button"><RotateCcw aria-hidden="true" size={15} /></button> : null}
                  </li>
                ))}
              </ol>
            )}
            </aside>
          </div>,
          document.body
        ) : null}
      </section>
    </main>
  )
}
