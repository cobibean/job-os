import { FileWarning } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { renderAsync } from 'docx-preview'

interface OriginalDocxPreviewProps {
  artifactId: string
  sourceFilename: string | null
}

type PreviewState = 'loading' | 'ready' | 'error'

export function OriginalDocxPreview({ artifactId, sourceFilename }: OriginalDocxPreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<PreviewState>('loading')
  const [message, setMessage] = useState('Loading the checksum-verified original…')

  useEffect(() => {
    const bridge = window.jobos?.documents
    const container = containerRef.current
    if (!bridge || !container) {
      setState('error')
      setMessage('The trusted original-document bridge is unavailable.')
      return
    }
    let active = true
    const blockNavigation = (event: Event) => {
      const target = event.target
      if (target instanceof Element && target.closest('a, area, form')) {
        event.preventDefault()
        event.stopPropagation()
      }
    }
    container.addEventListener('click', blockNavigation, true)
    container.addEventListener('submit', blockNavigation, true)
    bridge.loadOriginalDocx(artifactId)
      .then(async payload => {
        if (!active) return
        const staging = document.createElement('div')
        await renderAsync(payload.bytes, staging, staging, {
          breakPages: true,
          ignoreLastRenderedPageBreak: false,
          useBase64URL: true
        })
        if (!active) return
        for (const link of staging.querySelectorAll('a, area')) {
          link.removeAttribute('href')
          link.setAttribute('aria-disabled', 'true')
        }
        container.replaceChildren(...staging.childNodes)
        setState('ready')
        setMessage(`Original ${payload.filename} · SHA-256 verified`)
      })
      .catch(() => {
        if (!active) return
        setState('error')
        setMessage('The original DOCX could not be verified or rendered safely.')
      })
    return () => {
      active = false
      container.removeEventListener('click', blockNavigation, true)
      container.removeEventListener('submit', blockNavigation, true)
      container.replaceChildren()
    }
  }, [artifactId])

  return (
    <div className={`original-docx-preview ${state}`}>
      <div className="original-docx-status" role="status">
        {state === 'error' ? <FileWarning aria-hidden="true" size={18} /> : null}
        <span>{message}</span>
        {sourceFilename && state === 'loading' ? <small>{sourceFilename}</small> : null}
      </div>
      <div aria-busy={state === 'loading'} aria-label="Original Word document preview" className="original-docx-pages" ref={containerRef} />
    </div>
  )
}
