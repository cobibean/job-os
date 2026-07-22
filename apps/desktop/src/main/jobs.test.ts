// @vitest-environment node

import { expect, test, vi } from 'vitest'

vi.mock('@jobos/contracts', () => ({
  createJobOsApiClient: vi.fn(() => ({})),
  jobCreateFromBrowserV1JobsPost: vi.fn(),
  jobUpdateStatusV1JobsJobIdStatusPut: vi.fn(),
  jobsListV1JobsGet: vi.fn(),
  jobsReorderV1JobsOrderPut: vi.fn(),
  workspaceJobsV1WorkspaceJobsGet: vi.fn(),
  workspaceSelectJobV1WorkspaceJobsSelectionPut: vi.fn(),
  workspaceSortJobsV1WorkspaceJobsSortPut: vi.fn()
}))

import { jobCreateFromBrowserV1JobsPost } from '@jobos/contracts'

import { createMainJobsClient, JobEventDecoder } from './jobs.js'

test('browser extraction is persisted through the authenticated JobOS API', async () => {
  vi.mocked(jobCreateFromBrowserV1JobsPost).mockResolvedValue({
    response: new Response(null, { status: 200 }),
    data: {
      event_id: 17,
      created: false,
      job: {
        job_id: 'job-existing',
        company: 'Northstar Labs',
        title: 'Senior Engineer',
        status: 'discovered',
        status_group: 'Inbox',
        canonical_url: 'https://example.com/jobs/17',
        discovered_at: '2026-07-22T00:00:00Z',
        last_seen_at: '2026-07-22T00:00:00Z',
        description: 'Build useful things.',
        location: 'Remote'
      }
    }
  } as never)
  const client = createMainJobsClient({ baseUrl: 'http://127.0.0.1:8766', deviceToken: 'test-token' })

  const result = await client.createFromBrowser({
    companyName: 'Northstar Labs',
    title: 'Senior Engineer',
    canonicalUrl: 'https://example.com/jobs/17',
    locationText: 'Remote',
    descriptionText: 'Build useful things.',
    applicationUrl: 'https://example.com/jobs/17/apply'
  }, 'browser-save-17')

  expect(jobCreateFromBrowserV1JobsPost).toHaveBeenCalledWith(expect.objectContaining({
    body: expect.objectContaining({
      company_name: 'Northstar Labs',
      idempotency_key: 'browser-save-17',
      origin: 'user'
    })
  }))
  expect(result).toEqual(expect.objectContaining({
    eventId: 17,
    created: false,
    job: expect.objectContaining({ jobId: 'job-existing' })
  }))
})

test('the desktop event decoder preserves SSE events split across network chunks', () => {
  const decoder = new JobEventDecoder()

  const first = decoder.push('id: 7\nevent: jobos\ndata: {"event_id":7,"event_type":"job_status_')
  const second = decoder.push('changed","origin":"mcp","job_id":"job-1"}\n\n')

  expect(first).toEqual([])
  expect(second).toEqual([
    { eventId: 7, eventType: 'job_status_changed', origin: 'mcp', jobId: 'job-1' }
  ])
})
