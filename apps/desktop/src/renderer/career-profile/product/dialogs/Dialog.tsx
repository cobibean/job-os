import { X } from 'lucide-react'
import { useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useModalDialog } from '../../../app/dialogs/useModalDialog'

export function Dialog({ children, className = '', layerClassName = '', label, onClose }: {
  children: ReactNode
  className?: string
  layerClassName?: string
  label: string
  onClose: () => void
}) {
  const dialog = useRef<HTMLElement>(null)
  const layer = useRef<HTMLDivElement>(null)
  useModalDialog(dialog, onClose, { modalRootRef: layer })

  return createPortal(
    <div className={`career-product-modal-layer ${layerClassName}`} ref={layer}>
      <button aria-hidden="true" className="career-product-backdrop" onClick={onClose} tabIndex={-1} type="button" />
      <section
        aria-label={label}
        aria-modal="true"
        className={`career-product-dialog ${className}`}
        ref={dialog}
        role="dialog"
        tabIndex={-1}
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
