import type { CareerProfileArea, CareerProfileItemSnapshot } from '../../../shared/contracts'

export type EditableItemKind =
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

export type ItemFieldKind = 'text' | 'textarea' | 'list' | 'number' | 'select'

export interface ItemFieldSpec {
  key: string
  kind?: ItemFieldKind
  label: string
  options?: Array<{ label: string; value: string }>
  placeholder?: string
}

export interface ItemSpec {
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

export const itemSpecs: ItemSpec[] = [
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
    area: 'my_career', kind: 'claim', label: 'Professional statement',
    requiredAny: ['statement', 'qualifiers', 'forbidden_uses'],
    fields: [
      { key: 'statement', kind: 'textarea', label: 'What JobOS should know' },
      { key: 'qualifiers', kind: 'list', label: 'Context and qualifiers', placeholder: 'One piece of context per line' },
      { key: 'forbidden_uses', kind: 'list', label: 'Do not use this for', placeholder: 'One restriction per line' }
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

export const specsByKind = new Map(itemSpecs.map(spec => [spec.kind, spec]))

export const areaLabels: Record<CareerProfileArea, string> = {
  my_career: 'My Career',
  what_im_looking_for: 'What I’m Looking For',
  my_evidence: 'My Evidence'
}

export const careerGroupLabels: Partial<Record<EditableItemKind, string>> = {
  identity: 'Identity',
  education: 'Education',
  skill: 'Skills',
  positioning: 'Positioning',
  experience: 'Experience',
  project: 'Projects',
  claim: 'Professional statements',
  custom: 'Other details'
}

export const careerGroupDescriptions: Partial<Record<EditableItemKind, string>> = {
  identity: 'The name and professional summary JobOS can use when representing you.',
  education: 'Education and training JobOS can reference when they are relevant.',
  skill: 'Capabilities JobOS can use to understand fit and keep drafts accurate.',
  positioning: 'The headline and framing you want JobOS to use for your career story.',
  experience: 'Roles and work history JobOS can reference as career context.',
  project: 'Concrete examples of what you built, led, or contributed to.',
  claim: 'Statements you approved, with saved context and limits on how JobOS may use them.',
  custom: 'Additional career context that does not fit another section.'
}

export function isEditableKind(value: unknown): value is EditableItemKind {
  return typeof value === 'string' && specsByKind.has(value as EditableItemKind)
}

export function itemKind(item: CareerProfileItemSnapshot): EditableItemKind | null {
  return isEditableKind(item.value.kind) ? item.value.kind : null
}
