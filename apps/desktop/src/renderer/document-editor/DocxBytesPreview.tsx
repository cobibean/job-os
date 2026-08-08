import { FileWarning } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { renderAsync } from 'docx-preview'

interface DocxBytesPreviewProps {
  bytes: ArrayBuffer
  filename: string
  label: string
  sha256: string
}

type PreviewState = 'loading' | 'ready' | 'error'

export function DocxBytesPreview({ bytes, filename, label, sha256 }: DocxBytesPreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<PreviewState>('loading')
  const [message, setMessage] = useState(`Loading ${label.toLowerCase()}…`)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let active = true
    const blockNavigation = (event: Event) => {
      const target = event.target
      if (target instanceof Element && target.closest('a, area, form')) {
        event.preventDefault()
        event.stopPropagation()
      }
    }
    setState('loading')
    setMessage(`Loading ${label.toLowerCase()}…`)
    container.replaceChildren()
    const staging = document.createElement('div')
    container.addEventListener('click', blockNavigation, true)
    container.addEventListener('submit', blockNavigation, true)
    renderAsync(bytes, staging, staging, {
      breakPages: true,
      ignoreLastRenderedPageBreak: false,
      useBase64URL: true
    }).then(() => {
      if (!active) return
      for (const link of staging.querySelectorAll('a, area')) {
        link.removeAttribute('href')
        link.setAttribute('aria-disabled', 'true')
      }
      container.replaceChildren(...staging.childNodes)
      setState('ready')
      setMessage(`${label} · ${filename} · SHA-256 ${sha256.slice(0, 12)}…`)
    }).catch(() => {
      if (!active) return
      setState('error')
      setMessage(`${label} could not be rendered safely.`)
    })
    return () => {
      active = false
      container.removeEventListener('click', blockNavigation, true)
      container.removeEventListener('submit', blockNavigation, true)
      container.replaceChildren()
    }
  }, [bytes, filename, label, sha256])

  return (
    <div className={`original-docx-preview ${state}`}>
      <div className="original-docx-status" role="status">
        {state === 'error' ? <FileWarning aria-hidden="true" size={18} /> : null}
        <span>{message}</span>
      </div>
      <div aria-busy={state === 'loading'} aria-label={`${label} preview`} className="original-docx-pages" ref={containerRef} />
    </div>
  )
}
