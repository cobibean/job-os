import { X } from 'lucide-react'
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export function Dialog({ children, className = '', label, onClose }: {
  children: ReactNode
  className?: string
  label: string
  onClose: () => void
}) {
  const dialog = useRef<HTMLElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)

  useEffect(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const modalLayer = dialog.current?.closest('.career-product-modal-layer')
    const background = Array.from(document.body.children)
      .filter(element => element !== modalLayer) as HTMLElement[]
    background.forEach(element => { element.inert = true })
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus()
    return () => {
      background.forEach(element => { element.inert = false })
      const target = returnFocus.current
      window.requestAnimationFrame(() => {
        if (target?.isConnected) target.focus()
      })
    }
  }, [])

  useEffect(() => {
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      onClose()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  const handleKeys = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Tab') return
    const focusable = Array.from(dialog.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])'
    ) ?? [])
    if (focusable.length === 0) return
    const first = focusable[0]!
    const last = focusable.at(-1)!
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return createPortal(
    <div className="career-product-modal-layer">
      <button aria-hidden="true" className="career-product-backdrop" onClick={onClose} tabIndex={-1} type="button" />
      <section
        aria-label={label}
        aria-modal="true"
        className={`career-product-dialog ${className}`}
        onKeyDown={handleKeys}
        ref={dialog}
        role="dialog"
      >
        {children}
      </section>
    </div>,
    document.body
  )
}

export function DialogHeading({ closeLabel, eyebrow, onClose, title }: {
  closeLabel: string
  eyebrow: string
  onClose: () => void
  title: string
}) {
  return (
    <header className="career-product-dialog-heading">
      <div><span className="career-kicker">{eyebrow}</span><h3>{title}</h3></div>
      <button aria-label={closeLabel} className="career-icon-action" onClick={onClose} type="button"><X aria-hidden="true" size={16} /></button>
    </header>
  )
}
