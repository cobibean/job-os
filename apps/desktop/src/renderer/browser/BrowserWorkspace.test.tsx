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

test.each(['saving', 'saved'] as const)('navigation hides %s feedback for a different listing in the same tab', status => {
  const browser = controller()
  const savedUrl = browser.activeTab!.url
  const props = {
    browser, browserRepaired: false, browserRepairReasons: [], jobs: [], onSaveJob: vi.fn(),
    saveStates: { first: { status, message: 'Feedback for the first listing', sourceUrl: savedUrl, operationId: 'capture-a' } }
  }
  const { rerender } = render(<BrowserWorkspace {...props} />)
  expect(screen.getByText('Feedback for the first listing')).not.toBeNull()
  const nextTab = { ...browser.activeTab!, url: 'https://example.com/new-listing' }
  rerender(<BrowserWorkspace {...props} browser={{ ...browser, activeTab: nextTab, state: { ...browser.state, tabs: [nextTab] } }} />)
  expect(screen.queryByText('Feedback for the first listing')).toBeNull()
  const save = screen.getByRole('button', { name: 'Save this job to JobOS' }) as HTMLButtonElement
  expect(save.textContent).toContain('Save job')
  expect(save.disabled).toBe(false)
})

test('association options distinguish multiple saved roles at the same company', () => {
  const browser = controller()
  const job = { jobId: 'one', company: 'Example Co', title: 'Builder', status: 'discovered' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/one', discoveredAt: '', lastSeenAt: '' }
  render(<BrowserWorkspace browser={browser} browserRepaired={false} browserRepairReasons={[]} jobs={[job, { ...job, jobId: 'two', title: 'Designer' }]} onSaveJob={vi.fn()} saveStates={{}} />)
  expect(screen.getByRole('option', { name: 'Example Co · Builder' })).not.toBeNull()
  expect(screen.getByRole('option', { name: 'Example Co · Designer' })).not.toBeNull()
})
