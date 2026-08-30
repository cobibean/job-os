import { describe, expect, it } from 'vitest'

import type { CareerProfileItemSnapshot } from '../../../shared/contracts'
import { specsByKind } from './itemSpecs'
import {
  buildItemValue,
  draftFor,
  itemSearchText,
  itemSummary,
  itemTitle,
  preferenceGuidance,
  provenanceLabel,
  validateItemValue
} from './itemPresentation'

const item = (value: CareerProfileItemSnapshot['value']): CareerProfileItemSnapshot => ({
  actorPrincipal: 'agent:job-hunter',
  area: 'my_career',
  createdAt: '2026-08-29T12:00:00Z',
  evidenceIds: [],
  itemId: 'item_ABCDEFGHIJKLMNOPQRSTUVWX',
  itemRevision: 3,
  provenance: { method: 'agent_direct', sourceKind: 'agent', sourceLabel: 'Job Hunter' },
  reviewStatus: 'accepted',
  updatedAt: '2026-08-29T12:00:00Z',
  value
})

describe('Career Profile item presentation', () => {
  it('formats title, fallback summary, provenance, and punctuation-insensitive search text', () => {
    const skill = item({ kind: 'skill', name: 'Type Script' })
    expect(itemTitle(skill)).toBe('Type Script')
    expect(itemSummary(skill)).toBe('Skill · Revision 3')
    expect(provenanceLabel(skill)).toBe('Added by Job Hunter')
    expect(itemSearchText(skill)).toContain('typescript')
  })

  it('round-trips editor drafts into normalized item values', () => {
    const spec = specsByKind.get('experience')!
    const draft = draftFor(item({ kind: 'experience', organization: 'Northstar', current: true }), spec)
    expect(draft.current).toBe('true')
    expect(buildItemValue(spec, { ...draft, organization: ' Northstar ', current: 'false' })).toEqual({
      kind: 'experience', organization: 'Northstar', current: false
    })

    const compensation = specsByKind.get('compensation')!
    expect(buildItemValue(compensation, { currency: ' usd ', minimum: '120000' })).toEqual({
      kind: 'compensation', currency: 'USD', minimum: 120000
    })
  })

  it('preserves exact validation requirements and currency guidance', () => {
    const custom = specsByKind.get('custom')!
    expect(validateItemValue(custom, buildItemValue(custom, { label: 'Only a label' })))
      .toBe('Complete both required fields before saving.')
    const compensation = specsByKind.get('compensation')!
    expect(validateItemValue(compensation, buildItemValue(compensation, { currency: 'US' })))
      .toBe('Use a three-letter currency code, such as USD.')
    expect(validateItemValue(compensation, buildItemValue(compensation, {})))
      .toBe('Add at least one meaningful detail before saving.')
  })

  it('explains preference strength without turning preferences into filters', () => {
    expect(preferenceGuidance('target_roles', { roles: 'Platform Engineer', strength: 'preference' }))
      .toEqual(expect.objectContaining({
        affectedBehavior: 'Affects research, matching, and agent focus.',
        interpretation: expect.stringContaining('without filtering those alternatives out')
      }))
    expect(preferenceGuidance('dealbreaker', { label: 'Mandatory travel' })?.interpretation)
      .toBe('JobOS will treat Mandatory travel as a firm reason an opportunity should not pass.')
    expect(preferenceGuidance('skill', { name: 'TypeScript' })).toBeNull()
  })
})
