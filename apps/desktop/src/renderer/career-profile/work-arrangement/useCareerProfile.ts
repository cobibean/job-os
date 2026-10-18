import { useCallback, useEffect, useRef, useState, type SetStateAction } from 'react'

import type {
  CareerProfileBridge,
  WorkArrangementCurrent,
  WorkArrangementHistory,
  WorkArrangementMutationResult,
  WorkArrangementValue
} from '../../../shared/contracts'

export type CareerProfileStatus = 'loading' | 'ready' | 'saving' | 'saved' | 'conflict' | 'error'

interface ConflictState {
  current: WorkArrangementCurrent
  operation: 'save' | 'restore'
  proposed: WorkArrangementValue
  targetProfileRevision?: number
}

interface PendingMutation {
  idempotencyKey: string
  signature: string
}

const careerProfileCacheKey = 'jobos:career-profile:last-known-work-arrangement'

async function readCachedCareerProfile(bridge: CareerProfileBridge): Promise<WorkArrangementCurrent | null> {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(careerProfileCacheKey) ?? 'null')
    return await bridge.validateCachedWorkArrangement(parsed)
  } catch {
    return null
  }
}

export async function hasCachedCareerProfile(bridge: CareerProfileBridge): Promise<boolean> {
  return await readCachedCareerProfile(bridge) !== null
}

function cacheCareerProfile(current: WorkArrangementCurrent): void {
  try {
    window.localStorage.setItem(careerProfileCacheKey, JSON.stringify(current))
  } catch {
    // The API remains authoritative; a blocked local cache must not block normal use.
  }
}

const emptyValue: WorkArrangementValue = {
  mode: 'flexible',
  strength: 'preference',
  note: null
}

function requestId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

export function useCareerProfile(bridge: CareerProfileBridge) {
  const [current, setCurrent] = useState<WorkArrangementCurrent | null>(null)
  const [draft, updateDraft] = useState<WorkArrangementValue>(emptyValue)
  const [history, setHistory] = useState<WorkArrangementHistory | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [status, setStatus] = useState<CareerProfileStatus>('loading')
  const [message, setMessage] = useState('')
  const [conflict, setConflict] = useState<ConflictState | null>(null)
  const pendingSave = useRef<PendingMutation | null>(null)
  const pendingRestore = useRef<PendingMutation | null>(null)
  const draftRef = useRef<WorkArrangementValue>(emptyValue)
  const draftBaseline = useRef<WorkArrangementValue>(emptyValue)
  const editingRevision = useRef<number | null>(null)

  const setDraft = useCallback((action: SetStateAction<WorkArrangementValue>) => {
    const next = typeof action === 'function' ? action(draftRef.current) : action
    draftRef.current = next
    updateDraft(next)
  }, [])

  const applyCurrent = useCallback((value: WorkArrangementCurrent, resetDraft = false) => {
    cacheCareerProfile(value)
    setCurrent(value)
    const saved = value.record?.value ?? emptyValue
    const previous = draftRef.current
    const baseline = draftBaseline.current
    const dirty = !resetDraft && (Object.keys(saved) as Array<keyof WorkArrangementValue>)
      .some(field => previous[field] !== baseline[field])
    if (!dirty) {
      draftBaseline.current = saved
      editingRevision.current = value.profileRevision
      setDraft(saved)
      return
    }
    setDraft({
      mode: previous.mode !== baseline.mode ? previous.mode : saved.mode,
      strength: previous.strength !== baseline.strength ? previous.strength : saved.strength,
      note: previous.note !== baseline.note ? previous.note : saved.note
    })
    draftBaseline.current = {
      mode: previous.mode !== baseline.mode ? baseline.mode : saved.mode,
      strength: previous.strength !== baseline.strength ? baseline.strength : saved.strength,
      note: previous.note !== baseline.note ? baseline.note : saved.note
    }
  }, [setDraft])

  const load = useCallback(async (showLoading = true) => {
    if (showLoading) setStatus('loading')
    setMessage('')
    try {
      applyCurrent(await bridge.getWorkArrangement())
      setStatus('ready')
    } catch {
      const cached = await readCachedCareerProfile(bridge)
      if (cached) {
        applyCurrent(cached)
        setStatus('ready')
        setMessage('Offline — showing your last saved Career Profile. Reconnect before making changes.')
        return
      }
      setStatus('error')
      setMessage('Career Profile is unavailable right now. Your changes have not been lost.')
    }
  }, [applyCurrent, bridge])

  useEffect(() => { void load() }, [load])

  const handleMutation = useCallback((
    result: WorkArrangementMutationResult,
    proposed: WorkArrangementValue,
    success: string,
    operation: ConflictState['operation'] = 'save',
    targetProfileRevision?: number
  ) => {
    if (result.status === 'conflict') {
      applyCurrent(result.current, true)
      setDraft(proposed)
      setHistory(null)
      setConflict({ current: result.current, operation, proposed, targetProfileRevision })
      setStatus('conflict')
      setMessage('This preference changed somewhere else. Review the latest saved value before reapplying your change.')
      return false
    }
    applyCurrent(result.current, true)
    setConflict(null)
    setHistory(null)
    setStatus('saved')
    setMessage(success)
    return true
  }, [applyCurrent, setDraft])

  const save = useCallback(async (successMessage = 'Saved.') => {
    if (!current) return false
    const proposed = { ...draft, note: draft.note?.trim() || null }
    const expectedProfileRevision = editingRevision.current ?? current.profileRevision
    const signature = JSON.stringify({ expectedProfileRevision, value: proposed })
    if (pendingSave.current?.signature !== signature) {
      pendingSave.current = { idempotencyKey: requestId('career_save'), signature }
    }
    setStatus('saving')
    setMessage('')
    try {
      const result = await bridge.saveWorkArrangement({
        expectedProfileRevision,
        idempotencyKey: pendingSave.current.idempotencyKey,
        value: proposed
      })
      pendingSave.current = null
      return handleMutation(result, proposed, successMessage)
    } catch {
      setStatus('error')
      setMessage('JobOS could not save this preference. Your edit is still here—try again when the connection returns.')
      return false
    }
  }, [bridge, current, draft, handleMutation])

  const openHistory = useCallback(async () => {
    setHistoryOpen(true)
    setHistoryError('')
    if (history) return
    try {
      setHistory(await bridge.getWorkArrangementHistory())
    } catch {
      setHistoryError('History could not load. Try again.')
    }
  }, [bridge, history])

  const restore = useCallback(async (targetProfileRevision: number, proposedOverride?: WorkArrangementValue) => {
    if (!current) return false
    const proposed = proposedOverride
      ?? history?.revisions.find(revision => revision.profileRevision === targetProfileRevision)?.value
    if (!proposed) {
      setStatus('error')
      setHistoryError('That earlier value is no longer available. Reload history and try again.')
      return false
    }
    const signature = JSON.stringify({ expectedProfileRevision: current.profileRevision, targetProfileRevision })
    if (pendingRestore.current?.signature !== signature) {
      pendingRestore.current = { idempotencyKey: requestId('career_undo'), signature }
    }
    setStatus('saving')
    setMessage('')
    try {
      const result = await bridge.restoreWorkArrangement({
        expectedProfileRevision: current.profileRevision,
        idempotencyKey: pendingRestore.current.idempotencyKey,
        targetProfileRevision
      })
      pendingRestore.current = null
      const restored = handleMutation(
        result,
        proposed,
        'Previous preference restored as a new revision.',
        'restore',
        targetProfileRevision
      )
      setHistoryOpen(false)
      return restored
    } catch {
      setStatus('error')
      setMessage('')
      setHistoryError('JobOS could not restore that version. Nothing was changed.')
      return false
    }
  }, [bridge, current, handleMutation, history])

  const keepCurrent = useCallback(() => {
    if (!conflict) return
    applyCurrent(conflict.current, true)
    setConflict(null)
    setStatus('ready')
    setMessage('Kept the latest saved preference.')
  }, [applyCurrent, conflict])

  const reapplyConflict = useCallback(async (successMessage = 'Saved.') => {
    if (!conflict) return false
    if (conflict.operation === 'restore' && conflict.targetProfileRevision !== undefined) {
      return restore(conflict.targetProfileRevision, conflict.proposed)
    }
    return save(successMessage)
  }, [conflict, restore, save])

  return {
    conflict,
    current,
    draft,
    history,
    historyError,
    historyOpen,
    keepCurrent,
    load,
    message,
    openHistory,
    reapplyConflict,
    restore,
    save,
    setDraft,
    setHistoryOpen,
    status
  }
}
