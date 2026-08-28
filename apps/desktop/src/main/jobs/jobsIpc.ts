import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { BrowserState, JobSortMode, JobStatus } from '../../shared/contracts.js'
import { canonicalListingUrl, safeApplicationUrl, validatedBrowserJobExtraction } from './browserJobExtraction.js'
import type { createMainJobsClient } from './jobs.js'

type JobsClient = ReturnType<typeof createMainJobsClient>
export interface BrowserJobAccess {
  getState: () => BrowserState
  contextToken: (tabId: string) => { url: string, documentEpoch: number, loading: boolean }
  associate: (tabId: string, jobId: string | null) => BrowserState
}

export function registerJobsIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => JobsClient,
  getBrowserManager: () => BrowserJobAccess | null
): void {
  const sortModes = new Set<JobSortMode>(['manual', 'recent', 'alphabetical', 'status'])
  const statuses = new Set<JobStatus>(['discovered', 'scored', 'reviewed', 'shortlisted', 'apply_now', 'maybe', 'stretch', 'skipped', 'applied', 'interviewing', 'closed', 'archived'])

  ipc.handle('jobos:jobs:get-state', event => trusted(event).getState())
  ipc.handle('jobos:jobs:list', (event, sort: JobSortMode, query?: string, statusGroup?: string) => {
    if (!sortModes.has(sort)) throw new Error('Invalid job ordering')
    return trusted(event).list(sort, query, statusGroup)
  })
  ipc.handle('jobos:jobs:inspect', (event, jobId: string) => {
    if (typeof jobId !== 'string' || !jobId || jobId.length > 512) throw new Error('Invalid job')
    return trusted(event).inspect(jobId)
  })
  ipc.handle('jobos:jobs:select', (event, conversationId: string, jobId: string) => {
    if (typeof conversationId !== 'string' || !/^conv_[A-Za-z0-9_-]{1,128}$/.test(conversationId)) throw new Error('Invalid agent conversation')
    if (typeof jobId !== 'string' || !jobId || jobId.length > 512) throw new Error('Invalid job selection')
    return trusted(event).select(conversationId, jobId)
  })
  ipc.handle('jobos:jobs:reorder', (event, jobIds: string[]) => {
    if (!Array.isArray(jobIds) || jobIds.some(jobId => typeof jobId !== 'string') || new Set(jobIds).size !== jobIds.length) throw new Error('Invalid manual job order')
    return trusted(event).reorder(jobIds)
  })
  ipc.handle('jobos:jobs:set-sort', (event, sort: JobSortMode) => {
    if (!sortModes.has(sort)) throw new Error('Invalid job ordering')
    return trusted(event).setSort(sort)
  })
  ipc.handle('jobos:jobs:update-status', (event, jobId: string, status: JobStatus) => {
    if (typeof jobId !== 'string' || !jobId || !statuses.has(status)) throw new Error('Invalid job status change')
    return trusted(event).updateStatus(jobId, status)
  })
  ipc.handle('jobos:jobs:remove-demo', (event, jobId: string) => {
    if (typeof jobId !== 'string' || !jobId) throw new Error('Invalid demo job')
    return trusted(event).removeDemo(jobId)
  })
  ipc.handle('jobos:jobs:save-from-browser', async (event, rawTabId: unknown, expectedUrl: unknown, rawExtraction: unknown, idempotencyKey: unknown) => {
    const client = trusted(event)
    const browserManager = getBrowserManager()
    if (!browserManager) throw new Error('Browser surface unavailable')
    if (typeof rawTabId !== 'string' || !rawTabId || rawTabId.length > 128) throw new Error('Invalid browser tab')
    if (typeof expectedUrl !== 'string' || !expectedUrl || expectedUrl.length > 8192) throw new Error('Invalid browser address')
    if (typeof idempotencyKey !== 'string' || !idempotencyKey || idempotencyKey.length > 128) throw new Error('Invalid idempotency key')
    const extraction = validatedBrowserJobExtraction(rawExtraction)
    const before = browserManager.getState()
    const sourceTab = before.tabs.find(tab => tab.tabId === rawTabId)
    const sourceContext = browserManager.contextToken(rawTabId)
    if (before.activeTabId !== rawTabId || sourceTab?.url !== expectedUrl || sourceContext.url !== expectedUrl || sourceContext.loading) {
      throw new Error('The browser listing changed before saving finished. Retry on the intended listing.')
    }
    if (sourceTab.associatedJobId) throw new Error('This browser listing is already associated with a job')
    const canonicalUrl = canonicalListingUrl(expectedUrl, extraction.canonicalUrl)
    const result = await client.createFromBrowser({
      ...extraction,
      canonicalUrl,
      applicationUrl: safeApplicationUrl(extraction.applicationUrl)
    }, idempotencyKey)
    const after = browserManager.getState()
    const currentTab = after.tabs.find(tab => tab.tabId === rawTabId)
    const currentContext = browserManager.contextToken(rawTabId)
    if (after.activeTabId !== rawTabId || currentTab?.url !== expectedUrl || currentContext.url !== expectedUrl || currentContext.loading
      || currentContext.documentEpoch !== sourceContext.documentEpoch || currentTab.associatedJobId !== sourceTab.associatedJobId) return result
    browserManager.associate(rawTabId, result.job.jobId)
    return { ...result, associated: true }
  })
}
