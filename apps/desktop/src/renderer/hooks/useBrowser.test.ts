import { expect, test } from 'vitest'

import type { BrowserState } from '../../shared/contracts'
import { BROWSER_SAFE_TITLE_FALLBACK } from '../../shared/browserPersistence'
import { browserStateForPersistence } from './useBrowser'

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
