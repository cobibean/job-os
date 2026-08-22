import { BriefcaseBusiness, Check, Clock3, MapPin, RotateCcw, Save, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'

import { CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT, careerProfileAdditionalContextLength, type CareerProfileArea, type CareerProfileBridge, type CareerProfileItemSnapshot, type WorkArrangementMode, type WorkArrangementStrength, type WorkArrangementValue } from '../../shared/contracts'
import { useCareerProfile } from '../hooks/useCareerProfile'
import { useCareerProfileCollaboration } from '../hooks/useCareerProfileCollaboration'
import { useCareerProfileProduct } from '../hooks/useCareerProfileProduct'
import { CareerProfileProductExperience } from './CareerProfileProductExperience'

interface CareerProfileWorkspaceProps {
  active?: boolean
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

function agentDisplayName(principal: string): string {
  return principal
    .replace(/^agent:/, '')
    .split(/[-._]+/)
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ') || 'Connected agent'
}

interface SnapshotRow {
  key: string
  label: string
  value: string
}

const snapshotLabels: Record<string, string> = {
  actorPrincipal: 'Changed by',
  area: 'Profile area',
  createdAt: 'Created',
  evidenceIds: 'Evidence links',
  itemId: 'Item ID',
  itemRevision: 'Item revision',
  provenance: 'Source',
  reviewStatus: 'Status',
  updatedAt: 'Updated',
  value: 'Value'
}

function readableLabel(path: string): string {
  const key = path.split('.').at(-1) ?? path
  const known = snapshotLabels[key]
  if (known) return known
  const words = key.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function readableValue(path: string, value: string | number | boolean | null): string {
  if (value === null) return 'None'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return String(value)
  if (path.endsWith('.kind') || path === 'area' || path === 'reviewStatus' || path.startsWith('provenance.')) {
    return value.replaceAll('_', ' ')
  }
  return value
}

function flattenSnapshotValue(
  path: string,
  value: unknown,
  rows: SnapshotRow[]
): void {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      rows.push({ key: path, label: readableLabel(path), value: 'None' })
      return
    }
    if (value.every(item => typeof item !== 'object' || item === null)) {
      rows.push({
        key: path,
        label: readableLabel(path),
        value: value.map(item => readableValue(path, item as string | number | boolean | null)).join(', ')
      })
      return
    }
    value.forEach((item, index) => flattenSnapshotValue(`${path}.${index + 1}`, item, rows))
    return
  }
  if (typeof value === 'object' && value !== null) {
    Object.entries(value).forEach(([key, item]) => {
      flattenSnapshotValue(path ? `${path}.${key}` : key, item, rows)
    })
    return
  }
  rows.push({
    key: path,
    label: readableLabel(path),
    value: readableValue(path, value as string | number | boolean | null)
  })
}

function ProposalSnapshot({ snapshot, emptyLabel }: {
  snapshot: CareerProfileItemSnapshot | null
  emptyLabel: string
}) {
  if (!snapshot) return <p className="career-agent-snapshot-empty">{emptyLabel}</p>
  const rows: SnapshotRow[] = []
  flattenSnapshotValue('value', snapshot.value, rows)
  flattenSnapshotValue('evidenceIds', snapshot.evidenceIds, rows)
  flattenSnapshotValue('area', snapshot.area, rows)
  flattenSnapshotValue('itemId', snapshot.itemId, rows)
  flattenSnapshotValue('itemRevision', snapshot.itemRevision, rows)
  flattenSnapshotValue('reviewStatus', snapshot.reviewStatus, rows)
  flattenSnapshotValue('actorPrincipal', snapshot.actorPrincipal, rows)
  flattenSnapshotValue('provenance', snapshot.provenance, rows)
  flattenSnapshotValue('createdAt', snapshot.createdAt, rows)
  flattenSnapshotValue('updatedAt', snapshot.updatedAt, rows)
  return (
    <dl className="career-agent-snapshot">
      {rows.map(row => (
        <div key={row.key}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function CareerProfileWorkspace({ active = true, bridge = window.jobos.careerProfile, hasActiveTurn, online = true }: CareerProfileWorkspaceProps) {
  const profile = useCareerProfile(bridge)
  const product = useCareerProfileProduct(bridge)
  const refreshProfile = useCallback(async () => {
    const [, completeProfileRefreshed] = await Promise.all([profile.load(false), product.load(false)])
    return completeProfileRefreshed
  }, [product.load, profile.load])
  const collaboration = useCareerProfileCollaboration(bridge, online, refreshProfile)
  const [activeArea, setActiveArea] = useState<CareerProfileArea>('what_im_looking_for')
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

  useEffect(() => {
    if (!active || activeArea !== 'what_im_looking_for') profile.setHistoryOpen(false)
  }, [active, activeArea, profile.setHistoryOpen])

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
          <p>Your existing JobOS work is safe. Check the JobOS service connection and try again.</p>
          <button className="career-secondary-button" onClick={() => { void profile.load() }} type="button">Try again</button>
        </section>
      </main>
    )
  }

  const currentValue = profile.current?.record?.value
  const empty = !currentValue
  const productItems = product.current?.items ?? []
  const myCareerCount = productItems.filter(item => item.area === 'my_career').length
  const lookingCount = productItems.filter(item => item.area === 'what_im_looking_for').length + (currentValue ? 1 : 0)
  const evidenceCount = product.current?.sourceEvidence.length ?? 0
  const sectionCopy: Record<CareerProfileArea, { breadcrumb: string; description: string; title: string }> = {
    my_career: {
      breadcrumb: 'My Career',
      description: 'Keep the experience, skills, education, projects, and positioning you want JobOS to remember.',
      title: 'My Career'
    },
    what_im_looking_for: {
      breadcrumb: 'What I’m Looking For',
      description: 'Tell JobOS what you want next and how firmly it should apply each choice.',
      title: 'Work arrangement'
    },
    my_evidence: {
      breadcrumb: 'My Evidence',
      description: 'Keep the source files that support your story, with clear provenance and independent import recovery.',
      title: 'My Evidence'
    }
  }
  const visibleSection = sectionCopy[activeArea]

  return (
    <main className="career-profile-workspace">
      <aside aria-label="Career Profile sections" className="career-profile-rail">
        <div className="career-profile-heading">
          <span className="career-eyebrow">Your shared context</span>
          <h1>Career Profile</h1>
          <p>The information JobOS uses to understand your career and preferences.</p>
        </div>
        <nav className="career-profile-nav">
          <button aria-current={activeArea === 'my_career' ? 'page' : undefined} className={`career-nav-item ${activeArea === 'my_career' ? 'active' : ''}`} onClick={() => setActiveArea('my_career')} type="button"><BriefcaseBusiness aria-hidden="true" size={17} /><span><strong>My Career</strong><small>{myCareerCount} detail{myCareerCount === 1 ? '' : 's'}</small></span></button>
          <button aria-current={activeArea === 'what_im_looking_for' ? 'page' : undefined} className={`career-nav-item ${activeArea === 'what_im_looking_for' ? 'active' : ''}`} onClick={() => setActiveArea('what_im_looking_for')} type="button"><MapPin aria-hidden="true" size={17} /><span><strong>What I’m Looking For</strong><small>{lookingCount} preference{lookingCount === 1 ? '' : 's'}</small></span></button>
          <button aria-current={activeArea === 'my_evidence' ? 'page' : undefined} className={`career-nav-item ${activeArea === 'my_evidence' ? 'active' : ''}`} onClick={() => setActiveArea('my_evidence')} type="button"><Sparkles aria-hidden="true" size={17} /><span><strong>My Evidence</strong><small>{evidenceCount} source{evidenceCount === 1 ? '' : 's'}</small></span></button>
        </nav>
        <div className="career-staging-note"><span>JobOS Career Profile</span><p>This is the shared context JobOS and connected agents use.</p></div>
      </aside>

      <section className="career-profile-main">
        <span className="career-mobile-staging">JobOS Career Profile</span>
        <label className="career-mobile-nav">
          <span>Profile section</span>
          <select aria-label="Career Profile section" onChange={event => setActiveArea(event.target.value as CareerProfileArea)} value={activeArea}>
            <option value="my_career">My Career</option>
            <option value="what_im_looking_for">What I’m Looking For</option>
            <option value="my_evidence">My Evidence</option>
          </select>
        </label>
        <header className="career-detail-header">
          <div>
            <span className="career-breadcrumb">{visibleSection.breadcrumb}</span>
            <h2>{visibleSection.title}</h2>
            <p>{visibleSection.description}</p>
          </div>
          {profile.current?.record ? <span className="career-revision-badge">Revision {profile.current.profileRevision}</span> : null}
        </header>

        {collaboration.proposals.length > 0 ? (
          <section aria-label="Agent changes to review" className="career-agent-review-list">
            {collaboration.proposals.map(proposal => (
              <article className="career-agent-review-card" key={proposal.proposalId}>
                <div className="career-agent-review-heading">
                  <div>
                    <span className="career-kicker">Waiting for you</span>
                    <h3>Review {proposal.agentDisplayName}’s change</h3>
                  </div>
                  <span className="career-revision-badge">Based on revision {proposal.baseProfileRevision}</span>
                </div>
                <p className="career-agent-reason">{proposal.reason}</p>
                <p className="career-agent-review-note">{proposal.reviewReason}</p>
                <div className="career-agent-evidence">
                  <strong>Evidence</strong>
                  {proposal.evidenceIds.length > 0
                    ? <ul>{proposal.evidenceIds.map(evidenceId => <li key={evidenceId}>{evidenceId}</li>)}</ul>
                    : <p>No Evidence attached — that’s okay.</p>}
                </div>
                <div className="career-agent-change-grid">
                  <section><h4>Before</h4><ProposalSnapshot emptyLabel="Nothing yet" snapshot={proposal.before} /></section>
                  <section><h4>After</h4><ProposalSnapshot emptyLabel="Removed" snapshot={proposal.after} /></section>
                </div>
                <div className="career-agent-review-actions">
                  <button
                    className="career-primary-button"
                    disabled={!online || collaboration.status === 'saving'}
                    onClick={() => { void collaboration.decide(proposal, 'accept') }}
                    type="button"
                  >Accept exact change</button>
                  <button
                    className="career-secondary-button"
                    disabled={!online || collaboration.status === 'saving'}
                    onClick={() => { void collaboration.decide(proposal, 'reject') }}
                    type="button"
                  >Reject change</button>
                </div>
              </article>
            ))}
          </section>
        ) : null}

        {collaboration.directRevision ? (
          <section className="career-agent-direct-confirmation" role="status">
            <div>
              <strong>{agentDisplayName(collaboration.directRevision.actorPrincipal)} updated your Career Profile</strong>
              <p>{collaboration.directRevision.reason ?? 'An ordinary profile edit was applied directly and added to history.'}</p>
            </div>
            <button
              aria-label="Undo agent change"
              className="career-agent-undo-button"
              disabled={!online || collaboration.status === 'saving'}
              onClick={() => { void collaboration.undo(collaboration.directRevision!) }}
              type="button"
            ><RotateCcw aria-hidden="true" size={15} />Undo</button>
          </section>
        ) : null}

        {collaboration.message ? (
          <p className={`career-collaboration-message ${collaboration.status}`} role={collaboration.status === 'error' ? 'alert' : 'status'}>{collaboration.message}</p>
        ) : null}

        <CareerProfileProductExperience
          active={active}
          activeArea={activeArea}
          bridge={bridge}
          hasActiveTurn={hasActiveTurn}
          onBaselineRestored={refreshProfile}
          online={online}
          product={product}
        />

        <div hidden={activeArea !== 'what_im_looking_for'}>
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
        </div>

        {active && activeArea === 'what_im_looking_for' && profile.historyOpen ? createPortal(
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
