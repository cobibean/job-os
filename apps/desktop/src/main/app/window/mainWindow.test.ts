import { afterEach, expect, test, vi } from 'vitest'
import { createProfileSwitchCoordinator } from '../../installation-profiles/profileSwitchCoordinator.js'

const electron = vi.hoisted(() => {
  class Emitter {
    listeners = new Map<string, Array<(...arguments_: never[]) => void>>()
    on(name: string, listener: (...arguments_: never[]) => void) { this.listeners.set(name, [...this.listeners.get(name) ?? [], listener]); return this }
    once(name: string, listener: (...arguments_: never[]) => void) { return this.on(name, listener) }
    removeListener(name: string, listener: (...arguments_: never[]) => void) { this.listeners.set(name, (this.listeners.get(name) ?? []).filter(value => value !== listener)); return this }
    emit(name: string, ...arguments_: never[]) { for (const listener of this.listeners.get(name) ?? []) listener(...arguments_) }
  }
  const permissionCheck = vi.fn()
  const permissionRequest = vi.fn()
  const rendererSession = { setPermissionCheckHandler: permissionCheck, setPermissionRequestHandler: permissionRequest }
  class Window extends Emitter {
    static instances: Window[] = []
    options: Record<string, unknown>
    destroyed = false
    shown = false
    webContents = Object.assign(new Emitter(), {
      setWindowOpenHandler: vi.fn(),
      isLoadingMainFrame: vi.fn(() => false),
      isDestroyed: vi.fn(() => false),
      send: vi.fn()
    })
    loadFile = vi.fn(async () => undefined)
    loadURL = vi.fn(async () => undefined)
    show = vi.fn(() => { this.shown = true })
    close = vi.fn()
    isDestroyed = vi.fn(() => this.destroyed)
    constructor(options: Record<string, unknown>) { super(); this.options = options; Window.instances.push(this) }
  }
  const ipcMain = new Emitter()
  return { Window, ipcMain, rendererSession }
})

vi.mock('electron', () => ({
  BrowserWindow: electron.Window,
  ipcMain: electron.ipcMain,
  session: { defaultSession: electron.rendererSession, fromPartition: vi.fn(() => electron.rendererSession) }
}))

import { createMainWindow } from './mainWindow.js'

afterEach(() => {
  for (const window of electron.Window.instances) window.emit('closed')
  electron.Window.instances.length = 0
  electron.ipcMain.listeners.clear()
  vi.useRealTimers()
  vi.clearAllMocks()
})

async function safetyWindow() {
  let quitting = true
  const setSafetyRequester = vi.fn()
  const cancelAppQuit = vi.fn(() => { quitting = false })
  const quitApp = vi.fn()
  const result = await createMainWindow({
    preloadPath: '/desktop/preload.cjs', rendererRoot: '/desktop/renderer', enableLargerThanScreen: false,
    isAppQuitting: () => quitting, cancelAppQuit, quitApp, setSafetyRequester,
    attachWindowFeatures: () => ({ cleanup: vi.fn() }), afterShow: vi.fn()
  })
  const window = result as unknown as InstanceType<typeof electron.Window>
  const request = setSafetyRequester.mock.calls[0]![0] as (reason: 'window-close' | 'profile-switch') => Promise<boolean>
  const finishProfileSwitch = setSafetyRequester.mock.calls[0]![1] as (committed: boolean) => void
  const acknowledge = (id: string, value: unknown, sender: unknown = window.webContents) => {
    electron.ipcMain.emit('jobos:window:prepare-close-result', { sender } as never, id as never, value as never)
  }
  const close = () => {
    const event = { preventDefault: vi.fn() }
    window.emit('close', event as never)
    return event
  }
  return { window, request, finishProfileSwitch, acknowledge, close, cancelAppQuit, quitApp }
}

test('repeated Quit while one confirmation waits preserves quit intent and sends one request', async () => {
  const actions = await safetyWindow()
  actions.close()
  actions.close()
  expect(actions.window.webContents.send).toHaveBeenCalledTimes(1)
  expect(actions.cancelAppQuit).not.toHaveBeenCalled()
  const id = actions.window.webContents.send.mock.calls[0]![1] as string
  actions.acknowledge(id, true, {})
  expect(actions.quitApp).not.toHaveBeenCalled()
  actions.acknowledge(id, { promptPending: true })
  actions.acknowledge(id, true)
  await Promise.resolve()
  expect(actions.quitApp).toHaveBeenCalledOnce()
  expect(actions.cancelAppQuit).not.toHaveBeenCalled()
})

test('renderer crash cancels a waiting close and a late answer cannot close the app', async () => {
  const actions = await safetyWindow()
  actions.close()
  const id = actions.window.webContents.send.mock.calls[0]![1] as string
  actions.acknowledge(id, { promptPending: true })
  actions.window.webContents.emit('render-process-gone')
  await Promise.resolve()
  expect(actions.cancelAppQuit).toHaveBeenCalledOnce()
  expect(actions.window.webContents.send).toHaveBeenCalledWith('jobos:window:prepare-close-cancelled', id)
  actions.acknowledge(id, true)
  expect(actions.quitApp).not.toHaveBeenCalled()
})

test.each([true, false])('the profile coordinator reuses approved safety only for a confirmed transition, success %s', async success => {
  const actions = await safetyWindow()
  const requests: string[] = []
  actions.window.webContents.send.mockImplementation((channel, id, reason) => {
    if (channel !== 'jobos:window:prepare-close') return
    requests.push(reason)
    queueMicrotask(() => actions.acknowledge(id, reason === 'profile-switch'))
  })
  let relaunchClose: { preventDefault: ReturnType<typeof vi.fn> } | undefined
  const coordinator = createProfileSwitchCoordinator({
    getBrowserManager: () => null,
    requestWorkspaceSafety: () => actions.request('profile-switch'),
    finishWorkspaceSafety: actions.finishProfileSwitch,
    getClient: () => ({ activate: async () => {
      if (!success) throw new Error('Activation outcome unknown')
      return { switch_id: 'switch-1', to_profile_id: 'profile-new' }
    } }) as never,
    getRuntimeState: () => ({
      runtime: { apiBaseUrl: 'http://127.0.0.1:8766', deviceId: 'device', mode: 'local-service' },
      deviceToken: 'synthetic-token', connectivity: { state: 'connected', checkedAt: '', installationProfileId: 'profile-old' }
    }),
    apiLifecycle: { ensureApiReady: vi.fn() }, sourceApi: { stop: vi.fn(), rollbackProfileSwitch: vi.fn() },
    probe: vi.fn(async () => ({ state: 'connected', checkedAt: '', installationProfileId: 'profile-new' })),
    relaunchAndQuit: () => { relaunchClose = actions.close() }
  })
  const result = coordinator.complete({ profileId: 'profile-new', expectedRegistryRevision: 2, activationIdempotencyKey: 'activation-key' })
  if (success) {
    await result
    expect(relaunchClose?.preventDefault).not.toHaveBeenCalled()
    expect(requests).toEqual(['profile-switch'])
  } else {
    await expect(result).rejects.toThrow('Activation outcome unknown')
    expect(actions.window.webContents.send.mock.calls.some(([channel]) => channel === 'jobos:window:prepare-close-cancelled')).toBe(true)
    expect(actions.close().preventDefault).toHaveBeenCalledOnce()
    await Promise.resolve()
    expect(requests).toEqual(['profile-switch', 'window-close'])
    expect(actions.quitApp).not.toHaveBeenCalled()
  }
})

test('constructs the trusted renderer window with locked security and window-scoped cleanup', async () => {
  const cleanup = vi.fn()
  const attachmentAfterShow = vi.fn()
  const afterShow = vi.fn()
  const setSafetyRequester = vi.fn()
  const window = await createMainWindow({
    rendererPartition: 'persist:renderer-profile',
    preloadPath: '/desktop/dist/preload/preload.cjs',
    rendererRoot: '/desktop/dist/renderer',
    enableLargerThanScreen: false,
    isAppQuitting: () => false,
    cancelAppQuit: vi.fn(),
    quitApp: vi.fn(),
    setSafetyRequester,
    attachWindowFeatures: vi.fn(() => ({ cleanup, afterShow: attachmentAfterShow })),
    afterShow
  })
  expect((window as unknown as InstanceType<typeof electron.Window>).options).toMatchObject({
    webPreferences: {
      allowRunningInsecureContent: false,
      contextIsolation: true,
      nodeIntegration: false,
      preload: '/desktop/dist/preload/preload.cjs',
      sandbox: true,
      partition: 'persist:renderer-profile',
      webSecurity: true
    }
  })
  expect(window.webContents.setWindowOpenHandler).toHaveBeenCalled()
  expect(electron.rendererSession.setPermissionCheckHandler).toHaveBeenCalled()
  expect(electron.rendererSession.setPermissionRequestHandler).toHaveBeenCalled()
  expect(afterShow).toHaveBeenCalledWith(window)
  const concreteWindow = window as unknown as InstanceType<typeof electron.Window>
  expect(concreteWindow.show.mock.invocationCallOrder[0]).toBeLessThan(attachmentAfterShow.mock.invocationCallOrder[0]!)
  expect(attachmentAfterShow.mock.invocationCallOrder[0]).toBeLessThan(afterShow.mock.invocationCallOrder[0]!)
  concreteWindow.emit('closed')
  expect(cleanup).toHaveBeenCalledOnce()
  expect(setSafetyRequester).toHaveBeenLastCalledWith(expect.any(Function))
})
