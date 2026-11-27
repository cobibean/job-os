import { describe, expect, it } from 'vitest'

import { itemSpecs, specsByKind } from './itemSpecs'

describe('Career Profile item specifications', () => {
  it('defines each supported item kind once and keeps every field key unique within its editor', () => {
    expect(itemSpecs.map(spec => spec.kind)).toHaveLength(new Set(itemSpecs.map(spec => spec.kind)).size)
    expect(specsByKind.size).toBe(itemSpecs.length)
    for (const spec of itemSpecs) {
      expect(spec.fields.map(field => field.key)).toHaveLength(new Set(spec.fields.map(field => field.key)).size)
      expect(spec.requiredAny.every(key => spec.fields.some(field => field.key === key))).toBe(true)
      expect((spec.requiredAll ?? []).every(key => spec.fields.some(field => field.key === key))).toBe(true)
    }
  })

  it('offers guidance for agents as a My Career item with a type, title, and text', () => {
    const guidance = specsByKind.get('guidance')!
    expect(guidance.area).toBe('my_career')
    expect(guidance.requiredAll).toEqual(['label', 'text'])
    expect(guidance.fields.map(field => field.key)).toEqual(['category', 'label', 'text'])
    expect(guidance.fields[0]?.options?.map(option => option.value))
      .toEqual(['', 'brief', 'rule', 'wording', 'strength', 'objection', 'private', 'other'])
  })

  it('labels a professional statement by how to say it and what can’t be said, with private notes', () => {
    const claim = specsByKind.get('claim')!
    expect(claim.fields.map(field => [field.key, field.label])).toEqual([
      ['statement', 'What JobOS should know'],
      ['qualifiers', 'How to say it'],
      ['forbidden_uses', 'Can’t say'],
      ['notes', 'Notes and history']
    ])
    expect(claim.requiredAny).toEqual(['statement', 'qualifiers', 'forbidden_uses'])
  })

  it('keeps dealbreaker unavailable for positive-only importance fields', () => {
    for (const kind of ['target_roles', 'priority'] as const) {
      expect(specsByKind.get(kind)?.fields.find(field => field.key === 'strength')?.options)
        .not.toContainEqual({ label: 'Dealbreaker', value: 'dealbreaker' })
    }
    expect(specsByKind.get('location')?.fields.find(field => field.key === 'strength')?.options)
      .toContainEqual({ label: 'Dealbreaker', value: 'dealbreaker' })
  })
})
