import path from 'node:path'

import { BrowserWindow, ipcMain, session } from 'electron'
import type { IpcMainEvent } from 'electron'

import { applyDenyAllPermissionPolicy } from '../security/security.js'
import { RendererSafetyCoordinator } from './mainWindowLifecycle.js'

export interface WindowFeatureAttachment {
  afterShow?: () => void
  cleanup: () => void
}

export async function createMainWindow(options: {
  rendererPartition?: string
  preloadPath: string
  rendererRoot: string
  developmentUrl?: string
  enableLargerThanScreen: boolean
  isAppQuitting: () => boolean
  cancelAppQuit: () => void
  quitApp: () => void
  setSafetyRequester: (
    request: (reason: 'window-close' | 'profile-switch') => Promise<boolean>,
    finishProfileSwitch?: (committed: boolean) => void
  ) => void
  attachWindowFeatures: (window: BrowserWindow) => Promise<WindowFeatureAttachment> | WindowFeatureAttachment
  afterShow: (window: BrowserWindow) => void
}): Promise<BrowserWindow> {
  const window = new BrowserWindow({
    width: 1440,
    height: 1024,
    useContentSize: true,
    enableLargerThanScreen: options.enableLargerThanScreen,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: '#0f1114',
    show: false,
    title: 'JobOS',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      allowRunningInsecureContent: false,
      contextIsolation: true,
      nodeIntegration: false,
      preload: options.preloadPath,
      sandbox: true,
      ...(options.rendererPartition ? { partition: options.rendererPartition } : {}),
      webSecurity: true
    }
  })

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', event => event.preventDefault())
  const rendererSession = options.rendererPartition
    ? session.fromPartition(options.rendererPartition, { cache: true })
    : session.defaultSession
  applyDenyAllPermissionPolicy(rendererSession)

  let allowClose = false
  let closePending = false
  let approvedProfileSwitch: string | null = null
  const requestReasons = new Map<string, 'window-close' | 'profile-switch'>()
  const safety = new RendererSafetyCoordinator((requestId, reason) => {
    requestReasons.set(requestId, reason)
    if (!window.isDestroyed()) window.webContents.send('jobos:window:prepare-close', requestId, reason)
  }, 15_000, undefined, requestId => {
    requestReasons.delete(requestId)
    if (!window.isDestroyed() && !window.webContents.isDestroyed()) window.webContents.send('jobos:window:prepare-close-cancelled', requestId)
  })
  const finishProfileSwitch = (committed: boolean) => {
    if (!approvedProfileSwitch) return
    const requestId = approvedProfileSwitch
    approvedProfileSwitch = null
    if (committed) allowClose = true
    else if (!window.isDestroyed() && !window.webContents.isDestroyed()) window.webContents.send('jobos:window:prepare-close-cancelled', requestId)
  }
  options.setSafetyRequester(reason => safety.request(reason), finishProfileSwitch)
  const onPrepareCloseResult = (event: IpcMainEvent, requestId: unknown, safe: unknown) => {
    if (event.sender !== window.webContents) return
    if (safe && typeof safe === 'object' && 'promptPending' in safe) {
      safety.promptPending(requestId, safe.promptPending)
    } else {
      const reason = typeof requestId === 'string' ? requestReasons.get(requestId) : undefined
      if (!safety.resolve(requestId, safe)) return
      if (typeof requestId === 'string') requestReasons.delete(requestId)
      if (reason === 'profile-switch' && safe === true) approvedProfileSwitch = requestId as string
    }
  }
  ipcMain.on('jobos:window:prepare-close-result', onPrepareCloseResult)
  const rendererGone = () => { safety.dispose(); finishProfileSwitch(false) }
  window.webContents.on('render-process-gone', rendererGone)
  window.webContents.on('destroyed', rendererGone)
  window.on('close', event => {
    if (allowClose || window.webContents.isLoadingMainFrame()) return
    event.preventDefault()
    if (approvedProfileSwitch) { options.cancelAppQuit(); return }
    if (closePending) return
    closePending = true
    void safety.request('window-close').then(safe => {
      if (!safe) {
        closePending = false
        options.cancelAppQuit()
        return
      }
      allowClose = true
      if (options.isAppQuitting()) options.quitApp()
      else window.close()
    })
  })

  const attachment = await options.attachWindowFeatures(window)
  if (options.developmentUrl) await window.loadURL(options.developmentUrl)
  else await window.loadFile(path.join(options.rendererRoot, 'index.html'))
  window.show()
  attachment.afterShow?.()

  window.once('closed', () => {
    safety.dispose()
    options.setSafetyRequester(async () => false)
    ipcMain.removeListener('jobos:window:prepare-close-result', onPrepareCloseResult)
    attachment.cleanup()
  })
  options.afterShow(window)
  return window
}
