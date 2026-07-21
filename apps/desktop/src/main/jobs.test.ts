// @vitest-environment node

import { beforeEach, expect, test, vi } from 'vitest'

const { jobCreate } = vi.hoisted(() => ({ jobCreate: vi.fn() }))

vi.mock('@jobos/contracts', () => ({
  createJobOsApiClient: vi.fn(() => ({})),
  jobCreateFromBrowserV1JobsPost: jobCreate,
  jobUpdateStatusV1JobsJobIdStatusPut: vi.fn(),
  jobsListV1JobsGet: vi.fn(),
  jobsReorderV1JobsOrderPut: vi.fn(),
  workspaceJobsV1WorkspaceJobsGet: vi.fn(),
  workspaceSelectJobV1WorkspaceJobsSelectionPut: vi.fn(),
  workspaceSortJobsV1WorkspaceJobsSortPut: vi.fn()
}))

import { createMainJobsClient, JobEventDecoder } from './jobs.js'

beforeEach(() => jobCreate.mockReset())

test('the desktop saves a complete extracted listing through the canonical job endpoint', async () => {
  jobCreate.mockResolvedValue({
    response: { status: 200 },
    data: {
      event_id: 42,
      created: true,
      job: {
        job_id: 'browser-job-1',
        company: 'Northstar Labs',
        title: 'Applied AI Product Builder',
        status: 'discovered',
        status_group: 'Inbox',
        canonical_url: 'https://jobs.example.com/northstar',
        discovered_at: '2026-07-21T16:00:00Z',
        last_seen_at: '2026-07-21T16:00:00Z',
        description: 'Build useful agent workflows.',
        location: 'Remote'
      }
    }
  })

  const result = await createMainJobsClient({
    baseUrl: 'http://jobos.test',
    deviceToken: 'fake-device-token'
  }).addFromBrowser({
    companyName: 'Northstar Labs',
    title: 'Applied AI Product Builder',
    canonicalUrl: 'https://jobs.example.com/northstar',
    locationText: 'Remote',
    descriptionText: 'Build useful agent workflows.',
    applicationUrl: 'https://jobs.example.com/northstar/apply'
  })

  expect(result).toMatchObject({ eventId: 42, created: true, job: { jobId: 'browser-job-1' } })
  expect(jobCreate).toHaveBeenCalledOnce()
  expect(jobCreate.mock.calls[0]?.[0].body).toMatchObject({
    company_name: 'Northstar Labs',
    title: 'Applied AI Product Builder',
    canonical_url: 'https://jobs.example.com/northstar',
    location_text: 'Remote',
    description_text: 'Build useful agent workflows.',
    application_url: 'https://jobs.example.com/northstar/apply',
    origin: 'user'
  })
  expect(jobCreate.mock.calls[0]?.[0].body.idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
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
