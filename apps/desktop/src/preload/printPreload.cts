const { contextBridge, ipcRenderer } = require('electron') as typeof import('electron')

const PAYLOAD_CHANNEL = 'jobos:document-print-payload'
const READY_CHANNEL = 'jobos:document-print-ready'
const FAILED_CHANNEL = 'jobos:document-print-failed'
const MAX_PRINT_PAYLOAD_BYTES = 25 * 1024 * 1024

contextBridge.exposeInMainWorld('jobosPrint', Object.freeze({
  onPayload(callback: (payload: unknown) => void) {
    if (typeof callback !== 'function') throw new Error('Print callback is required')
    ipcRenderer.once(PAYLOAD_CHANNEL, (_event, payload: unknown) => {
      const serialized = JSON.stringify(payload)
      if (Buffer.byteLength(serialized, 'utf8') > MAX_PRINT_PAYLOAD_BYTES) {
        ipcRenderer.send(FAILED_CHANNEL, 'Print payload exceeded the local safety limit')
        return
      }
      callback(payload)
    })
  },
  ready(pageCount: number) {
    if (!Number.isSafeInteger(pageCount) || pageCount < 1 || pageCount > 10_000) {
      ipcRenderer.send(FAILED_CHANNEL, 'Print renderer returned an invalid page count')
      return
    }
    ipcRenderer.send(READY_CHANNEL, pageCount)
  },
  failed(message: string) {
    ipcRenderer.send(FAILED_CHANNEL, typeof message === 'string' ? message.slice(0, 500) : 'Print rendering failed')
  }
}))
