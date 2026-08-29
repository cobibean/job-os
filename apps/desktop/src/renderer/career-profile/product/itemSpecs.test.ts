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

  it('keeps dealbreaker unavailable for positive-only importance fields', () => {
    for (const kind of ['target_roles', 'priority'] as const) {
      expect(specsByKind.get(kind)?.fields.find(field => field.key === 'strength')?.options)
        .not.toContainEqual({ label: 'Dealbreaker', value: 'dealbreaker' })
    }
    expect(specsByKind.get('location')?.fields.find(field => field.key === 'strength')?.options)
      .toContainEqual({ label: 'Dealbreaker', value: 'dealbreaker' })
  })
})
