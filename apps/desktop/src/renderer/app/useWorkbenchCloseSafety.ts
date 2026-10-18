import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

import type { CloseSafetyRequest } from '../../shared/contracts'
import type { AgentSessionsController } from '../agents/chat/useAgentSessions'
import { useCloseSafety, type PrepareClose } from './CloseSafetyContext'

interface Options {
  prepareDocumentClose: RefObject<() => Promise<boolean>>
  sessions: AgentSessionsController
  flushWorkspace: () => Promise<void>
}

interface PendingClose {
  reason: 'window-close' | 'profile-switch'
  request?: CloseSafetyRequest
  cancelled: boolean
  checking: boolean
  browserHidden?: boolean
  resolve?: (revisions: Map<string, number> | null) => void
  unregister?: () => void
}

function drafts(sessions: AgentSessionsController) {
  return Object.entries(sessions.sessions).filter(([, session]) => session.draft.trim())
}

export function useWorkbenchCloseSafety(options: Options) {
  const latest = useRef(options)
  latest.current = options
  const pending = useRef<PendingClose | null>(null)
  const mounted = useRef(true)
  const [confirmation, setConfirmation] = useState<PendingClose | null>(null)
  const [inProgress, setInProgress] = useState(false)
  const [error, setError] = useState('')
  const [browserRestoreGeneration, setBrowserRestoreGeneration] = useState(0)

  const release = useCallback((current: PendingClose) => {
    current.cancelled = true
    current.resolve?.(null)
    current.unregister?.()
    if (pending.current !== current) return
    pending.current = null
    if (mounted.current) {
      setConfirmation(null)
      setInProgress(false)
      if (current.browserHidden) setBrowserRestoreGeneration(value => value + 1)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (pending.current) release(pending.current)
    }
  }, [release])

  const handler = useRef<PrepareClose>(async () => false)
  handler.current = async (reason, request) => {
    if (pending.current) return false
    const current: PendingClose = { reason, request, cancelled: false, checking: false }
    pending.current = current
    setError('')
    let approved = false
    current.unregister = request?.onCancelled(() => release(current))
    const safe = async () => {
      if (current.cancelled || !await latest.current.prepareDocumentClose.current()) return false
      if (current.cancelled) return false
      if (reason === 'profile-switch') {
        await latest.current.flushWorkspace()
        if (current.cancelled || Object.values(latest.current.sessions.sessions).some(session => (
          session.conversation.activeTurn !== null
          || session.summary.recoveryState === 'recovering'
          || session.operation !== null
        ))) return false
      }
      return !current.cancelled
    }
    try {
      if (!await safe() || latest.current.sessions.pendingArchiveId) return false
      if (drafts(latest.current.sessions).length === 0) {
        if (reason === 'profile-switch' && request) {
          setInProgress(true)
          current.browserHidden = true
          current.checking = true
          setConfirmation(current)
        }
        approved = true
        return true
      }
      setInProgress(true)
      current.browserHidden = true
      try {
        await window.jobos?.browser?.setBounds({ x: 0, y: 0, width: 0, height: 0, visible: false })
      } catch {
        if (!current.cancelled) setError('JobOS could not prepare the close confirmation. Your chat drafts are still here.')
        return false
      }
      if (current.cancelled || latest.current.sessions.pendingArchiveId) return false
      const revisions = await new Promise<Map<string, number> | null>(resolve => {
        current.resolve = resolve
        setConfirmation(current)
      })
      if (!revisions || current.cancelled || !await safe()) return false
      const remaining = drafts(latest.current.sessions)
      if (remaining.some(([id, session]) => revisions.get(id) !== session.draftRevision || session.operation !== null)) return false
      approved = true
      return true
    } catch {
      return false
    } finally {
      // A successful profile preflight stays read-only until main reports failure
      // or closes this window after confirming the target profile. Keep drafts
      // intact so a rejected transition never destroys text.
      if (!approved || reason !== 'profile-switch' || !request) release(current)
    }
  }
  useCloseSafety(useCallback((reason, request) => handler.current(reason, request), []))

  const cancel = useCallback(() => {
    const current = pending.current
    if (current && !current.checking) release(current)
  }, [release])
  const confirm = useCallback(() => {
    const current = pending.current
    if (!current || current.cancelled || current.checking) return
    const currentDrafts = drafts(latest.current.sessions)
    if (currentDrafts.some(([, session]) => session.operation !== null)) return
    current.checking = true
    current.request?.setPromptPending(false)
    setConfirmation({ ...current })
    current.resolve?.(new Map(currentDrafts.map(([id, session]) => [id, session.draftRevision])))
  }, [])
  const currentDrafts = drafts(options.sessions)
  return {
    confirmation,
    count: currentDrafts.length,
    busy: currentDrafts.some(([, session]) => session.operation !== null),
    inProgress,
    error,
    browserRestoreGeneration,
    cancel,
    confirm
  }
}
