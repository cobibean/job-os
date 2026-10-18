import { afterEach, expect, it, vi } from 'vitest'

import type { EditableDocument } from '../../../../shared/editableDocuments'
import { createBlankDocument, defaultDocumentSettings } from '../../../../shared/editableDocumentSchema'

const { preview } = vi.hoisted(() => ({ preview: vi.fn() }))
vi.mock('pagedjs', () => ({ Previewer: class { preview = preview } }))

afterEach(() => {
  document.head.replaceChildren()
  document.body.replaceChildren()
  Reflect.deleteProperty(document, 'fonts')
  Reflect.deleteProperty(window, 'jobosPrint')
  vi.unstubAllGlobals()
  vi.resetModules()
  vi.clearAllMocks()
})

it('paginates detached content and processes document styles before declaring print readiness', async () => {
  document.body.innerHTML = '<div id="print-source" hidden></div><div id="print-pages"></div>'
  Object.defineProperty(document, 'fonts', { configurable: true, value: { ready: Promise.resolve() } })
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1 })
  const content = createBlankDocument('resume')
  content.content![1]!.content![0]!.content = [{ type: 'text', text: '(FAKE) Printable resume content' }]
  const documentFixture: EditableDocument = {
    schemaVersion: 1, documentId: 'edoc_ABCDEFGHIJKLMNOPQRSTUVWX', jobId: '(FAKE)-print-job',
    documentKey: 'resume', documentLabel: 'Resume', revision: 1, content,
    settings: { ...defaultDocumentSettings(), header: { left: '(FAKE) Header', center: '', right: '', firstPageDifferent: false } },
    comments: [], sourceArtifactId: null, sourceFilename: null, sourceSha256: null,
    publishedRevision: null, importReport: { sourceFilename: null, importedAt: null, issues: [] },
    createdAt: '2026-10-18T00:00:00Z', updatedAt: '2026-10-18T00:00:00Z'
  }
  let finished!: () => void
  const completion = new Promise<void>(resolve => { finished = resolve })
  const ready = vi.fn(() => finished())
  const failed = vi.fn(() => finished())
  preview.mockImplementation(async (source: Node, stylesheets: unknown, pages: HTMLElement) => {
    // Attached sources cause Paged.js to rebuild html/body ancestors inside a page.
    expect(source).toBeInstanceOf(DocumentFragment)
    expect(source.isConnected).toBe(false)
    expect(source.parentNode).toBeNull()
    expect(source.textContent).toContain('(FAKE) Printable resume content')
    // An empty array skips parsing @page and position:running declarations.
    expect(stylesheets).toBeUndefined()
    expect(document.head.textContent).toContain('position:running(jobosHeader)')
    pages.append(source.cloneNode(true))
    return { total: 1 }
  })
  window.jobosPrint = {
    onPayload: callback => callback({ document: documentFixture, allowUnresolvedSuggestions: false }),
    ready, failed
  }

  await import('./documentPrint')
  await completion

  expect(failed).not.toHaveBeenCalled()
  expect(preview).toHaveBeenCalledOnce()
  expect(ready).toHaveBeenCalledWith(1)
  expect(document.querySelector('#print-source')).toBeNull()
  expect(document.querySelector('#print-pages')?.textContent).toContain('(FAKE) Printable resume content')
  expect(document.querySelector('#print-pages html, #print-pages body')).toBeNull()
})
