import { Previewer } from 'pagedjs'

import type { EditableDocument } from '../../../../shared/editableDocuments'
import { renderEditableDocumentHtml } from '../../../../shared/document-rendering/editableDocumentHtml'

interface JobOsPrintBridge {
  onPayload(callback: (payload: { document: EditableDocument; allowUnresolvedSuggestions: boolean }) => void): void
  ready(pageCount: number): void
  failed(message: string): void
}

declare global {
  interface Window {
    jobosPrint?: JobOsPrintBridge
  }
}

async function waitForAssets(root: ParentNode): Promise<void> {
  await document.fonts.ready
  const images = [...root.querySelectorAll('img')]
  await Promise.all(images.map(async image => {
    if (!image.complete) {
      await new Promise<void>((resolve, reject) => {
        image.addEventListener('load', () => resolve(), { once: true })
        image.addEventListener('error', () => reject(new Error('Print image failed to load')), { once: true })
      })
    }
    if (typeof image.decode === 'function') await image.decode()
  }))
}

const bridge = window.jobosPrint
if (!bridge) throw new Error('JobOS print bridge is unavailable')

bridge.onPayload(payload => {
  void (async () => {
    try {
      const html = renderEditableDocumentHtml(payload.document, {
        allowUnresolvedSuggestions: payload.allowUnresolvedSuggestions
      })
      const parsed = new DOMParser().parseFromString(html, 'text/html')
      const source = document.querySelector<HTMLElement>('#print-source')
      const pages = document.querySelector<HTMLElement>('#print-pages')
      if (!source || !pages) throw new Error('Print renderer mount point is unavailable')
      for (const style of parsed.head.querySelectorAll('style')) document.head.append(style.cloneNode(true))
      source.innerHTML = parsed.body.innerHTML
      source.hidden = false
      await waitForAssets(source)
      const flow = await new Previewer().preview(source, [], pages)
      source.remove()
      await waitForAssets(pages)
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      bridge.ready(Math.max(1, flow.total))
    } catch (error) {
      bridge.failed(error instanceof Error ? error.message : 'Print rendering failed')
    }
  })()
})
