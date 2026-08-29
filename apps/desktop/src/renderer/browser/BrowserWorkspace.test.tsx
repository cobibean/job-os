import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { BrowserController } from './useBrowser'
import { BrowserWorkspace } from './BrowserWorkspace'

afterEach(cleanup)

function controller(): BrowserController {
  const tabs = [
    {
      tabId: 'first', url: 'https://example.com/first', title: 'First listing', faviconUrl: null,
      associatedJobId: null, loading: false, canGoBack: false, canGoForward: false,
      error: null, crashed: false, blockedUrl: null
    },
    {
      tabId: 'second', url: 'https://example.com/second', title: 'Second listing', faviconUrl: null,
      associatedJobId: null, loading: false, canGoBack: true, canGoForward: false,
      error: null, crashed: false, blockedUrl: null
    }
  ]
  return {
    bridgeAvailable: true,
    restorationReady: true,
    state: { tabs, activeTabId: tabs[0]!.tabId, download: null, notice: null },
    activeTab: tabs[0]!,
    message: 'Browser ready',
    viewportRef: { current: null },
    reconcileExternalState: vi.fn(),
    openJobListing: vi.fn().mockResolvedValue(true),
    create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(),
    back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
  } as unknown as BrowserController
}

test('presents the workbench-lifetime browser controller without owning it', () => {
  const browser = controller()
  const onSaveJob = vi.fn()
  render(<BrowserWorkspace
    browser={browser}
    browserRepaired
    browserRepairReasons={['protected_title']}
    jobs={[]}
    onSaveJob={onSaveJob}
    saveStates={{}}
  />)

  const first = screen.getByRole('tab', { name: 'Select First listing' })
  const second = screen.getByRole('tab', { name: 'Select Second listing' })
  expect(first.getAttribute('aria-selected')).toBe('true')
  expect(second.getAttribute('tabindex')).toBe('-1')
  expect(screen.getByText('Credential-like title metadata was protected. No browser tabs were lost.')).not.toBeNull()

  fireEvent.keyDown(first, { key: 'ArrowRight' })
  expect(browser.select).toHaveBeenCalledWith('second')
  expect(document.activeElement).toBe(second)

  fireEvent.click(screen.getByRole('button', { name: 'Save this job to JobOS' }))
  expect(onSaveJob).toHaveBeenCalledWith(browser.activeTab)
})
