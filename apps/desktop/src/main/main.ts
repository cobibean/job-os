import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { app, BrowserWindow, ipcMain, session } from 'electron'

import type { ConnectivitySnapshot } from '../shared/contracts.js'
import { probeConnectivity } from './connectivity.js'
import { isTrustedRendererUrl } from './security.js'

const currentDirectory = path.dirname(fileURLToPath(import.meta.url))
const rendererRoot = path.resolve(currentDirectory, '../renderer')
const developmentUrl = process.env.VITE_DEV_SERVER_URL
const developmentOrigin = developmentUrl ? new URL(developmentUrl).origin : undefined

function disconnectedCredentialSnapshot(): ConnectivitySnapshot {
  return {
    state: 'disconnected',
    checkedAt: new Date().toISOString(),
    message: 'Device credential unavailable'
  }
}

function registerConnectivityInterface(): void {
  ipcMain.handle('jobos:connectivity:get', async event => {
    const senderUrl = event.senderFrame?.url ?? ''
    if (!isTrustedRendererUrl(senderUrl, { developmentOrigin, rendererRoot })) {
      if (process.env.JOBOS_CAPTURE_PATH) {
        console.info('[JobOS capture] rejected renderer URL', senderUrl)
      }
      throw new Error('Untrusted renderer')
    }

    const deviceToken = process.env.JOBOS_DEVICE_TOKEN
    if (!deviceToken) return disconnectedCredentialSnapshot()

    const snapshot = await probeConnectivity({
      baseUrl: process.env.JOBOS_API_BASE_URL ?? 'http://100.123.109.19:8766',
      deviceToken
    })
    if (process.env.JOBOS_CAPTURE_PATH) {
      console.info('[JobOS capture] connectivity', JSON.stringify(snapshot))
    }
    return snapshot
  })
}

async function createWindow(): Promise<BrowserWindow> {
  const window = new BrowserWindow({
    width: 1440,
    height: 1024,
    useContentSize: true,
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
      preload: path.resolve(currentDirectory, '../preload/preload.cjs'),
      sandbox: true,
      webSecurity: true
    }
  })

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', event => event.preventDefault())

  if (developmentUrl) {
    await window.loadURL(developmentUrl)
  } else {
    await window.loadFile(path.join(rendererRoot, 'index.html'))
  }

  window.show()

  const capturePath = process.env.JOBOS_CAPTURE_PATH
  if (capturePath) {
    setTimeout(async () => {
      const image = await window.webContents.capturePage()
      await writeFile(capturePath, image.toPNG())
      app.quit()
    }, 1_200)
  }

  return window
}

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false)
  })
  registerConnectivityInterface()
  await createWindow()

  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) await createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
