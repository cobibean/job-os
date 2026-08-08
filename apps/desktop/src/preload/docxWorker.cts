const { contextBridge, ipcRenderer } = require('electron') as typeof import('electron')

import type { DocxWorkerEnvelope, DocxWorkerResponse } from '../shared/docxWorker.js'

contextBridge.exposeInMainWorld('jobosDocxWorker', Object.freeze({
  subscribe(handler: (envelope: DocxWorkerEnvelope) => void) {
    const listener = (_event: unknown, envelope: DocxWorkerEnvelope) => handler(envelope)
    ipcRenderer.on('jobos:docx-worker:request', listener)
    return () => ipcRenderer.removeListener('jobos:docx-worker:request', listener)
  },
  respond(response: DocxWorkerResponse) {
    ipcRenderer.send('jobos:docx-worker:response', response)
  }
}))
