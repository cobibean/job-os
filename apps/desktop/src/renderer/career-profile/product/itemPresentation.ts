import type { CareerProfileEvidence, CareerProfileItemSnapshot } from '../../../shared/contracts'
import { itemKind, specsByKind, type EditableItemKind, type ItemSpec } from './itemSpecs'

export interface PreferenceGuidance {
  affectedBehavior: string
  example: string
  interpretation: string
}

export function normalizeCareerSearch(value: string): string {
  return value.toLocaleLowerCase().replaceAll(/[^a-z0-9]+/g, '')
}

export function readableValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(item => readableValue(item)).join(', ')
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return value.toLocaleString()
  if (value === null || value === undefined || value === '') return 'Not specified'
  return String(value).replaceAll('_', ' ')
}

export function readableLabel(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

export function itemTitle(item: CareerProfileItemSnapshot): string {
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

export function itemSummary(item: CareerProfileItemSnapshot): string {
  const value = item.value
  const candidates = [value.summary, value.note, value.explanation, value.text, value.field_of_study, value.level]
  const summary = candidates.find(candidate => typeof candidate === 'string' && candidate.trim())
  const kind = itemKind(item)
  return typeof summary === 'string' ? summary : `${kind ? specsByKind.get(kind)?.label : 'Career detail'} · Revision ${item.itemRevision}`
}

export function provenanceLabel(item: CareerProfileItemSnapshot): string {
  if (item.provenance.method === 'evidence_import') return 'Added from imported Evidence'
  if (item.provenance.method === 'user_entered' || item.actorPrincipal === 'primary-device') return 'Added by you'
  if (item.actorPrincipal.startsWith('agent:')) {
    const name = item.actorPrincipal.replace(/^agent:/, '').replaceAll(/[-._]+/g, ' ')
    return `Added by ${name.replace(/\b\w/g, letter => letter.toUpperCase())}`
  }
  return 'Added through JobOS'
}

export function evidenceProvenanceLabel(evidence: CareerProfileEvidence): string {
  if (evidence.provenance.method === 'user_import') return 'Imported by you'
  if (evidence.provenance.method === 'agent_import') return 'Imported by a connected agent'
  if (evidence.provenance.method === 'migration_import') return 'Imported during migration'
  return 'Imported through JobOS'
}

export function itemSearchText(item: CareerProfileItemSnapshot): string {
  const kind = itemKind(item)
  return normalizeCareerSearch([
    kind ? specsByKind.get(kind)?.label ?? '' : '',
    itemTitle(item),
    itemSummary(item),
    provenanceLabel(item),
    ...Object.values(item.value).map(readableValue)
  ].join(' '))
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export function draftFor(item: CareerProfileItemSnapshot | null, spec: ItemSpec): Record<string, string> {
  const value = item?.value ?? {}
  return Object.fromEntries(spec.fields.map(field => {
    const existing = value[field.key]
    if (Array.isArray(existing)) return [field.key, existing.join('\n')]
    if (typeof existing === 'boolean') return [field.key, existing ? 'true' : 'false']
    return [field.key, existing === undefined || existing === null ? '' : String(existing)]
  }))
}

export function buildItemValue(spec: ItemSpec, draft: Record<string, string>): Record<string, unknown> & { kind: string } {
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

export function validateItemValue(spec: ItemSpec, value: Record<string, unknown>): string {
  const allPresent = (spec.requiredAll ?? []).every(key => hasMeaningfulValue(value, key))
  const anyPresent = spec.requiredAny.some(key => hasMeaningfulValue(value, key))
  if (!allPresent || !anyPresent) {
    return spec.requiredAll ? 'Complete both required fields before saving.' : 'Add at least one meaningful detail before saving.'
  }
  if (typeof value.currency === 'string' && value.currency.length !== 3) {
    return 'Use a three-letter currency code, such as USD.'
  }
  return ''
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

export function preferenceGuidance(kind: EditableItemKind, draft: Record<string, string>): PreferenceGuidance | null {
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
