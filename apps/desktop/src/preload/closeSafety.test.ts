// @vitest-environment node
import { afterEach, expect, test, vi } from 'vitest'
import { readFile } from 'node:fs/promises'
import { stripTypeScriptTypes } from 'node:module'
import { runInNewContext } from 'node:vm'
import type { CloseSafetyRequest, JobOsRendererBridge } from '../shared/contracts'

const electron = vi.hoisted(() => ({
  contextBridge: { exposeInMainWorld: vi.fn() },
  ipcRenderer: { sendSync: vi.fn(), send: vi.fn(), invoke: vi.fn(), on: vi.fn(), removeListener: vi.fn() }
}))

afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); vi.resetModules() })

async function install() {
  const source = await readFile(new URL('./preload.cts', import.meta.url), 'utf8')
  const script = stripTypeScriptTypes(source)
    .replace("import { contextBridge, ipcRenderer } from 'electron'", 'const { contextBridge, ipcRenderer } = electron;')
  runInNewContext(script, { electron, setInterval, clearInterval })
  return electron.contextBridge.exposeInMainWorld.mock.calls[0]![1] as JobOsRendererBridge
}

test('one close subscription sends scoped prompt heartbeats until completion', async () => {
  vi.useFakeTimers()
  const bridge = await install()
  let finish!: (safe: boolean) => void
  let request!: CloseSafetyRequest
  const unsubscribe = bridge.lifecycle.subscribePrepareClose((_reason, context) => {
    request = context!
    request.setPromptPending(true)
    return new Promise(resolve => { finish = resolve })
  })
  const listener = electron.ipcRenderer.on.mock.calls.find(([channel]) => channel === 'jobos:window:prepare-close')![1]
  listener({}, 'request-1', 'window-close')
  await Promise.resolve()
  await vi.advanceTimersByTimeAsync(20_000)
  expect(electron.ipcRenderer.send.mock.calls.filter(([, id, payload]) => id === 'request-1' && payload?.promptPending === true)).toHaveLength(5)
  request.setPromptPending(false)
  const sent = electron.ipcRenderer.send.mock.calls.length
  await vi.advanceTimersByTimeAsync(10_000)
  expect(electron.ipcRenderer.send).toHaveBeenCalledTimes(sent)
  finish(true)
  await vi.runAllTimersAsync()
  expect(electron.ipcRenderer.send).toHaveBeenLastCalledWith('jobos:window:prepare-close-result', 'request-1', true)
  unsubscribe()
  expect(electron.ipcRenderer.removeListener).toHaveBeenCalledTimes(2)
})

test('late consent cannot approve a cancelled request and profile consent stays cancellable until the action settles', async () => {
  vi.useFakeTimers()
  const bridge = await install()
  const cancel = vi.fn()
  let finish!: (safe: boolean) => void
  bridge.lifecycle.subscribePrepareClose((_reason, request) => {
    request!.onCancelled(cancel)
    request!.setPromptPending(true)
    return new Promise(resolve => { finish = resolve })
  })
  const listener = electron.ipcRenderer.on.mock.calls.find(([channel]) => channel === 'jobos:window:prepare-close')![1]
  const cancelled = electron.ipcRenderer.on.mock.calls.find(([channel]) => channel === 'jobos:window:prepare-close-cancelled')![1]
  listener({}, 'request-1', 'window-close')
  await Promise.resolve()
  cancelled({}, 'stale-request')
  expect(cancel).not.toHaveBeenCalled()
  cancelled({}, 'request-1')
  finish(true)
  await vi.runAllTimersAsync()
  expect(cancel).toHaveBeenCalledOnce()
  expect(electron.ipcRenderer.send.mock.calls.some(([, id, value]) => id === 'request-1' && value === true)).toBe(false)
  listener({}, 'request-2', 'profile-switch')
  await Promise.resolve()
  finish(true)
  await vi.runAllTimersAsync()
  expect(electron.ipcRenderer.send).toHaveBeenLastCalledWith('jobos:window:prepare-close-result', 'request-2', true)
  cancelled({}, 'request-2')
  expect(cancel).toHaveBeenCalledTimes(2)
})
