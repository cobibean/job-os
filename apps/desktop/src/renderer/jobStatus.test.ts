import { expect, test } from 'vitest'

import { STATUS_TRANSITIONS, statusOptionLabel } from './jobStatus'

test('Considering is an Inbox-only target backed by shortlisted', () => {
  expect(STATUS_TRANSITIONS.discovered).toContain('shortlisted')
  expect(STATUS_TRANSITIONS.scored).toContain('shortlisted')
  expect(STATUS_TRANSITIONS.reviewed).toContain('shortlisted')
  expect(statusOptionLabel('discovered', 'shortlisted')).toBe('Considering')

  for (const status of ['applied', 'interviewing', 'closed', 'skipped', 'archived'] as const) {
    expect(STATUS_TRANSITIONS[status]).not.toContain('shortlisted')
  }
})