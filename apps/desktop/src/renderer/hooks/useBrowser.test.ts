import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { BrowserRestoreState, BrowserState, BrowserTab } from '../../shared/contracts'
import { BROWSER_SAFE_TITLE_FALLBACK } from '../../shared/browserPersistence'
import { browserStateForPersistence, findOpenJobListingTab, useBrowser } from './useBrowser'

afterEach(cleanup)

function browserTab(
  tabId: string,
  url: string,
  associatedJobId: string | null = null
): BrowserTab {
  return {
    tabId,
    url,
    title: tabId,
    faviconUrl: null,
    associatedJobId,
    loading: false,
    canGoBack: false,
    canGoForward: false,
    crashed: false,
    error: null,
    blockedUrl: null
  }
}

function browserState(title: string): BrowserState {
  return {
    tabs: [{
      tabId: 'remote',
      url: 'https://example.com/jobs?view=safe',
      title,
      faviconUrl: null,
      associatedJobId: null,
      loading: false,
      canGoBack: false,
      canGoForward: false,
      crashed: false,
      error: null,
      blockedUrl: null
    }],
    activeTabId: 'remote',
    download: null,
    notice: null
  }
}

test('renderer persistence redacts remote-controlled credential assignments in titles', () => {
  const persisted = browserStateForPersistence(browserState(
    '%ZZAWS%5FSECRET%5FACCESS%5FKEY%3Dexample-value'
  ))

  expect(persisted.tabs[0]?.title).toBe(BROWSER_SAFE_TITLE_FALLBACK)
  expect(JSON.stringify(persisted)).not.toContain('example-value')
})

test('renderer persistence preserves safe ordinary page titles', () => {
  expect(browserStateForPersistence(browserState('Planning Session: Q3')).tabs[0]?.title).toBe(
    'Planning Session: Q3'
  )
})

test('associated job tab wins even when its current URL differs', () => {
  const associated = browserTab('associated', 'https://example.com/company', 'job-7')

  expect(findOpenJobListingTab([associated], 'job-7', 'https://example.com/jobs/7')).toBe(associated)
})

test('safe-normalized canonical URL matches an unassociated tab', () => {
  const listing = browserTab('listing', 'https://EXAMPLE.com/jobs/7#details')

  expect(findOpenJobListingTab([listing], 'job-7', 'https://example.com/jobs/7')).toBe(listing)
})

test('unrelated tabs do not match a job listing', () => {
  expect(findOpenJobListingTab(
    [browserTab('gmail', 'https://mail.google.com/')],
    'job-7',
    'https://example.com/jobs/7'
  )).toBeNull()
})

test('association match wins over an earlier URL-only match', () => {
  const urlMatch = browserTab('url-match', 'https://example.com/jobs/7')
  const associated = browserTab('associated', 'https://example.com/company', 'job-7')

  expect(findOpenJobListingTab(
    [urlMatch, associated],
    'job-7',
    'https://example.com/jobs/7'
  )).toBe(associated)
})

function renderBrowserHook(initialTabs: BrowserTab[]) {
  const browserState = (activeTabId: string | null, tabs = initialTabs): BrowserState => ({
    tabs,
    activeTabId,
    download: null,
    notice: null
  })
  const restoreState: BrowserRestoreState = {
    tabs: initialTabs.map(({ loading: _loading, canGoBack: _canGoBack, canGoForward: _canGoForward,
      crashed: _crashed, error: _error, blockedUrl: _blockedUrl, ...tab }) => tab),
    activeTabId: initialTabs[0]?.tabId ?? null
  }
  const select = vi.fn((tabId: string) => Promise.resolve(browserState(tabId)))
  const create = vi.fn((url = 'about:blank', jobId: string | null = null) => Promise.resolve(browserState(
    'created',
    [...initialTabs, browserTab('created', url, jobId)]
  )))
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      browser: {
        restore: vi.fn().mockResolvedValue(browserState(restoreState.activeTabId)),
        subscribe: vi.fn().mockReturnValue(() => undefined),
        setBounds: vi.fn().mockResolvedValue(undefined),
        select,
        create
      }
    }
  })
  const hook = renderHook(() => useBrowser(restoreState, true, false, 'layout', vi.fn()))
  return { ...hook, create, select }
}

test('openJobListing selects an existing associated tab without creating one', async () => {
  const { result, select, create } = renderBrowserHook([
    browserTab('gmail', 'https://mail.google.com/'),
    browserTab('listing', 'https://example.com/company', 'job-7')
  ])
  await waitFor(() => expect(result.current.restorationReady).toBe(true))

  await act(async () => { await result.current.openJobListing('job-7', 'https://example.com/jobs/7') })

  expect(select).toHaveBeenCalledWith('listing')
  expect(create).not.toHaveBeenCalled()
})

test('openJobListing selects an existing normalized-URL tab without creating one', async () => {
  const { result, select, create } = renderBrowserHook([
    browserTab('gmail', 'https://mail.google.com/'),
    browserTab('listing', 'https://EXAMPLE.com/jobs/7#details')
  ])
  await waitFor(() => expect(result.current.restorationReady).toBe(true))

  await act(async () => { await result.current.openJobListing('job-7', 'https://example.com/jobs/7') })

  expect(select).toHaveBeenCalledWith('listing')
  expect(create).not.toHaveBeenCalled()
})

test('openJobListing creates an associated tab when no listing is open', async () => {
  const { result, select, create } = renderBrowserHook([
    browserTab('gmail', 'https://mail.google.com/')
  ])
  await waitFor(() => expect(result.current.restorationReady).toBe(true))

  await act(async () => { await result.current.openJobListing('job-7', 'https://example.com/jobs/7') })

  expect(create).toHaveBeenCalledWith('https://example.com/jobs/7', 'job-7')
  expect(select).not.toHaveBeenCalled()
})
