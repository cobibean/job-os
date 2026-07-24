import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { JobNavigator } from './JobNavigator'

afterEach(cleanup)

test('manual rows support deliberate drag ordering with an accessible button alternative', () => {
  const onReorder = vi.fn()
  const jobs = [
    { jobId: 'a', company: 'Alpha', title: 'Builder', status: 'discovered' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/a', discoveredAt: '', lastSeenAt: '' },
    { jobId: 'b', company: 'Beta', title: 'Operator', status: 'reviewed' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/b', discoveredAt: '', lastSeenAt: '' }
  ]
  render(
    <JobNavigator
      error={null} feedback={null} jobs={jobs} loading={false}
      onMove={vi.fn()} onQueryChange={vi.fn()} onReorder={onReorder}
      onSelect={vi.fn()} onSortChange={vi.fn()} onStatusChange={vi.fn()}
      onStatusGroupChange={vi.fn()} query="" selectedJobId={null}
      sortMode="manual" statusGroup=""
    />
  )
  const transfer = {
    getData: vi.fn().mockReturnValue('a'),
    setData: vi.fn(),
    effectAllowed: ''
  }
  const rows = screen.getAllByRole('listitem')

  fireEvent.dragStart(rows[0]!, { dataTransfer: transfer })
  fireEvent.dragOver(rows[1]!, { dataTransfer: transfer })
  fireEvent.drop(rows[1]!, { dataTransfer: transfer })

  expect(onReorder).toHaveBeenCalledWith('a', 'b')
  expect(screen.getByRole('button', { name: 'Move Alpha down' })).not.toBeNull()
})

test('status controls offer only transitions allowed from the current job state', () => {
  const jobs = [
    { jobId: 'a', company: 'Alpha', title: 'Builder', status: 'discovered' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/a', discoveredAt: '', lastSeenAt: '' },
    { jobId: 'b', company: 'Beta', title: 'Operator', status: 'shortlisted' as const, statusGroup: 'Considering', canonicalUrl: 'https://example.com/b', discoveredAt: '', lastSeenAt: '' }
  ]
  render(
    <JobNavigator
      error={null} feedback={null} jobs={jobs} loading={false}
      onMove={vi.fn()} onQueryChange={vi.fn()} onReorder={vi.fn()}
      onSelect={vi.fn()} onSortChange={vi.fn()} onStatusChange={vi.fn()}
      onStatusGroupChange={vi.fn()} query="" selectedJobId={null}
      sortMode="manual" statusGroup=""
    />
  )

  const alphaOptions = [...screen.getByRole('combobox', { name: 'Change Alpha status' }).querySelectorAll('option')]
    .map(option => option.value)
  const betaOptions = [...screen.getByRole('combobox', { name: 'Change Beta status' }).querySelectorAll('option')]
    .map(option => option.value)

  expect(alphaOptions).toEqual(['discovered', 'scored', 'reviewed', 'skipped', 'archived'])
  expect(betaOptions).toEqual(['shortlisted', 'apply_now', 'maybe', 'stretch', 'applied'])
})

test('status sections are independently collapsible and start collapsed', () => {
  const jobs = [
    { jobId: 'a', company: 'Alpha', title: 'Builder', status: 'discovered' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/a', discoveredAt: '', lastSeenAt: '' },
    { jobId: 'b', company: 'Beta', title: 'Operator', status: 'reviewed' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/b', discoveredAt: '', lastSeenAt: '' },
    { jobId: 'c', company: 'Gamma', title: 'Designer', status: 'applied' as const, statusGroup: 'Applied', canonicalUrl: 'https://example.com/c', discoveredAt: '', lastSeenAt: '' }
  ]
  render(
    <JobNavigator
      error={null} feedback={null} jobs={jobs} loading={false}
      onMove={vi.fn()} onQueryChange={vi.fn()} onReorder={vi.fn()}
      onSelect={vi.fn()} onSortChange={vi.fn()} onStatusChange={vi.fn()}
      onStatusGroupChange={vi.fn()} query="" selectedJobId={null}
      sortMode="status" statusGroup=""
    />
  )

  const inbox = screen.getByRole('button', { name: 'Inbox, 2 jobs' })
  const applied = screen.getByRole('button', { name: 'Applied, 1 job' })

  expect(inbox.getAttribute('aria-expanded')).toBe('false')
  expect(applied.getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryByRole('button', { name: 'Select Alpha Builder' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Select Gamma Designer' })).toBeNull()

  fireEvent.click(inbox)

  expect(inbox.getAttribute('aria-expanded')).toBe('true')
  expect(applied.getAttribute('aria-expanded')).toBe('false')
  expect(screen.getByRole('button', { name: 'Select Alpha Builder' })).not.toBeNull()
  expect(screen.getByRole('button', { name: 'Select Beta Operator' })).not.toBeNull()
  expect(screen.queryByRole('button', { name: 'Select Gamma Designer' })).toBeNull()

  fireEvent.click(applied)
  fireEvent.click(inbox)

  expect(inbox.getAttribute('aria-expanded')).toBe('false')
  expect(applied.getAttribute('aria-expanded')).toBe('true')
  expect(screen.queryByRole('button', { name: 'Select Alpha Builder' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Select Gamma Designer' })).not.toBeNull()
})
