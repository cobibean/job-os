import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  DocumentComment,
  DocumentSettings,
  EditableDocument,
  TiptapDocumentJson
} from '../../shared/editableDocuments'

export type DocumentSaveState = 'saved' | 'unsaved' | 'saving' | 'conflict' | 'error'

interface SavePayload {
  content: TiptapDocumentJson
  settings: DocumentSettings
  comments: DocumentComment[]
  sequence: number
}

interface UseDocumentAutosaveOptions {
  document: EditableDocument
  onDocumentChange: (document: EditableDocument) => void
}

export function useDocumentAutosave({ document, onDocumentChange }: UseDocumentAutosaveOptions) {
  const bridge = useRef(window.jobos?.editableDocuments).current
  const [state, setState] = useState<DocumentSaveState>('saved')
  const [message, setMessage] = useState('Saved')
  const revision = useRef(document.revision)
  const sequence = useRef(0)
  const pending = useRef<SavePayload | null>(null)
  const inFlight = useRef<Promise<boolean> | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryAttempt = useRef(0)
  const onChange = useRef(onDocumentChange)
  onChange.current = onDocumentChange

  useEffect(() => {
    revision.current = document.revision
  }, [document.documentId, document.revision])

  const saveNow = useCallback(async (): Promise<boolean> => {
    if (!bridge) {
      setState('error')
      setMessage('Editor bridge unavailable')
      return false
    }
    if (inFlight.current) return inFlight.current
    const payload = pending.current
    if (!payload) return true
    pending.current = null
    setState('saving')
    setMessage('Saving…')
    const baseRevision = revision.current
    let conflict = false
    const operation = bridge.save(document.documentId, {
      baseRevision,
      content: payload.content,
      settings: payload.settings,
      comments: payload.comments,
      idempotencyKey: `autosave-${document.documentId}-${baseRevision}-${payload.sequence}`
    }).then(saved => {
      revision.current = saved.revision
      retryAttempt.current = 0
      onChange.current(saved)
      if (pending.current) {
        setState('unsaved')
        setMessage('Unsaved changes')
      } else {
        setState('saved')
        setMessage('Saved')
      }
      return true
    }).catch(error => {
      pending.current ??= payload
      const text = error instanceof Error ? error.message : 'Document save failed'
      if (/conflict|revision|newer saved version/i.test(text)) {
        conflict = true
        setState('conflict')
        setMessage('A newer saved version is available')
      } else {
        setState('error')
        setMessage(text)
        const delays = [1_000, 2_000, 4_000, 8_000, 15_000]
        const delay = delays[Math.min(retryAttempt.current, delays.length - 1)]!
        retryAttempt.current += 1
        if (retryTimer.current) clearTimeout(retryTimer.current)
        retryTimer.current = setTimeout(() => {
          retryTimer.current = null
          void saveNow()
        }, delay)
      }
      return false
    }).finally(() => {
      inFlight.current = null
      if (pending.current && !conflict && !retryTimer.current) {
        queueMicrotask(() => { void saveNow() })
      }
    })
    inFlight.current = operation
    return operation
  }, [bridge, document.documentId])

  const queueSave = useCallback((next: Omit<SavePayload, 'sequence'>) => {
    sequence.current += 1
    pending.current = { ...next, sequence: sequence.current }
    if (timer.current) clearTimeout(timer.current)
    if (retryTimer.current) {
      clearTimeout(retryTimer.current)
      retryTimer.current = null
    }
    setState('unsaved')
    setMessage('Unsaved changes')
    timer.current = setTimeout(() => {
      timer.current = null
      void saveNow()
    }, 750)
  }, [saveNow])

  const flush = useCallback(async () => {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
    const saved = await saveNow()
    if (!saved) return false
    if (pending.current) return saveNow()
    return true
  }, [saveNow])

  const retry = useCallback(() => {
    if (retryTimer.current) clearTimeout(retryTimer.current)
    retryTimer.current = null
    void saveNow()
  }, [saveNow])

  const discardPending = useCallback(() => {
    pending.current = null
    if (timer.current) clearTimeout(timer.current)
    if (retryTimer.current) clearTimeout(retryTimer.current)
    timer.current = null
    retryTimer.current = null
    setState('saved')
    setMessage('Unsaved changes discarded')
  }, [])

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
    if (retryTimer.current) clearTimeout(retryTimer.current)
  }, [])

  return { discardPending, flush, message, queueSave, retry, state }
}
