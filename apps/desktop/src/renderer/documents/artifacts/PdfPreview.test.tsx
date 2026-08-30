import { cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

const { getDocument } = vi.hoisted(() => ({
  getDocument: vi.fn()
}))

vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({
  default: 'pdf.worker.min.mjs'
}))

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument
}))

import { PdfPreview } from './PdfPreview'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  getDocument.mockReset()
})

test('renders PDF pixels without loading annotations or JavaScript actions', async () => {
  const renderPage = vi.fn().mockReturnValue({ promise: Promise.resolve() })
  const getPageAnnotations = vi.fn()
  const getPageJSActions = vi.fn()
  const getDocumentJSActions = vi.fn()
  const hasDocumentJSActions = vi.fn()
  const getAnnotationsByType = vi.fn()
  getDocument.mockReturnValue({
    destroy: vi.fn(),
    promise: Promise.resolve({
      numPages: 1,
      getJSActions: getDocumentJSActions,
      hasJSActions: hasDocumentJSActions,
      getAnnotationsByType,
      getPage: vi.fn().mockResolvedValue({
        getAnnotations: getPageAnnotations,
        getJSActions: getPageJSActions,
        getViewport: vi.fn().mockReturnValue({ width: 100, height: 120 }),
        render: renderPage
      })
    })
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D)

  render(<PdfPreview bytes={new Uint8Array([37, 80, 68, 70]).buffer} page={1} zoom={1} onPageCount={vi.fn()} />)

  await waitFor(() => expect(renderPage).toHaveBeenCalled())
  expect(getPageAnnotations).not.toHaveBeenCalled()
  expect(getPageJSActions).not.toHaveBeenCalled()
  expect(getDocumentJSActions).not.toHaveBeenCalled()
  expect(hasDocumentJSActions).not.toHaveBeenCalled()
  expect(getAnnotationsByType).not.toHaveBeenCalled()
})

test('reports a malformed PDF without rendering an annotation layer', async () => {
  getDocument.mockReturnValue({
    destroy: vi.fn(),
    promise: Promise.reject(new Error('Invalid PDF structure'))
  })

  const view = render(<PdfPreview bytes={new Uint8Array([0, 1, 2]).buffer} page={1} zoom={1} onPageCount={vi.fn()} />)

  await waitFor(() => expect(view.getByRole('alert').textContent).toBe('Invalid PDF structure'))
  expect(view.container.querySelector('.annotationLayer')).toBeNull()
})

test('destroys the loading task and removes its canvas when the document surface exits', async () => {
  const destroy = vi.fn()
  const renderPage = vi.fn().mockReturnValue({ promise: Promise.resolve() })
  getDocument.mockReturnValue({
    destroy,
    promise: Promise.resolve({
      numPages: 1,
      getPage: vi.fn().mockResolvedValue({
        getViewport: vi.fn().mockReturnValue({ width: 100, height: 120 }),
        render: renderPage
      })
    })
  })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D)

  const view = render(<PdfPreview bytes={new Uint8Array([37, 80, 68, 70]).buffer} page={1} zoom={1} onPageCount={vi.fn()} />)
  const canvas = view.getByLabelText('Resume PDF page 1')
  await waitFor(() => expect(renderPage).toHaveBeenCalled())

  view.unmount()

  expect(destroy).toHaveBeenCalledOnce()
  expect(canvas.isConnected).toBe(false)
})
