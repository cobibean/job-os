import { useEffect, useRef, useState } from 'react'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

interface PdfPreviewProps {
  bytes: ArrayBuffer
  page: number
  zoom: number
  onPageCount: (count: number) => void
}

export function PdfPreview({ bytes, page, zoom, onPageCount }: PdfPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    let task: import('pdfjs-dist').PDFDocumentLoadingTask | null = null
    const canvas = canvasRef.current
    if (canvas) {
      canvas.width = 0
      canvas.height = 0
      canvas.style.width = '0'
      canvas.style.height = '0'
    }
    import('pdfjs-dist').then(pdfjs => {
      if (!active) return null
      pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker
      task = pdfjs.getDocument({ data: new Uint8Array(bytes.slice(0)) })
      return task.promise
    }).then(async document => {
      if (!active || !document) return
      onPageCount(document.numPages)
      const pdfPage = await document.getPage(Math.min(page, document.numPages))
      if (!active || !canvasRef.current) return
      const viewport = pdfPage.getViewport({ scale: zoom })
      const ratio = window.devicePixelRatio || 1
      const canvas = canvasRef.current
      const context = canvas.getContext('2d')
      if (!context) throw new Error('PDF canvas is unavailable')
      canvas.width = Math.floor(viewport.width * ratio)
      canvas.height = Math.floor(viewport.height * ratio)
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`
      await pdfPage.render({
        canvas,
        canvasContext: context,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        viewport
      }).promise
      if (active) setError(null)
    }).catch(error => {
      if (active) setError(error instanceof Error ? error.message : 'PDF preview failed')
    })
    return () => {
      active = false
      task?.destroy()
    }
  }, [bytes, onPageCount, page, zoom])

  return (
    <div className="pdf-stage">
      {error ? <div className="document-error" role="alert">{error}</div> : null}
      <canvas aria-label={`Resume PDF page ${page}`} className="pdf-page" ref={canvasRef} />
    </div>
  )
}
