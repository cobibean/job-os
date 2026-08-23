import {
  ArchiveRestore,
  ChevronRight,
  Clock3,
  Download,
  FileCheck2,
  FilePlus2,
  FileText,
  History,
  Link2,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  X
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode
} from 'react'
import { createPortal } from 'react-dom'

import type {
  CareerProfileArchiveSelection,
  CareerProfileArea,
  CareerProfileBridge,
  CareerProfileChangeRevision,
  CareerProfileContextMode,
  CareerProfileContextPreview,
  CareerProfileContextScope,
  CareerProfileEvidence,
  CareerProfileEvidenceImportRequest,
  CareerProfileEvidenceKind,
  CareerProfileEvidenceMode,
  CareerProfileItemSnapshot,
  ConnectedCareerProfileAgent
} from '../../shared/contracts'
import type { CareerProfileProductController } from '../hooks/useCareerProfileProduct'

interface CareerProfileProductExperienceProps {
  active: boolean
  activeArea: CareerProfileArea
  bridge: CareerProfileBridge
  hasActiveTurn: boolean
  onBaselineRestored: () => Promise<boolean>
  online: boolean
  product: CareerProfileProductController
}

type EditableItemKind =
  | 'identity'
  | 'education'
  | 'skill'
  | 'positioning'
  | 'experience'
  | 'project'
  | 'claim'
  | 'target_roles'
  | 'compensation'
  | 'location'
  | 'industries'
  | 'priority'
  | 'dealbreaker'
  | 'custom'

type FieldKind = 'text' | 'textarea' | 'list' | 'number' | 'select'

interface ItemFieldSpec {
  key: string
  kind?: FieldKind
  label: string
  options?: Array<{ label: string; value: string }>
  placeholder?: string
}

interface ItemSpec {
  area: Exclude<CareerProfileArea, 'my_evidence'>
  fields: ItemFieldSpec[]
  kind: EditableItemKind
  label: string
  requiredAll?: string[]
  requiredAny: string[]
}

const strengthOptions = [
  { label: 'Not specified', value: '' },
  { label: 'Requirement', value: 'requirement' },
  { label: 'Strong preference', value: 'strong_preference' },
  { label: 'Preference', value: 'preference' },
  { label: 'Dealbreaker', value: 'dealbreaker' }
]

const positiveStrengthOptions = strengthOptions.filter(option => option.value !== 'dealbreaker')

const itemSpecs: ItemSpec[] = [
  {
    area: 'my_career', kind: 'identity', label: 'Identity',
    requiredAny: ['professional_name', 'email', 'phone', 'city', 'links'],
    fields: [
      { key: 'professional_name', label: 'Professional name' },
      { key: 'email', label: 'Email' },
      { key: 'phone', label: 'Phone' },
      { key: 'city', label: 'City' },
      { key: 'links', kind: 'list', label: 'Links', placeholder: 'One link per line' }
    ]
  },
  {
    area: 'my_career', kind: 'education', label: 'Education',
    requiredAny: ['institution', 'credential', 'field_of_study', 'started_on', 'ended_on', 'details'],
    fields: [
      { key: 'institution', label: 'Institution' },
      { key: 'credential', label: 'Credential' },
      { key: 'field_of_study', label: 'Field of study' },
      { key: 'started_on', label: 'Started', placeholder: 'YYYY, YYYY-MM, or YYYY-MM-DD' },
      { key: 'ended_on', label: 'Ended', placeholder: 'YYYY, YYYY-MM, or YYYY-MM-DD' },
      { key: 'details', kind: 'textarea', label: 'Details' }
    ]
  },
  {
    area: 'my_career', kind: 'skill', label: 'Skill', requiredAny: ['name', 'level', 'note'],
    fields: [
      { key: 'name', label: 'Skill name' },
      {
        key: 'level', kind: 'select', label: 'Level', options: [
          { label: 'Not specified', value: '' },
          { label: 'Familiar', value: 'familiar' },
          { label: 'Proficient', value: 'proficient' },
          { label: 'Advanced', value: 'advanced' },
          { label: 'Expert', value: 'expert' }
        ]
      },
      { key: 'note', kind: 'textarea', label: 'Context' }
    ]
  },
  {
    area: 'my_career', kind: 'positioning', label: 'Positioning', requiredAny: ['headline', 'summary'],
    fields: [
      { key: 'headline', label: 'Headline' },
      { key: 'summary', kind: 'textarea', label: 'Summary' }
    ]
  },
  {
    area: 'my_career', kind: 'experience', label: 'Experience',
    requiredAny: ['organization', 'role', 'location', 'started_on', 'ended_on', 'current', 'summary'],
    fields: [
      { key: 'organization', label: 'Organization' },
      { key: 'role', label: 'Role' },
      { key: 'location', label: 'Location' },
      { key: 'started_on', label: 'Started', placeholder: 'YYYY, YYYY-MM, or YYYY-MM-DD' },
      { key: 'ended_on', label: 'Ended', placeholder: 'YYYY, YYYY-MM, or YYYY-MM-DD' },
      {
        key: 'current', kind: 'select', label: 'Current role', options: [
          { label: 'Not specified', value: '' },
          { label: 'Yes', value: 'true' },
          { label: 'No', value: 'false' }
        ]
      },
      { key: 'summary', kind: 'textarea', label: 'Summary' }
    ]
  },
  {
    area: 'my_career', kind: 'project', label: 'Project', requiredAny: ['name', 'role', 'summary', 'url'],
    fields: [
      { key: 'name', label: 'Project name' },
      { key: 'role', label: 'Your role' },
      { key: 'url', label: 'Project link' },
      { key: 'summary', kind: 'textarea', label: 'Summary' }
    ]
  },
  {
    area: 'my_career', kind: 'claim', label: 'Career claim',
    requiredAny: ['statement', 'qualifiers', 'forbidden_uses'],
    fields: [
      { key: 'statement', kind: 'textarea', label: 'Statement' },
      { key: 'qualifiers', kind: 'list', label: 'Qualifiers', placeholder: 'One qualifier per line' },
      { key: 'forbidden_uses', kind: 'list', label: 'Do not use this claim for', placeholder: 'One restriction per line' }
    ]
  },
  {
    area: 'my_career', kind: 'custom', label: 'Other career detail',
    requiredAny: ['label', 'text'], requiredAll: ['label', 'text'],
    fields: [
      { key: 'label', label: 'Label' },
      { key: 'text', kind: 'textarea', label: 'Detail' }
    ]
  },
  {
    area: 'what_im_looking_for', kind: 'target_roles', label: 'Target roles',
    requiredAny: ['roles'],
    fields: [
      { key: 'roles', kind: 'list', label: 'Roles', placeholder: 'One target role per line' },
      { key: 'strength', kind: 'select', label: 'Importance', options: positiveStrengthOptions }
    ]
  },
  {
    area: 'what_im_looking_for', kind: 'compensation', label: 'Compensation',
    requiredAny: ['currency', 'minimum', 'target', 'period', 'note'],
    fields: [
      { key: 'currency', label: 'Currency', placeholder: 'USD' },
      { key: 'minimum', kind: 'number', label: 'Minimum' },
      { key: 'target', kind: 'number', label: 'Target' },
      {
        key: 'period', kind: 'select', label: 'Pay period', options: [
          { label: 'Not specified', value: '' },
          { label: 'Per year', value: 'year' },
          { label: 'Per hour', value: 'hour' }
        ]
      },
      { key: 'note', kind: 'textarea', label: 'Context' }
    ]
  },
  {
    area: 'what_im_looking_for', kind: 'location', label: 'Locations',
    requiredAny: ['locations', 'relocation'],
    fields: [
      { key: 'locations', kind: 'list', label: 'Locations', placeholder: 'One place per line' },
      {
        key: 'relocation', kind: 'select', label: 'Relocation', options: [
          { label: 'Not specified', value: '' },
          { label: 'Open to relocating', value: 'yes' },
          { label: 'Not relocating', value: 'no' },
          { label: 'Would consider it', value: 'consider' }
        ]
      },
      { key: 'strength', kind: 'select', label: 'Importance', options: strengthOptions }
    ]
  },
  {
    area: 'what_im_looking_for', kind: 'industries', label: 'Industries',
    requiredAny: ['industries'],
    fields: [
      { key: 'industries', kind: 'list', label: 'Industries', placeholder: 'One industry per line' },
      { key: 'strength', kind: 'select', label: 'Importance', options: strengthOptions }
    ]
  },
  {
    area: 'what_im_looking_for', kind: 'priority', label: 'Priority',
    requiredAny: ['label', 'explanation'],
    fields: [
      { key: 'label', label: 'Priority' },
      { key: 'explanation', kind: 'textarea', label: 'Why it matters' },
      { key: 'strength', kind: 'select', label: 'Importance', options: positiveStrengthOptions }
    ]
  },
  {
    area: 'what_im_looking_for', kind: 'dealbreaker', label: 'Dealbreaker',
    requiredAny: ['label', 'explanation'],
    fields: [
      { key: 'label', label: 'Dealbreaker' },
      { key: 'explanation', kind: 'textarea', label: 'Why this rules a role out' }
    ]
  }
]

const specsByKind = new Map(itemSpecs.map(spec => [spec.kind, spec]))
const areaLabels: Record<CareerProfileArea, string> = {
  my_career: 'My Career',
  what_im_looking_for: 'What I’m Looking For',
  my_evidence: 'My Evidence'
}

function requestId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

function isEditableKind(value: unknown): value is EditableItemKind {
  return typeof value === 'string' && specsByKind.has(value as EditableItemKind)
}

function itemKind(item: CareerProfileItemSnapshot): EditableItemKind | null {
  return isEditableKind(item.value.kind) ? item.value.kind : null
}

function itemTitle(item: CareerProfileItemSnapshot): string {
  const value = item.value
  const candidates = [
    value.name,
    value.professional_name,
    value.headline,
    value.role,
    value.organization,
    value.institution,
    value.credential,
    value.label,
    value.statement,
    Array.isArray(value.roles) ? readableValue(value.roles) : null,
    Array.isArray(value.locations) ? readableValue(value.locations) : null,
    Array.isArray(value.industries) ? readableValue(value.industries) : null
  ]
  const title = candidates.find(candidate => typeof candidate === 'string' && candidate.trim())
  const kind = itemKind(item)
  return typeof title === 'string' ? title : (kind ? specsByKind.get(kind)?.label ?? 'Career detail' : 'Career detail')
}

function itemSummary(item: CareerProfileItemSnapshot): string {
  const value = item.value
  const candidates = [value.summary, value.note, value.explanation, value.text, value.field_of_study, value.level]
  const summary = candidates.find(candidate => typeof candidate === 'string' && candidate.trim())
  return typeof summary === 'string' ? summary : `${itemKind(item) ? specsByKind.get(itemKind(item)!)?.label : 'Career detail'} · Revision ${item.itemRevision}`
}

function provenanceLabel(item: CareerProfileItemSnapshot): string {
  if (item.provenance.method === 'evidence_import') return 'Added from imported Evidence'
  if (item.provenance.method === 'user_entered' || item.actorPrincipal === 'primary-device') return 'Added by you'
  if (item.actorPrincipal.startsWith('agent:')) {
    const name = item.actorPrincipal.replace(/^agent:/, '').replaceAll(/[-._]+/g, ' ')
    return `Added by ${name.replace(/\b\w/g, letter => letter.toUpperCase())}`
  }
  return 'Added through JobOS'
}

function evidenceProvenanceLabel(evidence: CareerProfileEvidence): string {
  if (evidence.provenance.method === 'user_import') return 'Imported by you'
  if (evidence.provenance.method === 'agent_import') return 'Imported by a connected agent'
  if (evidence.provenance.method === 'migration_import') return 'Imported during migration'
  return 'Imported through JobOS'
}

function readableLabel(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

function readableValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(item => readableValue(item)).join(', ')
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return value.toLocaleString()
  if (value === null || value === undefined || value === '') return 'Not specified'
  return String(value).replaceAll('_', ' ')
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function activeEvidence(product: CareerProfileProductController): CareerProfileEvidence[] {
  return product.current?.sourceEvidence.filter(source => source.active) ?? []
}

function Dialog({ children, className = '', label, onClose }: {
  children: ReactNode
  className?: string
  label: string
  onClose: () => void
}) {
  const dialog = useRef<HTMLElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)

  useEffect(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const modalLayer = dialog.current?.closest('.career-product-modal-layer')
    const background = Array.from(document.body.children)
      .filter(element => element !== modalLayer) as HTMLElement[]
    background.forEach(element => { element.inert = true })
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus()
    return () => {
      background.forEach(element => { element.inert = false })
      const target = returnFocus.current
      window.requestAnimationFrame(() => {
        if (target?.isConnected) target.focus()
      })
    }
  }, [])

  useEffect(() => {
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      onClose()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  const handleKeys = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Tab') return
    const focusable = Array.from(dialog.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])'
    ) ?? [])
    if (focusable.length === 0) return
    const first = focusable[0]!
    const last = focusable.at(-1)!
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return createPortal(
    <div className="career-product-modal-layer">
      <button aria-hidden="true" className="career-product-backdrop" onClick={onClose} tabIndex={-1} type="button" />
      <section
        aria-label={label}
        aria-modal="true"
        className={`career-product-dialog ${className}`}
        onKeyDown={handleKeys}
        ref={dialog}
        role="dialog"
      >
        {children}
      </section>
    </div>,
    document.body
  )
}

function DialogHeading({ closeLabel, eyebrow, onClose, title }: {
  closeLabel: string
  eyebrow: string
  onClose: () => void
  title: string
}) {
  return (
    <header className="career-product-dialog-heading">
      <div><span className="career-kicker">{eyebrow}</span><h3>{title}</h3></div>
      <button aria-label={closeLabel} className="career-icon-action" onClick={onClose} type="button"><X aria-hidden="true" size={16} /></button>
    </header>
  )
}

function ItemDetails({ item, online, onClose, onEdit, product }: {
  item: CareerProfileItemSnapshot
  online: boolean
  onClose: () => void
  onEdit: () => void
  product: CareerProfileProductController
}) {
  const [removing, setRemoving] = useState(false)
  const linked = product.current?.sourceEvidence.filter(source => item.evidenceIds.includes(source.evidenceId)) ?? []
  const values = Object.entries(item.value).filter(([key]) => key !== 'kind')
  const kind = itemKind(item)
  const spec = kind ? specsByKind.get(kind) : undefined
  const guidance = item.area === 'what_im_looking_for' && kind && spec
    ? preferenceGuidance(kind, draftFor(item, spec))
    : null

  const remove = async () => {
    if (!online || removing) return
    setRemoving(true)
    if (await product.removeItem(item)) onClose()
    setRemoving(false)
  }

  return (
    <Dialog className="drawer" label={`${itemTitle(item)} details`} onClose={onClose}>
      <DialogHeading closeLabel="Close details" eyebrow={specsByKind.get(itemKind(item)!)?.label ?? 'Career detail'} onClose={onClose} title={itemTitle(item)} />
      <div className="career-product-dialog-body">
        <div className="career-product-provenance">
          <ShieldCheck aria-hidden="true" size={18} />
          <div><strong>{provenanceLabel(item)}</strong><span>{readableLabel(item.reviewStatus)} · Revision {item.itemRevision} · Updated {new Date(item.updatedAt).toLocaleDateString()}</span></div>
        </div>
        <dl className="career-product-detail-list">
          {values.map(([key, value]) => <div key={key}><dt>{readableLabel(key)}</dt><dd>{readableValue(value)}</dd></div>)}
        </dl>
        {guidance && spec ? (
          <section aria-label={`${spec.label} behavior`} className="career-product-plain-note" role="region">
            <p><strong>Interpretation</strong><span>{guidance.interpretation}</span></p>
            <p><strong>Example</strong><span>{guidance.example}</span></p>
            <p><strong>Affects</strong><span>{guidance.affectedBehavior}</span></p>
          </section>
        ) : null}
        <section className="career-product-linked-evidence">
          <h4><Link2 aria-hidden="true" size={15} />Linked Evidence</h4>
          {linked.length === 0
            ? <p>No Evidence linked — that’s okay. Evidence is optional and is never treated as a quality score.</p>
            : <ul>{linked.map(source => <li key={source.evidenceId}>{source.originalFilename}</li>)}</ul>}
        </section>
      </div>
      <footer className="career-product-dialog-actions">
        <button className="career-primary-button" disabled={!online} onClick={onEdit} type="button">Edit detail</button>
        <button className="career-secondary-button danger" disabled={!online || removing} onClick={() => { void remove() }} type="button"><Trash2 aria-hidden="true" size={14} />{removing ? 'Removing…' : 'Remove detail'}</button>
      </footer>
    </Dialog>
  )
}

function draftFor(item: CareerProfileItemSnapshot | null, spec: ItemSpec): Record<string, string> {
  const value = item?.value ?? {}
  return Object.fromEntries(spec.fields.map(field => {
    const existing = value[field.key]
    if (Array.isArray(existing)) return [field.key, existing.join('\n')]
    if (typeof existing === 'boolean') return [field.key, existing ? 'true' : 'false']
    return [field.key, existing === undefined || existing === null ? '' : String(existing)]
  }))
}

function buildItemValue(spec: ItemSpec, draft: Record<string, string>): Record<string, unknown> & { kind: string } {
  const value: Record<string, unknown> & { kind: string } = { kind: spec.kind }
  for (const field of spec.fields) {
    const raw = (draft[field.key] ?? '').trim()
    if (!raw) continue
    if (field.kind === 'list') {
      value[field.key] = raw.split(/\r?\n/).map(part => part.trim()).filter(Boolean)
    } else if (field.kind === 'number') {
      value[field.key] = Number(raw)
    } else if (field.key === 'current') {
      value[field.key] = raw === 'true'
    } else if (field.key === 'currency') {
      value[field.key] = raw.toUpperCase()
    } else {
      value[field.key] = raw
    }
  }
  return value
}

function hasMeaningfulValue(value: Record<string, unknown>, key: string): boolean {
  const candidate = value[key]
  return Array.isArray(candidate) ? candidate.length > 0 : candidate !== undefined && candidate !== ''
}

interface PreferenceGuidance {
  affectedBehavior: string
  example: string
  interpretation: string
}

function draftLines(value: string | undefined): string[] {
  return (value ?? '').split(/\r?\n/).map(part => part.trim()).filter(Boolean)
}

function importanceMeaning(value: string | undefined): string {
  if (value === 'requirement') return 'treat it as a firm boundary'
  if (value === 'strong_preference') return 'prioritize it while still considering unusually strong alternatives'
  if (value === 'preference') return 'rank it ahead of alternatives without filtering those alternatives out'
  if (value === 'dealbreaker') return 'filter out opportunities that violate it'
  return 'keep its importance unspecified until you choose one'
}

function preferenceGuidance(kind: EditableItemKind, draft: Record<string, string>): PreferenceGuidance | null {
  if (kind === 'target_roles') {
    const roles = draftLines(draft.roles).map(role => readableValue(role))
    const target = roles[0] ?? 'a role you add'
    return {
      affectedBehavior: 'Affects research, matching, and agent focus.',
      example: `A ${target} opening can be researched as a target; adjacent roles can still appear unless another saved boundary rules them out.`,
      interpretation: `JobOS will use ${roles.length > 0 ? roles.join(', ') : 'the roles you add'} as career targets and ${importanceMeaning(draft.strength)}.`
    }
  }
  if (kind === 'compensation') {
    const currency = (draft.currency ?? '').trim().toUpperCase() || 'your chosen currency'
    const period = draft.period === 'hour' ? 'per hour' : draft.period === 'year' ? 'per year' : 'for the period you choose'
    const range = draft.minimum || draft.target
      ? `${draft.minimum ? `a minimum of ${currency} ${draft.minimum}` : 'no stated minimum'}${draft.target ? ` and a target of ${currency} ${draft.target}` : ''} ${period}`
      : 'only the pay details you enter; omitted amounts remain unknown'
    return {
      affectedBehavior: 'Affects matching and agent focus; it never negotiates or applies on your behalf.',
      example: 'A listing below a stated minimum can be called out, while missing salary information stays unknown rather than being guessed.',
      interpretation: `JobOS will compare advertised compensation with ${range}.`
    }
  }
  if (kind === 'location') {
    const locations = draftLines(draft.locations).map(location => readableValue(location))
    const place = locations[0] ?? 'a place you add'
    const relocation = draft.relocation === 'yes'
      ? 'You are open to relocating.'
      : draft.relocation === 'no'
        ? 'You are not relocating.'
        : draft.relocation === 'consider'
          ? 'Relocation can be considered.'
          : 'Relocation remains unspecified.'
    return {
      affectedBehavior: 'Affects research, browsing, matching, and alerts.',
      example: `A role in ${place} can follow this preference; a role elsewhere is handled according to the importance and relocation choices you save.`,
      interpretation: `JobOS will use ${locations.length > 0 ? locations.join(', ') : 'the locations you add'} and ${importanceMeaning(draft.strength)}. ${relocation}`
    }
  }
  if (kind === 'industries') {
    const industries = draftLines(draft.industries).map(industry => readableValue(industry))
    const industry = industries[0] ?? 'an industry you add'
    return {
      affectedBehavior: 'Affects research, matching, and agent focus.',
      example: `A role in ${industry} can be prioritized; roles in other industries remain available unless you save a firm boundary.`,
      interpretation: `JobOS will use ${industries.length > 0 ? industries.join(', ') : 'the industries you add'} as industry preferences and ${importanceMeaning(draft.strength)}.`
    }
  }
  if (kind === 'priority') {
    const label = (draft.label ?? '').trim() || 'this priority'
    return {
      affectedBehavior: 'Affects ranking, matching, and agent focus.',
      example: `When two roles are otherwise similar, the one that better supports ${label} can rank higher.`,
      interpretation: `JobOS will treat ${label} as something to favor and ${importanceMeaning(draft.strength)}.`
    }
  }
  if (kind === 'dealbreaker') {
    const label = (draft.label ?? '').trim() || 'this boundary'
    return {
      affectedBehavior: 'Affects filtering, matching, and alerts.',
      example: `A role that clearly violates ${label} can be ruled out before it reaches your shortlist.`,
      interpretation: `JobOS will treat ${label} as a firm reason an opportunity should not pass.`
    }
  }
  return null
}

function ItemEditor({ area, expectedProfileRevision, item, onClose, online, product }: {
  area: Exclude<CareerProfileArea, 'my_evidence'>
  expectedProfileRevision: number
  item: CareerProfileItemSnapshot | null
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const availableSpecs = itemSpecs.filter(spec => spec.area === area)
  const initialKind = (item ? itemKind(item) : null) ?? availableSpecs[0]!.kind
  const [kind, setKind] = useState<EditableItemKind>(initialKind)
  const spec = specsByKind.get(kind)!
  const [draft, setDraft] = useState<Record<string, string>>(() => draftFor(item, spec))
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>(item?.evidenceIds ?? [])
  const [validation, setValidation] = useState('')
  const evidence = activeEvidence(product)
  const guidance = preferenceGuidance(kind, draft)
  const conflict = product.itemConflict
  const conflictAnnouncement = useRef<HTMLElement>(null)

  useEffect(() => {
    if (conflict) conflictAnnouncement.current?.focus()
  }, [conflict])

  const close = () => {
    product.dismissItemConflict()
    onClose()
  }

  const evidenceName = (evidenceId: string) => (
    product.current?.sourceEvidence.find(source => source.evidenceId === evidenceId)?.originalFilename
      ?? `${evidenceId} (unavailable)`
  )

  const changeKind = (nextKind: EditableItemKind) => {
    const next = specsByKind.get(nextKind)!
    setKind(nextKind)
    setDraft(draftFor(null, next))
    setValidation('')
  }

  const save = async () => {
    const value = buildItemValue(spec, draft)
    const allPresent = (spec.requiredAll ?? []).every(key => hasMeaningfulValue(value, key))
    const anyPresent = spec.requiredAny.some(key => hasMeaningfulValue(value, key))
    if (!allPresent || !anyPresent) {
      setValidation(spec.requiredAll ? 'Complete both required fields before saving.' : 'Add at least one meaningful detail before saving.')
      return
    }
    if (typeof value.currency === 'string' && value.currency.length !== 3) {
      setValidation('Use a three-letter currency code, such as USD.')
      return
    }
    setValidation('')
    if (await product.saveItem(item, value, selectedEvidence, expectedProfileRevision)) onClose()
  }

  return (
    <Dialog label={item ? `Edit ${itemTitle(item)}` : `Add ${area === 'my_career' ? 'career detail' : 'preference'}`} onClose={close}>
      <DialogHeading
        closeLabel="Close editor"
        eyebrow={item ? 'Edit saved detail' : 'Add to your profile'}
        onClose={close}
        title={item ? itemTitle(item) : `Add ${area === 'my_career' ? 'career detail' : 'preference'}`}
      />
      <form className="career-product-editor" onSubmit={event => { event.preventDefault(); void save() }}>
        <label className="career-field">
          <span>Detail type</span>
          <select aria-label="Detail type" disabled={Boolean(item) || product.status === 'saving'} onChange={event => changeKind(event.target.value as EditableItemKind)} value={kind}>
            {availableSpecs.map(candidate => <option key={candidate.kind} value={candidate.kind}>{candidate.label}</option>)}
          </select>
        </label>
        <div className="career-product-editor-grid">
          {spec.fields.map(field => (
            <label className={`career-field ${field.kind === 'textarea' || field.kind === 'list' ? 'wide' : ''}`} key={field.key}>
              <span>{field.label}</span>
              {field.kind === 'textarea' || field.kind === 'list' ? (
                <textarea
                  aria-label={field.label}
                  disabled={product.status === 'saving'}
                  onChange={event => setDraft(current => ({ ...current, [field.key]: event.target.value }))}
                  placeholder={field.placeholder}
                  rows={field.kind === 'list' ? 3 : 4}
                  value={draft[field.key] ?? ''}
                />
              ) : field.kind === 'select' ? (
                <select aria-label={field.label} disabled={product.status === 'saving'} onChange={event => setDraft(current => ({ ...current, [field.key]: event.target.value }))} value={draft[field.key] ?? ''}>
                  {field.options?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              ) : (
                <input
                  aria-label={field.label}
                  disabled={product.status === 'saving'}
                  min={field.kind === 'number' ? 0 : undefined}
                  onChange={event => setDraft(current => ({ ...current, [field.key]: event.target.value }))}
                  placeholder={field.placeholder}
                  type={field.kind === 'number' ? 'number' : 'text'}
                  value={draft[field.key] ?? ''}
                />
              )}
            </label>
          ))}
        </div>
        {guidance ? (
          <section aria-label={`${spec.label} behavior`} className="career-product-plain-note" role="region">
            <p><strong>Interpretation</strong><span>{guidance.interpretation}</span></p>
            <p><strong>Example</strong><span>{guidance.example}</span></p>
            <p><strong>Affects</strong><span>{guidance.affectedBehavior}</span></p>
          </section>
        ) : null}
        <fieldset className="career-product-evidence-picker">
          <legend>Link Evidence <small>Optional</small></legend>
          {evidence.length === 0 ? <p>No Evidence is available yet. You can save this detail without it.</p> : evidence.map(source => (
            <label key={source.evidenceId}>
              <input
                checked={selectedEvidence.includes(source.evidenceId)}
                disabled={product.status === 'saving'}
                onChange={event => setSelectedEvidence(current => event.target.checked ? [...current, source.evidenceId] : current.filter(id => id !== source.evidenceId))}
                type="checkbox"
              />
              <span>{source.originalFilename}</span>
            </label>
          ))}
        </fieldset>
        {validation ? <p className="career-inline-alert" role="alert">{validation}</p> : null}
        {conflict ? (
          <section aria-label="Resolve stale edit" aria-live="assertive" className="career-conflict-card" ref={conflictAnnouncement} role="alert" tabIndex={-1}>
            <h4>Choose what JobOS should keep</h4>
            <p>A newer version was saved before this draft. Nothing has been overwritten.</p>
            <div className="career-agent-change-grid">
              <section>
                <h5>Current saved version</h5>
                {conflict.latestItem ? <dl className="career-product-detail-list">{Object.entries(conflict.latestItem.value).filter(([key]) => key !== 'kind').map(([key, value]) => <div key={key}><dt>{readableLabel(key)}</dt><dd>{readableValue(value)}</dd></div>)}</dl> : <p>The original detail is no longer in the latest profile.</p>}
                <strong>Current linked sources</strong>
                {conflict.latestItem?.evidenceIds.length ? <ul>{conflict.latestItem.evidenceIds.map(evidenceId => <li key={evidenceId}>{evidenceName(evidenceId)}</li>)}</ul> : <p>None</p>}
              </section>
              <section>
                <h5>Your proposed draft</h5>
                <dl className="career-product-detail-list">{Object.entries(conflict.proposedValue).filter(([key]) => key !== 'kind').map(([key, value]) => <div key={key}><dt>{readableLabel(key)}</dt><dd>{readableValue(value)}</dd></div>)}</dl>
                <strong>Proposed linked sources</strong>
                {conflict.proposedEvidenceIds.length ? <ul>{conflict.proposedEvidenceIds.map(evidenceId => <li key={evidenceId}>{evidenceName(evidenceId)}</li>)}</ul> : <p>None</p>}
              </section>
            </div>
            <div className="career-conflict-actions">
              <button className="career-secondary-button" onClick={() => { product.keepItemConflict(); onClose() }} type="button">Keep current</button>
              <button className="career-secondary-button" disabled={!online || product.status === 'saving'} onClick={() => { void product.reapplyItemConflict().then(saved => { if (saved) onClose() }) }} type="button">Reapply my change</button>
              {conflict.canPreserveBoth ? <button className="career-secondary-button" disabled={!online || product.status === 'saving'} onClick={() => { void product.preserveBothItemConflict().then(saved => { if (saved) onClose() }) }} type="button">Preserve both</button> : null}
            </div>
          </section>
        ) : product.status === 'error' ? <p className="career-feedback error" role="alert">{product.message}</p> : null}
        <footer className="career-product-dialog-actions">
          <button className="career-primary-button" disabled={!online || product.status === 'saving'} type="submit">{product.status === 'saving' ? 'Saving…' : 'Save detail'}</button>
          <button className="career-secondary-button" disabled={product.status === 'saving'} onClick={close} type="button">Cancel</button>
        </footer>
      </form>
    </Dialog>
  )
}

interface ItemEditorSession {
  expectedProfileRevision: number
  item: CareerProfileItemSnapshot | null
}

function ItemArea({ active, area, online, product }: {
  active: boolean
  area: Exclude<CareerProfileArea, 'my_evidence'>
  online: boolean
  product: CareerProfileProductController
}) {
  const [detailItemId, setDetailItemId] = useState<string | null>(null)
  const [editorSession, setEditorSession] = useState<ItemEditorSession | null>(null)
  const items = (product.current?.items ?? []).filter(item => item.area === area && itemKind(item) !== null)
  const detailItem = detailItemId
    ? product.current?.items.find(item => item.itemId === detailItemId) ?? null
    : null
  const areaName = area === 'my_career' ? 'career detail' : 'preference'

  const openEditor = (item: CareerProfileItemSnapshot | null) => {
    const expectedProfileRevision = product.current?.profileRevision
    if (expectedProfileRevision === undefined) return
    setEditorSession({ expectedProfileRevision, item })
  }

  useEffect(() => {
    if (!active) {
      setDetailItemId(null)
      setEditorSession(null)
    }
  }, [active])

  return (
    <section className="career-product-area" aria-label={areaLabels[area]}>
      <div className="career-product-area-heading">
        <div>
          <span className="career-kicker">{area === 'my_career' ? 'Your story' : 'Beyond work arrangement'}</span>
          <h3>{area === 'my_career' ? 'Career details' : 'Other preferences'}</h3>
          <p>{area === 'my_career' ? 'The experience, skills, and positioning you want JobOS to remember.' : 'Roles, location, compensation, priorities, and boundaries—each kept as its own clear choice.'}</p>
        </div>
        <button className="career-primary-button" disabled={!online || !product.current} onClick={() => openEditor(null)} type="button"><Plus aria-hidden="true" size={15} />Add {areaName}</button>
      </div>
      {items.length === 0 ? (
        <div className="career-product-empty">
          <FilePlus2 aria-hidden="true" size={22} />
          <strong>No {area === 'my_career' ? 'career details' : 'other preferences'} yet</strong>
          <p>Start with one useful fact. There is no completeness score to chase.</p>
        </div>
      ) : (
        <div className="career-product-card-grid">
          {items.map(item => (
            <button aria-label={`${itemTitle(item)} details`} className="career-product-card" key={item.itemId} onClick={() => setDetailItemId(item.itemId)} type="button">
              <div><span className="career-product-kind">{specsByKind.get(itemKind(item)!)?.label}</span><span className={`career-product-review ${item.reviewStatus}`}>{readableLabel(item.reviewStatus)}</span></div>
              <strong>{itemTitle(item)}</strong>
              <p>{itemSummary(item)}</p>
              <footer><span>{provenanceLabel(item)}</span><span>{item.evidenceIds.length} Evidence</span><ChevronRight aria-hidden="true" size={16} /></footer>
            </button>
          ))}
        </div>
      )}
      {detailItem ? <ItemDetails
        item={detailItem}
        online={online}
        onClose={() => setDetailItemId(null)}
        onEdit={() => { openEditor(detailItem); setDetailItemId(null) }}
        product={product}
      /> : null}
      {detailItemId && !detailItem ? (
        <Dialog label="Career detail no longer available" onClose={() => setDetailItemId(null)}>
          <DialogHeading closeLabel="Close missing detail" eyebrow="Profile updated" onClose={() => setDetailItemId(null)} title="Career detail no longer available" />
          <div className="career-product-dialog-body"><p className="career-feedback error" role="alert">This detail is no longer in the current Career Profile.</p></div>
        </Dialog>
      ) : null}
      {editorSession ? <ItemEditor
        area={area}
        expectedProfileRevision={editorSession.expectedProfileRevision}
        item={editorSession.item}
        onClose={() => setEditorSession(null)}
        online={online}
        product={product}
      /> : null}
    </section>
  )
}

interface ImportQueueEntry {
  error: string
  expectedProfileRevision: number | null
  file: File
  id: string
  idempotencyKey: string
  status: 'queued' | 'reading' | 'importing' | 'imported' | 'conflict' | 'error'
}

function guessEvidenceKind(filename: string): CareerProfileEvidenceKind {
  const lower = filename.toLowerCase()
  if (lower.includes('resume') || lower.includes('cv')) return 'resume'
  if (lower.includes('portfolio')) return 'portfolio'
  if (lower.startsWith('http') || lower.includes('citation')) return 'citation'
  return 'supporting_document'
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 32_768
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length)))
  }
  return btoa(binary)
}

function EvidenceDetails({ evidence, onClose, online, product }: {
  evidence: CareerProfileEvidence
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const [removing, setRemoving] = useState(false)
  const linkedItems = product.current?.items.filter(item => item.evidenceIds.includes(evidence.evidenceId)) ?? []

  const remove = async () => {
    setRemoving(true)
    if (await product.removeEvidence(evidence.evidenceId)) onClose()
    setRemoving(false)
  }

  return (
    <Dialog className="drawer" label={`${evidence.originalFilename} details`} onClose={onClose}>
      <DialogHeading closeLabel="Close details" eyebrow="Evidence source" onClose={onClose} title={evidence.originalFilename} />
      <div className="career-product-dialog-body">
        <div className="career-product-provenance"><FileCheck2 aria-hidden="true" size={18} /><div><strong>{evidenceProvenanceLabel(evidence)}</strong><span>{readableLabel(evidence.provenance.sourceKind)} · {formatBytes(evidence.byteCount)}</span></div></div>
        <dl className="career-product-detail-list">
          <div><dt>Source label</dt><dd>{evidence.provenance.sourceLabel}</dd></div>
          <div><dt>Imported</dt><dd>{new Date(evidence.importedAt).toLocaleString()}</dd></div>
          <div><dt>Status</dt><dd>{evidence.active ? 'Available' : 'Unavailable'}</dd></div>
          <div><dt>Linked details</dt><dd>{linkedItems.length === 0 ? 'None' : linkedItems.map(itemTitle).join(', ')}</dd></div>
        </dl>
        <p className="career-product-plain-note">Evidence supports context and provenance. Not having Evidence is never treated as a defect or used to rate your career story.</p>
      </div>
      <footer className="career-product-dialog-actions">
        <button className="career-secondary-button danger" disabled={!online || removing || !evidence.active} onClick={() => { void remove() }} type="button"><Trash2 aria-hidden="true" size={14} />{removing ? 'Removing…' : 'Remove from active use'}</button>
      </footer>
    </Dialog>
  )
}

function EvidenceArea({ active, online, product }: { active: boolean; online: boolean; product: CareerProfileProductController }) {
  const [queue, setQueue] = useState<ImportQueueEntry[]>([])
  const [detail, setDetail] = useState<CareerProfileEvidence | null>(null)
  const processing = useRef(false)
  const evidence = product.current?.sourceEvidence ?? []

  useEffect(() => {
    if (!active) setDetail(null)
  }, [active])

  const addFiles = useCallback((files: File[]) => {
    if (!online) return
    setQueue(current => [
      ...current,
      ...files.map(file => ({
        error: file.size > 10 * 1024 * 1024 ? 'Files must be 10 MiB or smaller.' : '',
        expectedProfileRevision: null,
        file,
        id: requestId('evidence_queue'),
        idempotencyKey: requestId('career_evidence'),
        status: file.size > 10 * 1024 * 1024 ? 'error' as const : 'queued' as const
      }))
    ])
  }, [online])

  useEffect(() => {
    if (!online || product.status === 'saving' || processing.current) return
    const next = queue.find(entry => entry.status === 'queued')
    if (!next) return
    processing.current = true
    const run = async () => {
      setQueue(current => current.map(entry => entry.id === next.id ? { ...entry, status: 'reading', error: '' } : entry))
      try {
        const buffer = await next.file.arrayBuffer()
        if (buffer.byteLength < 1 || buffer.byteLength > 10 * 1024 * 1024) throw new Error('Files must be between 1 byte and 10 MiB.')
        const expectedProfileRevision = next.expectedProfileRevision ?? product.current?.profileRevision
        if (expectedProfileRevision === undefined) throw new Error('The current Career Profile revision is unavailable. Reconnect and try again.')
        const request: CareerProfileEvidenceImportRequest = {
          capturedAt: null,
          contentBase64: arrayBufferToBase64(buffer),
          expectedProfileRevision,
          idempotencyKey: next.idempotencyKey,
          mediaType: next.file.type || 'application/octet-stream',
          originalFilename: next.file.name,
          sourceKind: guessEvidenceKind(next.file.name),
          sourceLabel: next.file.name
        }
        setQueue(current => current.map(entry => entry.id === next.id ? { ...entry, expectedProfileRevision, status: 'importing' } : entry))
        const result = await product.importEvidence(request)
        setQueue(current => current.map(entry => entry.id !== next.id ? entry : result === 'saved'
          ? { ...entry, expectedProfileRevision, status: 'imported', error: '' }
          : result === 'conflict'
            ? {
                ...entry,
                expectedProfileRevision,
                status: 'conflict',
                error: 'Your profile changed first. This confirmed conflict did not overwrite anything; choose whether to import against the latest profile.'
              }
            : {
                ...entry,
                expectedProfileRevision,
                status: 'error',
                error: 'This source could not be imported. Retry preserves the exact original revision and request identity.'
              }))
      } catch (error) {
        setQueue(current => current.map(entry => entry.id === next.id ? {
          ...entry,
          status: 'error',
          error: error instanceof Error ? error.message : 'This source could not be imported.'
        } : entry))
      } finally {
        processing.current = false
      }
    }
    void run()
  }, [online, product.current?.profileRevision, product.importEvidence, product.status, queue])

  const selectFiles = (event: ChangeEvent<HTMLInputElement>) => {
    if (!online) return
    addFiles(Array.from(event.target.files ?? []))
    event.target.value = ''
  }
  const dropFiles = (event: DragEvent<HTMLLabelElement>) => {
    if (!online) return
    event.preventDefault()
    addFiles(Array.from(event.dataTransfer.files))
  }
  const retry = (entry: ImportQueueEntry) => {
    if (!online) return
    setQueue(current => current.map(candidate => candidate.id === entry.id ? {
      ...candidate,
      error: '',
      status: 'queued'
    } : candidate))
  }
  const retryAgainstLatest = (entry: ImportQueueEntry) => {
    if (!online || !product.current) return
    setQueue(current => current.map(candidate => candidate.id === entry.id ? {
      ...candidate,
      error: '',
      expectedProfileRevision: product.current!.profileRevision,
      idempotencyKey: requestId('career_evidence'),
      status: 'queued'
    } : candidate))
  }

  return (
    <section className="career-product-area career-evidence-area" aria-label="My Evidence">
      <label
        aria-disabled={!online}
        className={`career-evidence-dropzone ${!online ? 'disabled' : ''}`}
        onDragOver={online ? event => event.preventDefault() : undefined}
        onDrop={online ? dropFiles : undefined}
      >
        <Upload aria-hidden="true" size={24} />
        <strong>Drop resumes, portfolios, or supporting files here</strong>
        <span>{online ? 'Or choose files. Each source imports independently, up to 10 MiB.' : 'Offline — saved Evidence remains readable. Reconnect before choosing or dropping files.'}</span>
        <input aria-label="Choose Evidence files" disabled={!online} multiple onChange={online ? selectFiles : undefined} type="file" />
      </label>
      {queue.length > 0 ? (
        <section aria-label="Evidence import progress" aria-live="polite" className="career-import-queue">
          <div className="career-product-area-heading compact"><div><span className="career-kicker">Import progress</span><h3>Sources in this batch</h3></div></div>
          <ul>
            {queue.map(entry => {
              const progressText = entry.status === 'imported'
                ? `Imported ${entry.file.name}`
                : entry.status === 'reading'
                  ? 'Reading file…'
                  : entry.status === 'importing'
                    ? 'Importing…'
                    : entry.status === 'queued'
                      ? 'Queued'
                      : entry.error
              return (
                <li className={entry.status} key={entry.id}>
                  <FileText aria-hidden="true" size={16} />
                  <div>
                    <strong>{entry.file.name}</strong>
                    {entry.status === 'error' || entry.status === 'conflict'
                      ? <span aria-label={`${entry.file.name} import ${entry.status}`} role="alert">{progressText}</span>
                      : <span>{progressText}</span>}
                  </div>
                  {entry.status === 'error' ? <button aria-label={`Retry ${entry.file.name}`} className="career-secondary-button" disabled={!online} onClick={() => retry(entry)} type="button"><RefreshCw aria-hidden="true" size={13} />Retry</button> : null}
                  {entry.status === 'conflict' ? <button aria-label={`Import ${entry.file.name} against latest profile`} className="career-secondary-button" disabled={!online} onClick={() => retryAgainstLatest(entry)} type="button"><RefreshCw aria-hidden="true" size={13} />Import against latest</button> : null}
                  {entry.status === 'imported' ? <FileCheck2 aria-label="Imported" size={17} /> : null}
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}
      <div className="career-product-area-heading">
        <div><span className="career-kicker">Your sources</span><h3>Evidence library</h3><p>Evidence is optional. It helps explain where a detail came from; it never makes your profile better or worse.</p></div>
      </div>
      {evidence.length === 0 ? (
        <div className="career-product-empty"><FileText aria-hidden="true" size={22} /><strong>No Evidence yet</strong><p>Your Career Profile still works without it. Add a source only when it is useful.</p></div>
      ) : (
        <div className="career-product-card-grid">
          {evidence.map(source => (
            <button aria-label={`${source.originalFilename} details`} className={`career-product-card evidence ${source.active ? '' : 'inactive'}`} key={source.evidenceId} onClick={() => setDetail(source)} type="button">
              <div><span className="career-product-kind">{readableLabel(source.provenance.sourceKind)}</span><span className={`career-product-review ${source.active ? 'accepted' : 'inactive'}`}>{source.active ? 'Available' : 'Unavailable'}</span></div>
              <strong>{source.originalFilename}</strong>
              <p>{source.provenance.sourceLabel}</p>
              <footer><span>{formatBytes(source.byteCount)}</span><span>{new Date(source.importedAt).toLocaleDateString()}</span><ChevronRight aria-hidden="true" size={16} /></footer>
            </button>
          ))}
        </div>
      )}
      {detail ? <EvidenceDetails evidence={detail} onClose={() => setDetail(null)} online={online} product={product} /> : null}
    </section>
  )
}

function ContextDialog({ bridge, onClose, online, product }: {
  bridge: CareerProfileBridge
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const [agents, setAgents] = useState<ConnectedCareerProfileAgent[]>([])
  const [agentId, setAgentId] = useState('')
  const [scope, setScope] = useState<CareerProfileContextScope | null>(null)
  const [mode, setMode] = useState<CareerProfileContextMode>('none')
  const [selectedAreas, setSelectedAreas] = useState<CareerProfileArea[]>([])
  const [selectedItems, setSelectedItems] = useState<string[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading')
  const [message, setMessage] = useState('')
  const [messageKind, setMessageKind] = useState<'info' | 'error'>('info')
  const [preview, setPreview] = useState<CareerProfileContextPreview | null>(null)
  const pendingKey = useRef('')

  useEffect(() => {
    let cancelled = false
    void bridge.listConnectedAgents().then(result => {
      if (cancelled) return
      const active = result.filter(agent => agent.active)
      setAgents(active)
      setAgentId(active[0]?.agentId ?? '')
      if (active.length === 0) setStatus('ready')
    }).catch(() => {
      if (!cancelled) { setStatus('error'); setMessageKind('error'); setMessage('Connected agents could not load. Try again after reconnecting.') }
    })
    return () => { cancelled = true }
  }, [bridge])

  useEffect(() => {
    setScope(null)
    setMode('none')
    setSelectedAreas([])
    setSelectedItems([])
    setPreview(null)
    setMessage('')
    setMessageKind('info')
    pendingKey.current = ''
    if (!agentId) return
    let cancelled = false
    setStatus('loading')
    void bridge.getCareerProfileContext(agentId).then(result => {
      if (cancelled) return
      setScope(result)
      setMode(result.mode)
      setSelectedAreas(result.selectedAreas)
      setSelectedItems(result.selectedItemIds)
      setPreview(null)
      setMessage('')
      setMessageKind('info')
      setStatus('ready')
      pendingKey.current = ''
    }).catch(() => {
      if (!cancelled) { setStatus('error'); setMessageKind('error'); setMessage('This agent’s Career Profile access could not load.') }
    })
    return () => { cancelled = true }
  }, [agentId, bridge])

  const scopeReady = status === 'ready' && scope?.agentId === agentId

  const chooseMode = (nextMode: CareerProfileContextMode) => {
    setMode(nextMode)
    setMessage('')
    setMessageKind('info')
    setPreview(null)
    pendingKey.current = ''
    if (nextMode !== 'selected') { setSelectedAreas([]); setSelectedItems([]) }
  }
  const toggleArea = (area: CareerProfileArea, checked: boolean) => {
    setSelectedAreas(current => checked ? [...current, area] : current.filter(candidate => candidate !== area))
    setPreview(null)
    setMessage('')
    setMessageKind('info')
    pendingKey.current = ''
  }
  const toggleItem = (itemId: string, checked: boolean) => {
    setSelectedItems(current => checked ? [...current, itemId] : current.filter(candidate => candidate !== itemId))
    setPreview(null)
    setMessage('')
    setMessageKind('info')
    pendingKey.current = ''
  }

  const save = async () => {
    if (!product.current || !scopeReady || !scope || !agentId) return
    if (mode === 'selected' && selectedAreas.length === 0 && selectedItems.length === 0) {
      setStatus('ready'); setMessageKind('error'); setMessage('Choose at least one whole area or exact detail.')
      return
    }
    if (!pendingKey.current) pendingKey.current = requestId('career_context')
    setStatus('saving'); setMessage(''); setMessageKind('info')
    try {
      const result = await bridge.updateCareerProfileContext(agentId, {
        expectedAuthorityEpoch: product.current.authorityEpoch,
        expectedProfileRevision: product.current.profileRevision,
        idempotencyKey: pendingKey.current,
        mode,
        selectedAreas,
        selectedItemIds: selectedItems
      })
      pendingKey.current = ''
      setScope(result)
      setMode(result.mode)
      setSelectedAreas(result.selectedAreas)
      setSelectedItems(result.selectedItemIds)
      setPreview(null)
      setStatus('ready')
      setMessageKind('info')
      setMessage('Access saved. New agent turns will use this choice.')
    } catch {
      setStatus('ready')
      setMessageKind('error')
      setMessage('Access could not be saved. Your exact draft is still here; retry uses the same request identity so an uncertain response cannot create a second change.')
    }
  }

  const sameSelection = (left: string[], right: string[]) => (
    left.length === right.length && [...left].sort().every((value, index) => value === [...right].sort()[index])
  )
  const draftDiffersFromSaved = !scopeReady || !scope
    || mode !== scope.mode
    || !sameSelection(selectedAreas, scope.selectedAreas)
    || !sameSelection(selectedItems, scope.selectedItemIds)

  const makePreview = async () => {
    if (!agentId || !scopeReady || draftDiffersFromSaved) return
    setStatus('loading'); setMessage(''); setMessageKind('info')
    try {
      setPreview(await bridge.previewCareerProfileContext(agentId))
      setStatus('ready')
    } catch {
      setStatus('ready'); setMessageKind('error'); setMessage('The shared-context preview could not be created.')
    }
  }

  const acceptedItems = product.current?.items.filter(item => item.reviewStatus === 'accepted') ?? []
  const selectedMode = mode === 'selected'
  const agent = agents.find(candidate => candidate.agentId === agentId)

  return (
    <Dialog label="Agent Career Profile access" onClose={onClose}>
      <DialogHeading closeLabel="Close access" eyebrow="You choose what is shared" onClose={onClose} title="Agent Career Profile access" />
      <div className="career-product-dialog-body career-context-dialog">
        {agents.length === 0 && status !== 'loading' ? <div className="career-product-empty"><ShieldCheck aria-hidden="true" size={20} /><strong>No connected agents</strong><p>Connect an agent before sharing Career Profile context.</p></div> : (
          <>
            <label className="career-field"><span>Connected agent</span><select aria-label="Connected agent" disabled={status === 'saving'} onChange={event => setAgentId(event.target.value)} value={agentId}>{agents.map(candidate => <option key={candidate.agentId} value={candidate.agentId}>{candidate.displayName}</option>)}</select></label>
            <fieldset className="career-context-options" disabled={!scopeReady}>
              <legend>What can {agent?.displayName ?? 'this agent'} use in new turns?</legend>
              <label><input checked={mode === 'none'} name="career-profile-context-mode" onChange={() => chooseMode('none')} type="radio" /><span><strong>No Career Profile context</strong><small>The agent receives none of this profile.</small></span></label>
              <label><input aria-label="Only selected details" checked={mode === 'selected'} name="career-profile-context-mode" onChange={() => chooseMode('selected')} type="radio" /><span><strong>Only selected details</strong><small>Share exact items or whole areas you choose below. Linked Evidence is not included unless you explicitly select My Evidence.</small></span></label>
              <label><input checked={mode === 'broader'} name="career-profile-context-mode" onChange={() => chooseMode('broader')} type="radio" /><span><strong>Broader accepted profile</strong><small>Explicitly grant every accepted detail and every active Evidence source.</small></span></label>
            </fieldset>
            {selectedMode ? (
              <section className="career-context-selection">
                <div><strong>Whole areas</strong><small>Choosing an area also includes future accepted details in that area.</small></div>
                {(Object.entries(areaLabels) as Array<[CareerProfileArea, string]>).map(([area, label]) => <label key={area}><input aria-label={`All of ${label}`} checked={selectedAreas.includes(area)} disabled={!scopeReady} onChange={event => toggleArea(area, event.target.checked)} type="checkbox" /><span>All of {label}</span></label>)}
                <div><strong>Exact saved details</strong><small>These stay exact even when other details change.</small></div>
                {acceptedItems.length === 0 ? <p>No accepted profile details are available yet.</p> : acceptedItems.map(item => <label key={item.itemId}><input checked={selectedItems.includes(item.itemId)} disabled={!scopeReady} onChange={event => toggleItem(item.itemId, event.target.checked)} type="checkbox" /><span>{itemTitle(item)} <small>{areaLabels[item.area]}</small></span></label>)}
              </section>
            ) : null}
            {draftDiffersFromSaved && scope ? <p className="career-product-plain-note" role="status">Save access before previewing. Preview always shows the saved scope, never unsaved draft choices.</p> : null}
            {preview ? (
              <div className="career-context-preview" role="status">
                <FileCheck2 aria-hidden="true" size={18} />
                <div>
                  <strong>Saved-scope preview created</strong>
                  <span>{preview.profile.items.length} profile detail{preview.profile.items.length === 1 ? '' : 's'} and {preview.profile.sourceEvidence.length} Evidence source{preview.profile.sourceEvidence.length === 1 ? '' : 's'} · Revision {preview.profileRevision}</span>
                  <strong>Profile details</strong>
                  {preview.profile.items.length === 0
                    ? <span>None</span>
                    : <ul>{preview.profile.items.map(item => <li key={item.itemId}>{itemTitle(item)} — {areaLabels[item.area]}</li>)}</ul>}
                  <strong>Evidence files</strong>
                  {preview.profile.sourceEvidence.length === 0
                    ? <span>None</span>
                    : <ul>{preview.profile.sourceEvidence.map(source => <li key={source.evidenceId}>{source.originalFilename}</li>)}</ul>}
                </div>
              </div>
            ) : null}
          </>
        )}
        {message ? <p className={`career-feedback ${messageKind === 'error' ? 'error' : 'saved'}`} role={messageKind === 'error' ? 'alert' : 'status'}>{message}</p> : null}
      </div>
      {agents.length > 0 ? <footer className="career-product-dialog-actions"><button className="career-primary-button" disabled={!online || !scopeReady} onClick={() => { void save() }} type="button">{status === 'saving' ? 'Saving…' : 'Save access'}</button><button aria-label="Preview saved-scope context" className="career-secondary-button" disabled={!online || !scopeReady || draftDiffersFromSaved} onClick={() => { void makePreview() }} type="button">Preview saved scope</button></footer> : null}
    </Dialog>
  )
}

function ExportDialog({ bridge, onClose, online, product }: {
  bridge: CareerProfileBridge
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const [mode, setMode] = useState<CareerProfileEvidenceMode | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const evidence = activeEvidence(product)

  const incompleteChoice = mode === null || (mode === 'selected' && selected.length === 0)

  const save = async () => {
    if (!product.current || incompleteChoice || mode === null) return
    setSaving(true); setMessage('')
    try {
      const result = await bridge.exportCareerProfile({
        evidenceMode: mode,
        expectedProfileRevision: product.current.profileRevision,
        selectedEvidenceIds: mode === 'selected' ? selected : []
      })
      setMessage(result.status === 'cancelled' ? 'Export cancelled. Nothing was written.' : `${result.filename} saved with ${result.includedEvidenceIds.length} Evidence source${result.includedEvidenceIds.length === 1 ? '' : 's'}.`)
    } catch {
      setMessage('The export could not be saved. Your Career Profile was not changed.')
    } finally { setSaving(false) }
  }

  return (
    <Dialog label="Export Career Profile" onClose={onClose}>
      <DialogHeading closeLabel="Close export" eyebrow="Portable current state" onClose={onClose} title="Export Career Profile" />
      <div className="career-product-dialog-body">
        <p className="career-product-plain-note">Every export includes current profile data and provenance. Choose separately whether the actual Evidence files travel with it.</p>
        <fieldset className="career-context-options" disabled={saving}>
          <legend>Evidence files to include</legend>
          <label><input aria-label="Profile only" checked={mode === 'profile_only'} name="career-profile-export-evidence-mode" onChange={() => { setMode('profile_only'); setSelected([]); setMessage('') }} type="radio" /><span><strong>Profile only</strong><small>Keep Evidence metadata, but do not copy any source files.</small></span></label>
          <label><input aria-label="Selected Evidence" checked={mode === 'selected'} disabled={evidence.length === 0} name="career-profile-export-evidence-mode" onChange={() => { setMode('selected'); setMessage('') }} type="radio" /><span><strong>Selected Evidence</strong><small>Copy only the source files you choose below.</small></span></label>
          <label><input checked={mode === 'all'} name="career-profile-export-evidence-mode" onChange={() => { setMode('all'); setSelected([]); setMessage('') }} type="radio" /><span><strong>All active Evidence</strong><small>Copy every currently available source file.</small></span></label>
        </fieldset>
        {mode === 'selected' ? <section className="career-context-selection">{evidence.map(source => <label key={source.evidenceId}><input aria-label={source.originalFilename} checked={selected.includes(source.evidenceId)} onChange={event => { setSelected(current => event.target.checked ? [...current, source.evidenceId] : current.filter(id => id !== source.evidenceId)); setMessage('') }} type="checkbox" /><span>{source.originalFilename}</span></label>)}</section> : null}
        {mode === null ? <p className="career-product-plain-note" role="status">Choose one Evidence-file option before saving the export.</p> : null}
        {mode === 'selected' && selected.length === 0 ? <p className="career-inline-alert" id="career-export-selection-status" role="status">Select at least one Evidence source to enable Save export.</p> : null}
        {message ? <p className={`career-feedback ${/could not/.test(message) ? 'error' : 'saved'}`} role={/could not/.test(message) ? 'alert' : 'status'}>{message}</p> : null}
      </div>
      <footer className="career-product-dialog-actions"><button aria-describedby={mode === 'selected' && selected.length === 0 ? 'career-export-selection-status' : undefined} className="career-primary-button" disabled={!online || saving || incompleteChoice} onClick={() => { void save() }} type="button"><Download aria-hidden="true" size={14} />{saving ? 'Saving…' : 'Save export'}</button></footer>
    </Dialog>
  )
}

function RestoreDialog({ bridge, hasActiveTurn, onClose, onRestored, online, product }: {
  bridge: CareerProfileBridge
  hasActiveTurn: boolean
  onClose: () => void
  onRestored: () => Promise<boolean>
  online: boolean
  product: CareerProfileProductController
}) {
  const [archive, setArchive] = useState<CareerProfileArchiveSelection | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [status, setStatus] = useState<'ready' | 'choosing' | 'restoring' | 'error'>('ready')
  const [message, setMessage] = useState('')
  const pendingRequest = useRef<Parameters<CareerProfileBridge['restoreCareerProfile']>[0] | null>(null)

  const choose = async () => {
    setStatus('choosing'); setMessage('')
    try {
      const nextArchive = await bridge.chooseCareerProfileArchive()
      if (nextArchive?.archiveToken !== archive?.archiveToken) {
        setConfirmation('')
        pendingRequest.current = null
      }
      setArchive(nextArchive)
      setStatus('ready')
    } catch {
      setStatus('error'); setMessage('That archive could not be read. Choose a regular JobOS Career Profile ZIP smaller than 100 MiB.')
    }
  }
  const restore = async () => {
    if (!pendingRequest.current) {
      if (!archive || !product.current || confirmation !== 'RESTORE_CAREER_PROFILE_BASELINE') return
      pendingRequest.current = {
        archiveToken: archive.archiveToken,
        confirmation: 'RESTORE_CAREER_PROFILE_BASELINE',
        expectedProfileRevision: product.current.profileRevision,
        idempotencyKey: requestId('career_restore')
      }
    }
    setStatus('restoring'); setMessage('')
    try {
      const result = await bridge.restoreCareerProfile(pendingRequest.current)
      product.invalidatePersistentCache()
      if (!await onRestored()) throw new Error('authoritative profile refresh failed')
      product.confirmBaselineRestored(result.unavailableEvidenceIds.length)
      pendingRequest.current = null
      onClose()
    } catch {
      setStatus('error'); setMessage('JobOS could not confirm whether the baseline restore completed. The outcome is uncertain. Retry restore to safely check or complete it with the same request identity.')
    }
  }

  return (
    <Dialog label="Restore Career Profile baseline" onClose={onClose}>
      <DialogHeading closeLabel="Close restore" eyebrow="High-impact profile change" onClose={onClose} title="Restore Career Profile baseline" />
      <div className="career-product-dialog-body">
        <div className="career-restore-warning"><ArchiveRestore aria-hidden="true" size={20} /><div><strong>This creates a new baseline.</strong><span>The archive’s current state replaces the current Career Profile. The old timeline is not restored or mixed into it.</span></div></div>
        {hasActiveTurn ? <p className="career-feedback error" role="alert">Finish or stop the active agent turn before restoring the Career Profile.</p> : null}
        <button className="career-secondary-button" disabled={!online || hasActiveTurn || status === 'choosing' || status === 'restoring'} onClick={() => { void choose() }} type="button">{status === 'choosing' ? 'Choosing…' : 'Choose archive'}</button>
        {archive ? <div className="career-archive-selection"><FileCheck2 aria-hidden="true" size={18} /><div><strong>{archive.filename}</strong><span>{formatBytes(archive.byteCount)}</span></div></div> : null}
        <label className="career-field"><span>Type the restore confirmation</span><input aria-label="Type the restore confirmation" autoComplete="off" disabled={!archive || hasActiveTurn || status === 'restoring'} onChange={event => setConfirmation(event.target.value)} placeholder="RESTORE_CAREER_PROFILE_BASELINE" value={confirmation} /></label>
        {message ? <p className="career-feedback error" role="alert">{message}</p> : null}
      </div>
      <footer className="career-product-dialog-actions">
        {status === 'error' ? (
          <button aria-disabled={!online || hasActiveTurn} className="career-primary-button danger" key="retry-restore" onClick={() => { void restore() }} type="button">Retry restore</button>
        ) : (
          <button className="career-primary-button danger" disabled={!online || hasActiveTurn || !archive || confirmation !== 'RESTORE_CAREER_PROFILE_BASELINE' || status === 'restoring'} key="start-restore" onClick={() => { void restore() }} type="button">{status === 'restoring' ? 'Restoring…' : 'Restore as new baseline'}</button>
        )}
      </footer>
    </Dialog>
  )
}

function HistoryDialog({ bridge, onClose, online, product }: {
  bridge: CareerProfileBridge
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const [revisions, setRevisions] = useState<CareerProfileChangeRevision[] | null>(null)
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setMessage('')
    try { setRevisions((await bridge.getCareerProfileChangeHistory()).revisions) } catch { setMessage('Career Profile history could not load. Try again.') }
  }, [bridge])
  useEffect(() => { void load() }, [load])

  const undo = async (revision: CareerProfileChangeRevision) => {
    if (!product.current) return
    setSaving(true); setMessage('')
    try {
      await bridge.undoCareerProfileChange(revision.revisionId, {
        expectedProfileRevision: product.current.profileRevision,
        idempotencyKey: requestId('career_history_undo')
      })
      await product.load(false)
      await load()
      setMessage('Change undone as a new revision.')
    } catch {
      await product.load(false)
      setMessage('That change could not be undone without overwriting newer work. The latest profile is shown.')
    } finally { setSaving(false) }
  }

  return (
    <Dialog className="drawer" label="Career Profile history" onClose={onClose}>
      <DialogHeading closeLabel="Close Career Profile history" eyebrow="Change log" onClose={onClose} title="Career Profile history" />
      <div className="career-product-dialog-body">
        {message ? <p className={`career-feedback ${/undone/.test(message) ? 'saved' : 'error'}`} role={/undone/.test(message) ? 'status' : 'alert'}>{message}</p> : null}
        {!revisions ? <p role="status">Loading history…</p> : revisions.length === 0 ? <div className="career-product-empty"><Clock3 aria-hidden="true" size={20} /><strong>No complete-profile changes yet</strong><p>Your work-arrangement history remains available in its own panel.</p></div> : <ol className="career-product-history">{revisions.map(revision => <li key={revision.revisionId}><div><strong>{readableLabel(revision.operation.replaceAll('.', ' '))}</strong><span>{revision.reason ?? `${readableLabel(revision.actorKind)} change`}</span><small>Revision {revision.profileRevision} · {new Date(revision.createdAt).toLocaleString()}</small></div>{revision.undoable ? <button className="career-secondary-button" disabled={!online || saving} onClick={() => { void undo(revision) }} type="button">Undo change</button> : <span className="career-product-not-undoable">Baseline</span>}</li>)}</ol>}
      </div>
    </Dialog>
  )
}

export function CareerProfileProductExperience({
  active,
  activeArea,
  bridge,
  hasActiveTurn,
  onBaselineRestored,
  online,
  product
}: CareerProfileProductExperienceProps) {
  const [dialog, setDialog] = useState<'context' | 'export' | 'restore' | 'history' | null>(null)
  const writable = online && !product.readOnly
  const evidenceMessageHandledLocally = activeArea === 'my_evidence'
    && /(added to My Evidence|could not be imported|changed somewhere else)/.test(product.message)

  useEffect(() => { if (!active) setDialog(null) }, [active])

  return (
    <>
      <div className="career-product-toolbar" aria-label="Career Profile actions">
        <button className="career-secondary-button" onClick={() => setDialog('context')} type="button"><ShieldCheck aria-hidden="true" size={15} />Agent access</button>
        <button className="career-secondary-button" onClick={() => setDialog('history')} type="button"><History aria-hidden="true" size={15} />History</button>
        <button className="career-secondary-button" onClick={() => setDialog('export')} type="button"><Download aria-hidden="true" size={15} />Export</button>
        <button className="career-secondary-button" onClick={() => setDialog('restore')} type="button"><ArchiveRestore aria-hidden="true" size={15} />Restore baseline</button>
      </div>

      {product.status === 'loading' ? <div className="career-product-loading" role="status">Loading complete Career Profile…</div> : null}
      {product.status === 'error' && !product.current ? <div className="career-product-recover" role="alert"><p>{product.message}</p><button className="career-secondary-button" onClick={() => { void product.load() }} type="button">Retry complete profile</button></div> : null}
      {product.current && !online ? <p className="career-feedback career-product-message error" role="status">Offline — saved complete-profile content is still readable. Reconnect before changing, importing, exporting, or restoring it.</p> : null}
      {product.message && product.current && product.status !== 'saving' && !evidenceMessageHandledLocally ? <p className={`career-feedback career-product-message ${product.status}`} role={product.status === 'error' || product.status === 'conflict' ? 'alert' : 'status'}>{product.message}</p> : null}

      {product.current ? (
        <>
          <div hidden={!active || activeArea !== 'my_career'}><ItemArea active={active && activeArea === 'my_career'} area="my_career" online={writable} product={product} /></div>
          <div hidden={!active || activeArea !== 'what_im_looking_for'}><ItemArea active={active && activeArea === 'what_im_looking_for'} area="what_im_looking_for" online={writable} product={product} /></div>
          <div hidden={!active || activeArea !== 'my_evidence'}><EvidenceArea active={active && activeArea === 'my_evidence'} online={writable} product={product} /></div>
        </>
      ) : null}

      {dialog === 'context' ? <ContextDialog bridge={bridge} onClose={() => setDialog(null)} online={writable} product={product} /> : null}
      {dialog === 'history' ? <HistoryDialog bridge={bridge} onClose={() => setDialog(null)} online={writable} product={product} /> : null}
      {dialog === 'export' ? <ExportDialog bridge={bridge} onClose={() => setDialog(null)} online={writable} product={product} /> : null}
      {dialog === 'restore' ? <RestoreDialog bridge={bridge} hasActiveTurn={hasActiveTurn} onClose={() => setDialog(null)} onRestored={onBaselineRestored} online={writable} product={product} /> : null}
    </>
  )
}
