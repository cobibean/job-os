// @vitest-environment node

import { expect, test } from 'vitest'

import { EventEmitter } from 'node:events'

import type { BrowserWindow, Dialog, Session, WebContents, WebContentsView } from 'electron'
import { vi } from 'vitest'

import { BrowserManager, DEFAULT_BROWSER_URL, isOrdinaryWebUrl, normalizeBrowserInput, remoteBrowserPreferences, safeBlockedExternalUrl } from './browser.js'
import { BROWSER_PERSISTENCE_LIMITS, sanitizeBrowserUrlForPersistence } from '../shared/browserPersistence.js'

test('browser input keeps ordinary sites free and turns plain text into a search', () => {
  expect(normalizeBrowserInput('https://mail.google.com/mail/u/0/')).toBe('https://mail.google.com/mail/u/0/')
  expect(normalizeBrowserInput('jobs.example.com/opening/7')).toBe('https://jobs.example.com/opening/7')
  expect(normalizeBrowserInput('staff product manager jobs')).toBe('https://www.google.com/search?q=staff%20product%20manager%20jobs')
  expect(normalizeBrowserInput('')).toBe(DEFAULT_BROWSER_URL)
})

test('remote navigation policy admits only ordinary web pages', () => {
  expect(isOrdinaryWebUrl('https://example.com')).toBe(true)
  expect(isOrdinaryWebUrl('http://127.0.0.1:8765/listing')).toBe(true)
  expect(isOrdinaryWebUrl('mailto:person@example.com')).toBe(false)
  expect(isOrdinaryWebUrl('file:///etc/passwd')).toBe(false)
  expect(isOrdinaryWebUrl('javascript:alert(1)')).toBe(false)
})

test('remote browser content has no Node, preload, webview, or privileged renderer bridge', () => {
  const preferences = remoteBrowserPreferences()
  expect(preferences).toMatchObject({
    nodeIntegration: false,
    contextIsolation: true,
    sandbox: true,
    webSecurity: true,
    webviewTag: false
  })
  expect(preferences).not.toHaveProperty('preload')
})

test('persisted browser URLs remove credentials, auth parameters, and fragments', () => {
  expect(sanitizeBrowserUrlForPersistence('https://person:password@example.com/callback?code=secret&view=inbox#token')).toBe(
    'https://example.com/callback?view=inbox'
  )
  expect(sanitizeBrowserUrlForPersistence(
    'https://example.com/download?view=compact&ticket=a&assertion=b&sig=c&sessionid=d&oauth_verifier=e&X-Amz-Credential=f&X-Amz-Signature=g&X-Goog-Signature=h#fragment'
  )).toBe('https://example.com/download?view=compact')
  expect(safeBlockedExternalUrl('mailto:person@example.com?subject=Hello&ticket=secret#fragment')).toBe(
    'mailto:person@example.com?subject=Hello'
  )
  expect(safeBlockedExternalUrl('javascript:alert(1)')).toBeNull()
})

test('tabs reorder and bounds changes reuse the same live WebContentsView instances', async () => {
  const views: WebContentsView[] = []
  const attached: WebContentsView[] = []
  const createView = () => {
    const events = new EventEmitter()
    let url = ''
    const contents = Object.assign(events, {
      navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
      loadURL: vi.fn(async (next: string) => { url = next }),
      getURL: () => url,
      setWindowOpenHandler: vi.fn(),
      isDestroyed: () => false,
      close: vi.fn(),
      reload: vi.fn(),
      stop: vi.fn()
    })
    const view = { webContents: contents, setBounds: vi.fn() } as unknown as WebContentsView
    views.push(view)
    return view
  }
  const permissionRequest = vi.fn()
  const browserSession = Object.assign(new EventEmitter(), {
    setPermissionCheckHandler: vi.fn(),
    setPermissionRequestHandler: permissionRequest
  }) as unknown as Session
  const window = {
    contentView: {
      addChildView: (view: WebContentsView) => attached.push(view),
      removeChildView: (view: WebContentsView) => attached.splice(attached.indexOf(view), 1)
    },
    webContents: { send: vi.fn() },
    isDestroyed: () => false
  } as unknown as BrowserWindow
  const manager = new BrowserManager({
    window,
    browserSession,
    createView,
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    clipboard: { writeText: vi.fn() },
    downloadsPath: '/tmp'
  })
  const callback = vi.fn()
  const requestHandler = permissionRequest.mock.calls[0]?.[0]
  requestHandler(null as unknown as WebContents, 'geolocation', callback)
  expect(callback).toHaveBeenCalledWith(false)
  expect(manager.getState().notice).toContain('blocked geolocation permission')

  await manager.restore({
    tabs: [
      { tabId: 'google', url: 'https://www.google.com/', title: 'Google', faviconUrl: null, associatedJobId: null },
      { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', faviconUrl: null, associatedJobId: null }
    ],
    activeTabId: 'google'
  })
  const originalViews = [...views]
  manager.setBounds({ x: 240, y: 130, width: 720, height: 640, visible: true })
  manager.reorder(['gmail', 'google'])
  manager.select('gmail')
  manager.setBounds({ x: 310, y: 180, width: 540, height: 510, visible: true })
  manager.select('google')
  manager.associate('google', 'job-7')

  expect(views).toEqual(originalViews)
  expect(views).toHaveLength(2)
  expect(manager.getState().tabs.map(tab => tab.tabId)).toEqual(['gmail', 'google'])
  expect(manager.getState().tabs.find(tab => tab.tabId === 'google')).toMatchObject({
    url: 'https://www.google.com/',
    associatedJobId: 'job-7'
  })
  expect(attached).toEqual([views[0]])
  expect(views[0]?.setBounds).toHaveBeenLastCalledWith({ x: 310, y: 180, width: 540, height: 510 })

  await manager.close('gmail')
  await manager.create('https://jobs.example.com/roles/8', 'job-8')
  expect(manager.getState().tabs).toHaveLength(2)
  expect(manager.getState().tabs.at(-1)).toMatchObject({
    url: 'https://jobs.example.com/roles/8',
    associatedJobId: 'job-8'
  })
  expect(views).toHaveLength(3)
  expect(browserSession.listenerCount('will-download')).toBe(1)
  manager.dispose()
  expect(browserSession.listenerCount('will-download')).toBe(0)
})

test('main-process emission enforces Workspace bounds and keeps later saves viable', async () => {
  const views: WebContentsView[] = []
  const createView = () => {
    const events = new EventEmitter()
    let url = ''
    const contents = Object.assign(events, {
      navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
      loadURL: vi.fn(async (next: string) => { url = next }), getURL: () => url,
      setWindowOpenHandler: vi.fn(), isDestroyed: () => false, close: vi.fn(), reload: vi.fn(), stop: vi.fn()
    })
    const view = { webContents: contents, setBounds: vi.fn() } as unknown as WebContentsView
    views.push(view)
    return view
  }
  const browserSession = Object.assign(new EventEmitter(), {
    setPermissionCheckHandler: vi.fn(), setPermissionRequestHandler: vi.fn()
  }) as unknown as Session
  const clipboard = { writeText: vi.fn() }
  const manager = new BrowserManager({
    window: {
      contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
      webContents: { send: vi.fn() }, isDestroyed: () => false
    } as unknown as BrowserWindow,
    browserSession, createView, clipboard,
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    downloadsPath: '/tmp'
  })

  await manager.restore({ tabs: [], activeTabId: null })
  expect(manager.getState().tabs[0]?.url).toBe(DEFAULT_BROWSER_URL)
  await manager.restore({
    tabs: [{ tabId: 'authoritative', url: 'https://mail.google.com/', title: 'Gmail', faviconUrl: null, associatedJobId: null }],
    activeTabId: 'authoritative'
  })
  expect(manager.getState().tabs.map(tab => tab.tabId)).toEqual(['authoritative'])
  for (let index = 1; index < BROWSER_PERSISTENCE_LIMITS.tabs; index += 1) await manager.create(`https://example.com/${index}`)
  const beforeLimit = manager.getState().tabs.map(tab => tab.tabId)
  await manager.create('https://example.com/blocked-51')
  expect(manager.getState().tabs.map(tab => tab.tabId)).toEqual(beforeLimit)
  expect(manager.getState().notice).toContain('Tab limit reached')

  const firstContents = views[1]?.webContents as unknown as EventEmitter
  await manager.navigate('authoritative', `https://example.com/${'u'.repeat(9000)}`)
  firstContents.emit('did-navigate')
  firstContents.emit('page-title-updated', {}, 'T'.repeat(700))
  firstContents.emit('page-favicon-updated', {}, [`https://example.com/${'f'.repeat(9000)}`])
  const durable = manager.getState()
  expect(durable.tabs).toHaveLength(BROWSER_PERSISTENCE_LIMITS.tabs)
  expect(durable.tabs[0]?.url).toBe('https://example.com/')
  expect(durable.tabs[0]?.title).toHaveLength(BROWSER_PERSISTENCE_LIMITS.title)
  expect(durable.tabs[0]?.faviconUrl).toBe('https://example.com/')
  expect(JSON.stringify({ tabs: durable.tabs, activeTabId: durable.activeTabId }).length).toBeGreaterThan(0)

  const navigationEvent = { preventDefault: vi.fn() }
  firstContents.emit('will-navigate', navigationEvent, 'mailto:person@example.com?subject=Hello&ticket=secret#fragment')
  expect(navigationEvent.preventDefault).toHaveBeenCalled()
  expect(manager.getState().tabs[0]?.blockedUrl).toBe('mailto:person@example.com?subject=Hello')
  manager.copyBlockedUrl(manager.getState().tabs[0]!.tabId)
  expect(clipboard.writeText).toHaveBeenCalledWith('mailto:person@example.com?subject=Hello')

  const authoritativeView = views[1]
  if (!authoritativeView) throw new Error('Authoritative browser view was not created')
  const openHandler = (authoritativeView.webContents.setWindowOpenHandler as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]
  if (!openHandler) throw new Error('Window-open policy was not installed')
  openHandler({ url: 'slack://channel/open?ticket=secret&team=safe' })
  expect(manager.getState().tabs[0]?.blockedUrl).toBe('slack://channel/open?team=safe')
  manager.dispose()
})
