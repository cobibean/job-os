// @vitest-environment node
import { readFileSync } from 'node:fs'

import { expect, test } from 'vitest'

import type { JobStatus } from '../../shared/contracts'

import { STATUS_TRANSITIONS, statusOptionLabel } from './jobStatus'

const transitionsFixture = (JSON.parse(readFileSync(
  new URL('../../../../../tests/fixtures/job-status-transitions.json', import.meta.url),
  'utf8'
)) as { transitions: Record<JobStatus, JobStatus[]> }).transitions

const sorted = (table: Record<string, readonly string[]>) =>
  Object.fromEntries(Object.entries(table).map(([status, targets]) => [status, [...targets].sort()]))

test('renderer transitions match the shared API transition contract', () => {
  expect(sorted(STATUS_TRANSITIONS)).toEqual(sorted(transitionsFixture))
})

test('every pre-application status can be marked applied directly', () => {
  for (const status of ['discovered', 'scored', 'reviewed', 'shortlisted', 'apply_now', 'maybe', 'stretch', 'skipped'] as const) {
    expect(STATUS_TRANSITIONS[status]).toContain('applied')
  }
})

test('Considering is an Inbox-only target backed by shortlisted', () => {
  expect(STATUS_TRANSITIONS.discovered).toContain('shortlisted')
  expect(STATUS_TRANSITIONS.scored).toContain('shortlisted')
  expect(STATUS_TRANSITIONS.reviewed).toContain('shortlisted')
  expect(statusOptionLabel('discovered', 'shortlisted')).toBe('Considering')

  for (const status of ['applied', 'interviewing', 'closed', 'skipped', 'archived'] as const) {
    expect(STATUS_TRANSITIONS[status]).not.toContain('shortlisted')
  }
})