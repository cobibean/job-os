import { expect, test, vi } from 'vitest'

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
