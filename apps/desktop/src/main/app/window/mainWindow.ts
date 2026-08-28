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
  setSafetyRequester: (request: (reason: 'window-close' | 'profile-switch') => Promise<boolean>) => void
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
  const safety = new RendererSafetyCoordinator((requestId, reason) => {
    if (!window.isDestroyed()) window.webContents.send('jobos:window:prepare-close', requestId, reason)
  })
  options.setSafetyRequester(reason => safety.request(reason))
  const onPrepareCloseResult = (event: IpcMainEvent, requestId: unknown, safe: unknown) => {
    if (event.sender === window.webContents) safety.resolve(requestId, safe)
  }
  ipcMain.on('jobos:window:prepare-close-result', onPrepareCloseResult)
  window.on('close', event => {
    if (allowClose || window.webContents.isLoadingMainFrame()) return
    event.preventDefault()
    void safety.request('window-close').then(safe => {
      if (!safe) {
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
