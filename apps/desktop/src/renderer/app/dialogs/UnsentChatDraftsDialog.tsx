import { useEffect } from 'react'

import type { CloseSafetyRequest } from '../../../shared/contracts'
import { Dialog } from '../../career-profile/product/dialogs/Dialog'

export function UnsentChatDraftsDialog({ count, busy, checking, request, onCancel, onConfirm }: {
  count: number
  busy: boolean
  checking: boolean
  request?: CloseSafetyRequest
  onCancel: () => void
  onConfirm: () => void
}) {
  useEffect(() => {
    request?.setPromptPending(!checking)
    return () => request?.setPromptPending(false)
  }, [checking, request])

  const switchingWithoutDrafts = checking && count === 0
  return (
    <Dialog label={switchingWithoutDrafts ? 'Switching profiles…' : 'Discard unsent chat messages?'} layerClassName="app-close-safety-layer" onClose={onCancel}>
      <div className="career-product-dialog-body">
        <h3>{switchingWithoutDrafts ? 'Switching profiles…' : 'Discard unsent chat messages?'}</h3>
        {switchingWithoutDrafts
          ? <p>This workspace will remain read-only until the profile switch finishes.</p>
          : <p>{count === 1 ? '1 chat has an unsent message.' : `${count} chats have unsent messages.`} They are not saved when JobOS closes or switches profiles.</p>}
        {busy && <p role="status">Wait for the pending chat action to finish before discarding unsent messages.</p>}
        {checking && <p role="status">Preparing to continue…</p>}
      </div>
      {!switchingWithoutDrafts && <footer className="career-product-dialog-actions">
        <button className="career-secondary-button" disabled={checking} onClick={onCancel} type="button">Keep working</button>
        <button className="career-primary-button danger" disabled={busy || checking} onClick={onConfirm} type="button">Discard messages and continue</button>
      </footer>}
    </Dialog>
  )
}
