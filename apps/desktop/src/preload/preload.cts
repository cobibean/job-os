import { contextBridge, ipcRenderer } from 'electron'

import type { JobOsRendererBridge } from '../shared/contracts.js'

const bridge: JobOsRendererBridge = Object.freeze({
  connectivity: Object.freeze({
    get: () => ipcRenderer.invoke('jobos:connectivity:get')
  })
})

contextBridge.exposeInMainWorld('jobos', bridge)
