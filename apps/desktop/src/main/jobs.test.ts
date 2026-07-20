import { expect, test } from 'vitest'

import { JobEventDecoder } from './jobs.js'

test('the desktop event decoder preserves SSE events split across network chunks', () => {
  const decoder = new JobEventDecoder()

  const first = decoder.push('id: 7\nevent: jobos\ndata: {"event_id":7,"event_type":"job_status_')
  const second = decoder.push('changed","origin":"mcp","job_id":"job-1"}\n\n')

  expect(first).toEqual([])
  expect(second).toEqual([
    { eventId: 7, eventType: 'job_status_changed', origin: 'mcp', jobId: 'job-1' }
  ])
})
