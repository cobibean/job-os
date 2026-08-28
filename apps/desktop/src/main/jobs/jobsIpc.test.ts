import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerJobsIpc } from './jobsIpc.js'

function registrar() {
  const handlers = new Map<string, (...arguments_: never[]) => unknown>()
  const ipc = { handle: (channel: string, handler: (...arguments_: never[]) => unknown) => handlers.set(channel, handler) } as unknown as Pick<IpcMain, 'handle'>
  const client = {
    getState: vi.fn(), list: vi.fn(), inspect: vi.fn(), select: vi.fn(), reorder: vi.fn(),
    setSort: vi.fn(), updateStatus: vi.fn(), removeDemo: vi.fn(),
    createFromBrowser: vi.fn(async () => ({ eventId: 1, created: true, job: { jobId: 'job-1' } }))
  }
  let browser: Record<string, unknown> | null = null
  registerJobsIpc(ipc, () => client as never, () => browser as never)
  return { handlers, client, setBrowser: (value: Record<string, unknown>) => { browser = value } }
}

test('registers all jobs channels and uses a live browser getter', async () => {
  const { handlers, client, setBrowser } = registrar()
  expect([...handlers.keys()]).toEqual([
    'jobos:jobs:get-state', 'jobos:jobs:list', 'jobos:jobs:inspect', 'jobos:jobs:select',
    'jobos:jobs:reorder', 'jobos:jobs:set-sort', 'jobos:jobs:update-status',
    'jobos:jobs:remove-demo', 'jobos:jobs:save-from-browser'
  ])
  const event = {} as IpcMainInvokeEvent
  await expect(handlers.get('jobos:jobs:save-from-browser')?.(event, 'tab-1', 'https://example.com/job', {}, 'save-key')).rejects.toThrow('Browser surface unavailable')
  const tab = { tabId: 'tab-1', url: 'https://example.com/job', associatedJobId: null }
  const context = { url: tab.url, loading: false, documentEpoch: 4 }
  const associate = vi.fn()
  setBrowser({ getState: vi.fn(() => ({ activeTabId: 'tab-1', tabs: [tab] })), contextToken: vi.fn(() => context), associate })
  await expect(handlers.get('jobos:jobs:save-from-browser')?.(event, 'tab-1', tab.url, {
    companyName: 'Example', title: 'Engineer', canonicalUrl: tab.url,
    locationText: 'Remote', descriptionText: 'Role', applicationUrl: 'https://example.com/apply'
  }, 'save-key')).resolves.toMatchObject({ associated: true })
  expect(client.createFromBrowser).toHaveBeenCalledTimes(1)
  expect(associate).toHaveBeenCalledWith('tab-1', 'job-1')
})

test('preserves jobs validation messages and ordering', () => {
  const { handlers } = registrar()
  const event = {} as IpcMainInvokeEvent
  expect(() => handlers.get('jobos:jobs:list')?.(event, 'unknown')).toThrow('Invalid job ordering')
  expect(() => handlers.get('jobos:jobs:select')?.(event, 'bad', 'job')).toThrow('Invalid agent conversation')
  expect(() => handlers.get('jobos:jobs:reorder')?.(event, ['job', 'job'])).toThrow('Invalid manual job order')
})
