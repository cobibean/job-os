import { useLayoutEffect, useRef, type RefObject } from 'react'

interface ModalOptions {
  open?: boolean
  initialFocusRef?: RefObject<HTMLElement | null>
  returnFocusRef?: RefObject<HTMLElement | null>
  modalRootRef?: RefObject<HTMLElement | null>
}

const activeDialogs: HTMLElement[] = []
const inertElements = new Map<HTMLElement, { count: number; previous: boolean }>()

function focusableElements(dialog: HTMLElement): HTMLElement[] {
  return [...dialog.querySelectorAll<HTMLElement>(
    'button, input, select, textarea, a[href], [tabindex], [contenteditable="true"]'
  )].filter(element => {
    const style = window.getComputedStyle(element)
    return element.tabIndex >= 0 && !element.matches(':disabled')
      && !element.closest('[hidden], [inert], [aria-hidden="true"]')
      && style.display !== 'none' && style.visibility !== 'hidden'
  })
}

/** Shares modal behavior without changing each dialog's content or layout. */
export function useModalDialog(
  dialogRef: RefObject<HTMLElement | null>,
  onClose: () => void,
  { open = true, initialFocusRef, returnFocusRef, modalRootRef }: ModalOptions = {}
): void {
  const close = useRef(onClose)
  close.current = onClose

  useLayoutEffect(() => {
    const dialog = dialogRef.current
    if (!open || !dialog) return
    const returnTarget = returnFocusRef?.current
      ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    const background: HTMLElement[] = []
    // Dialogs may be inline or portalled. Inert siblings at every ancestor so
    // the dialog itself never inherits inert from its workspace container.
    for (let branch: HTMLElement = modalRootRef?.current ?? dialog; branch.parentElement; branch = branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (!(sibling instanceof HTMLElement) || sibling === branch) continue
        const record = inertElements.get(sibling)
        inertElements.set(sibling, record ? { ...record, count: record.count + 1 } : { count: 1, previous: sibling.inert === true })
        sibling.inert = true
        background.push(sibling)
      }
      if (branch.parentElement === document.body) break
    }
    activeDialogs.push(dialog)
    const isTop = () => activeDialogs.at(-1) === dialog
    const focusFirst = () => {
      const controls = focusableElements(dialog)
      const initial = initialFocusRef?.current
      ;(initial && controls.includes(initial) ? initial : controls[0] ?? dialog).focus()
    }
    focusFirst()

    const handleKey = (event: KeyboardEvent) => {
      if (!isTop()) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close.current()
      } else if (event.key === 'Tab') {
        const controls = focusableElements(dialog)
        const first = controls[0]
        const last = controls.at(-1)
        if (!first || !last) { event.preventDefault(); dialog.focus(); return }
        if (!dialog.contains(document.activeElement) || document.activeElement === dialog) {
          event.preventDefault()
          ;(event.shiftKey ? last : first).focus()
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus()
        }
      }
    }
    const containFocus = (event: FocusEvent) => {
      if (isTop() && event.target instanceof Node && !dialog.contains(event.target)) focusFirst()
    }
    document.addEventListener('keydown', handleKey, true)
    document.addEventListener('focusin', containFocus)
    return () => {
      document.removeEventListener('keydown', handleKey, true)
      document.removeEventListener('focusin', containFocus)
      const index = activeDialogs.indexOf(dialog)
      if (index !== -1) activeDialogs.splice(index, 1)
      for (const element of background) {
        const record = inertElements.get(element)!
        if (record.count > 1) inertElements.set(element, { ...record, count: record.count - 1 })
        else { element.inert = record.previous; inertElements.delete(element) }
      }
      requestAnimationFrame(() => {
        const top = activeDialogs.at(-1)
        if (returnTarget?.isConnected && (!top || top.contains(returnTarget))) returnTarget.focus()
      })
    }
  }, [dialogRef, open, initialFocusRef, returnFocusRef, modalRootRef])
}
