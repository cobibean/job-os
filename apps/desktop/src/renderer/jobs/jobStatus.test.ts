// @vitest-environment node
import { readFileSync } from 'node:fs'

import { expect, test } from 'vitest'

import type { JobStatus } from '../../shared/contracts'

import { JOB_STATUSES, STATUS_TRANSITIONS, statusOptionLabel } from './jobStatus'

const transitionsFixture = (JSON.parse(readFileSync(
  new URL('../../../../../tests/fixtures/job-status-transitions.json', import.meta.url),
  'utf8'
)) as { transitions: Record<JobStatus, JobStatus[]> }).transitions

const sorted = (table: Record<string, readonly string[]>) =>
  Object.fromEntries(Object.entries(table).map(([status, targets]) => [status, [...targets].sort()]))

test('renderer transitions match the shared API transition contract', () => {
  expect(sorted(STATUS_TRANSITIONS)).toEqual(sorted(transitionsFixture))
})

test('every status can move to every other status', () => {
  for (const status of JOB_STATUSES) {
    expect(STATUS_TRANSITIONS[status]).toEqual(JOB_STATUSES.filter(target => target !== status))
  }
  expect(STATUS_TRANSITIONS.archived).toContain('discovered')
  expect(STATUS_TRANSITIONS.applied).toContain('shortlisted')
})

test('shortlisted is labelled Considering and applied as Mark applied', () => {
  expect(statusOptionLabel('discovered', 'shortlisted')).toBe('Considering')
  expect(statusOptionLabel('closed', 'shortlisted')).toBe('Considering')
  expect(statusOptionLabel('discovered', 'applied')).toBe('Mark applied')
  expect(statusOptionLabel('applied', 'applied')).toBe('Applied')
})
