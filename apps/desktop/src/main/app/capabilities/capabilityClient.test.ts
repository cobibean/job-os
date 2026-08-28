// @vitest-environment node

import { expect, test, vi } from 'vitest'

import { dispatchCapabilityCommand, startDesktopCapabilityClient } from './capabilityClient.js'

const state = {
  tabs: [{ tabId: 'tab-1', url: 'https://example.com/', title: 'Example', faviconUrl: null,
    associatedJobId: null, loading: false, canGoBack: false, canGoForward: false,
    error: null, crashed: false, blockedUrl: null }],
  activeTabId: 'tab-1', download: null, notice: null
}

test('capability dispatch validates commands and calls only fixed BrowserManager operations', async () => {
  const manager = {
    inspect: vi.fn(() => state),
    navigate: vi.fn(async () => state),
    snapshot: vi.fn(async () => ({ tabId: 'tab-1', url: 'https://example.com/',
      title: 'Example', text: 'Apply', requestedTextStart: 0, textStart: 0, textLength: 5,
      totalTextLength: 5, hasMore: false, pageRevision: 'a'.repeat(64), scrollY: 0,
      scrollHeight: 800, viewportHeight: 800,
      elements: [{ targetId: 't_1', role: 'link', name: 'Apply', disabled: false,
        href: 'https://example.com/apply' }] }))
  }
  const inspected = await dispatchCapabilityCommand(manager, {
    type: 'command', command_id: 'cmd_12345678', idempotency_key: 'inspect-1', origin: 'mcp',
    deadline_at: new Date(Date.now() + 1000).toISOString(), command: 'tabs.inspect', arguments: {}
  })
  const snapshot = await dispatchCapabilityCommand(manager, {
    type: 'command', command_id: 'cmd_22345678', idempotency_key: 'snapshot-1', origin: 'mcp',
    deadline_at: new Date(Date.now() + 1000).toISOString(), command: 'page.snapshot',
    arguments: { tab_id: 'tab-1' }
  })
  const invalid = await dispatchCapabilityCommand(manager, {
    type: 'command', command_id: 'cmd_32345678', idempotency_key: 'bad-1', origin: 'mcp',
    deadline_at: new Date(Date.now() + 1000).toISOString(), command: 'script.execute',
    arguments: { javascript: 'document.cookie' }
  })

  expect(inspected.state).toBe('completed')
  expect(snapshot.data).toMatchObject({
    tab_id: 'tab-1', requested_text_start: 0, text_start: 0, text_length: 5,
    next_text_start: null, total_text_length: 5, has_more: false,
    page_revision: 'a'.repeat(64), scroll_y: 0,
    scroll_height: 800, viewport_height: 800,
    targets: [{ target_id: 't_1', href: 'https://example.com/apply' }]
  })
  expect(invalid).toMatchObject({ state: 'failed', error: { code: 'validation' } })
  expect(JSON.stringify(invalid)).not.toContain('document.cookie')
})

test('snapshot pagination stays below the tool result budget with many long LinkedIn targets', async () => {
  const longHref = `https://www.linkedin.com/jobs/view/4431837844/?trackingId=${'x'.repeat(8_000)}`
  const snapshot = vi.fn(async (_tabId: string, textStart = 0, textLength = 12_000) => ({
    tabId: 'tab-1', url: 'https://www.linkedin.com/jobs/view/4431837844/',
    title: 'AI Product Manager', text: 'T'.repeat(textLength), requestedTextStart: textStart,
    textStart, textLength, totalTextLength: 48_123, hasMore: true,
    pageRevision: 'b'.repeat(64), scrollY: 0, scrollHeight: 8_000, viewportHeight: 900,
    elements: Array.from({ length: 100 }, (_, index) => ({
      targetId: `t_${index + 1}`, role: 'link', name: 'See job', disabled: false, href: longHref
    }))
  }))
  const result = await dispatchCapabilityCommand({ inspect: () => state, snapshot }, {
    type: 'command', command_id: 'cmd_oversized1', idempotency_key: 'snapshot-large-1',
    origin: 'mcp', deadline_at: new Date(Date.now() + 1000).toISOString(),
    command: 'page.snapshot', arguments: {
      tab_id: 'tab-1', text_start: 12_000, text_length: 12_000, include_targets: true
    }
  })

  expect(snapshot).toHaveBeenCalledWith('tab-1', 12_000, 12_000, true)
  expect(result.data).toMatchObject({
    requested_text_start: 12_000, text_length: 12_000, next_text_start: 24_000
  })
  expect(JSON.stringify(result).length).toBeLessThan(21_000)
  const targets = result.data?.targets
  expect(Array.isArray(targets)).toBe(true)
  expect(Array.isArray(targets) ? targets.length : 100).toBeLessThan(100)
})

test('capability dispatch applies the documented default scroll amount', async () => {
  const scroll = vi.fn(async () => state)
  const result = await dispatchCapabilityCommand({ inspect: () => state, scroll }, {
    type: 'command', command_id: 'cmd_42345678', idempotency_key: 'scroll-1', origin: 'mcp',
    deadline_at: new Date(Date.now() + 1000).toISOString(), command: 'page.scroll',
    arguments: { tab_id: 'tab-1', direction: 'down' }
  })

  expect(result.state).toBe('completed')
  expect(scroll).toHaveBeenCalledWith('tab-1', 'down', 600)
})

test('capability dispatch preserves BrowserManager method binding', async () => {
  const manager = {
    current: state,
    inspect() { return this.current },
    select(_tabId: string) { return this.current }
  }
  const result = await dispatchCapabilityCommand(manager, {
    type: 'command', command_id: 'cmd_52345678', idempotency_key: 'select-1', origin: 'mcp',
    deadline_at: new Date(Date.now() + 1000).toISOString(), command: 'tab.select',
    arguments: { tab_id: 'tab-1' }
  })

  expect(result.state).toBe('completed')
  expect(result.data).toMatchObject({ active_tab_id: 'tab-1' })
})

test('capability dispatch lets the agent associate a live tab with its saved job', async () => {
  const associate = vi.fn(() => ({
    ...state,
    tabs: [{ ...state.tabs[0]!, associatedJobId: 'job-7' }]
  }))
  const result = await dispatchCapabilityCommand({ inspect: () => state, associate }, {
    type: 'command', command_id: 'cmd_62345678', idempotency_key: 'associate-1', origin: 'mcp',
    deadline_at: new Date(Date.now() + 1000).toISOString(), command: 'tab.associate',
    arguments: { tab_id: 'tab-1', job_id: 'job-7' }
  })

  expect(result.state).toBe('completed')
  expect(associate).toHaveBeenCalledWith('tab-1', 'job-7')
  expect(result.data).toMatchObject({ tabs: [{ tab_id: 'tab-1', associated_job_id: 'job-7' }] })
})

test('capability dispatch can create a recovery tab without stealing focus', async () => {
  const create = vi.fn(async () => ({
    ...state,
    tabs: [...state.tabs, { ...state.tabs[0]!, tabId: 'recovery-tab', url: 'https://example.com/job' }]
  }))
  const result = await dispatchCapabilityCommand({ inspect: () => state, create }, {
    type: 'command', command_id: 'cmd_72345678', idempotency_key: 'create-background-1', origin: 'mcp',
    deadline_at: new Date(Date.now() + 1000).toISOString(), command: 'tab.create',
    arguments: { url: 'https://example.com/job', associated_job_id: null, activate: false }
  })

  expect(result.state).toBe('completed')
  expect(create).toHaveBeenCalledWith('https://example.com/job', null, false)
  expect(result.data).toMatchObject({ active_tab_id: 'tab-1' })
})

test('capability client authenticates in the first frame and keeps the token out of its URL', () => {
  const sent: string[] = []
  const listeners = new Map<string, (event: { data?: string }) => void>()
  const socket = {
    readyState: 1,
    addEventListener: vi.fn((name: string, listener: (event: { data?: string }) => void) => listeners.set(name, listener)),
    send: vi.fn((value: string) => sent.push(value)),
    close: vi.fn()
  }
  const factory = vi.fn((_url: string) => socket)
  const stop = startDesktopCapabilityClient({ inspect: () => state }, {
    baseUrl: 'http://127.0.0.1:8766', deviceToken: 'super-secret-device-token',
    deviceId: 'primary-device'
  }, {
    socketFactory: factory,
    setTimer: vi.fn(() => ({} as NodeJS.Timeout)),
    clearTimer: vi.fn()
  })
  listeners.get('open')?.({})

  expect(factory).toHaveBeenCalledWith('ws://127.0.0.1:8766/v1/desktop/capabilities')
  expect(sent).toEqual([JSON.stringify({ type: 'authenticate', token: 'super-secret-device-token', device_id: 'primary-device' })])
  expect(String(factory.mock.calls[0]?.[0])).not.toContain('super-secret-device-token')
  stop()
  expect(socket.close).toHaveBeenCalled()
})

test('capability client does not connect until persisted browser restoration completes', async () => {
  let markReady!: () => void
  const browserReady = new Promise<void>(resolve => { markReady = resolve })
  const factory = vi.fn(() => ({
    readyState: 0, addEventListener: vi.fn(), send: vi.fn(), close: vi.fn()
  }))
  const stop = startDesktopCapabilityClient({ inspect: () => state }, {
    baseUrl: 'http://127.0.0.1:8766', deviceToken: 'token', deviceId: 'primary-device'
  }, { socketFactory: factory, browserReady })

  expect(factory).not.toHaveBeenCalled()
  markReady()
  await browserReady
  await Promise.resolve()
  expect(factory).toHaveBeenCalledTimes(1)
  stop()
})

test('stale socket callbacks cannot close or reconnect over a replacement socket', () => {
  const listeners = [
    new Map<string, (event: { data?: string }) => void>(),
    new Map<string, (event: { data?: string }) => void>()
  ]
  const sockets = listeners.map(socketListeners => ({
    readyState: 1,
    addEventListener: vi.fn((name: string, listener: (event: { data?: string }) => void) => {
      socketListeners.set(name, listener)
    }),
    send: vi.fn(), close: vi.fn()
  }))
  const timers: Array<() => void> = []
  const factory = vi.fn((_url: string) => sockets[factory.mock.calls.length - 1]!)
  const stop = startDesktopCapabilityClient({ inspect: () => state }, {
    baseUrl: 'http://127.0.0.1:8766', deviceToken: 'token', deviceId: 'primary-device'
  }, {
    socketFactory: factory,
    setTimer: vi.fn(callback => { timers.push(callback); return {} as NodeJS.Timeout }),
    clearTimer: vi.fn()
  })

  listeners[0]!.get('close')?.({})
  timers.shift()?.()
  expect(factory).toHaveBeenCalledTimes(2)
  listeners[0]!.get('error')?.({})
  listeners[0]!.get('close')?.({})
  expect(sockets[1]!.close).not.toHaveBeenCalled()
  expect(timers).toHaveLength(0)
  stop()
})
