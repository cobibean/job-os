import { buildPatchedDocx, type EditingDocument, type PmNode } from '@jobos/docx-editor-core'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { DocxBinding } from '../../shared/docxDocuments'

export type DocxSaveState = 'saved' | 'unsaved' | 'saving' | 'conflict' | 'error'

interface PendingSave { generation: number; document: PmNode }

interface UseDocxAutosaveOptions {
  binding: DocxBinding
  parsed: EditingDocument
  onBindingChange: (binding: DocxBinding) => void
}

export function useDocxAutosave({ binding, parsed, onBindingChange }: UseDocxAutosaveOptions) {
  const bridge = useRef(window.jobos?.docxDocuments).current
  const [state, setState] = useState<DocxSaveState>('saved')
  const [message, setMessage] = useState('Saved')
  const generation = useRef(0)
  const persistedGeneration = useRef(0)
  const bindingRef = useRef(binding)
  const pending = useRef<PendingSave | null>(null)
  const currentDocument = useRef<PmNode>(parsed.pmDoc)
  const inFlight = useRef<Promise<boolean> | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const paused = useRef(false)

  bindingRef.current = binding

  const saveNow = useCallback(async (): Promise<boolean> => {
    if (!bridge) {
      setState('error'); setMessage('Editor bridge unavailable'); return false
    }
    if (paused.current) return false
    if (inFlight.current) return inFlight.current
    const next = pending.current
    if (!next) return persistedGeneration.current === generation.current
    pending.current = null
    setState('saving'); setMessage('Saving to DOCX…')
    const operation = buildPatchedDocx(parsed, next.document).then(bytes => bridge.save({
      bindingId: bindingRef.current.bindingId,
      expectedSha256: bindingRef.current.sha256,
      generation: next.generation,
      bytes: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
    })).then(result => {
      bindingRef.current = result.binding
      persistedGeneration.current = result.persistedGeneration
      onBindingChange(result.binding)
      if (pending.current || persistedGeneration.current !== generation.current) {
        setState('unsaved'); setMessage('Unsaved changes')
      } else {
        setState('saved'); setMessage('Saved to DOCX')
      }
      return true
    }).catch(error => {
      pending.current ??= next
      const text = error instanceof Error ? error.message : 'DOCX save failed'
      if (/changed outside|external|conflict/i.test(text)) {
        paused.current = true
        setState('conflict'); setMessage('DOCX changed outside JobOS')
      } else {
        setState('error'); setMessage(text)
      }
      return false
    }).finally(() => {
      inFlight.current = null
      if (pending.current && pending.current.generation > next.generation && !paused.current) {
        queueMicrotask(() => { void saveNow() })
      }
    })
    inFlight.current = operation
    return operation
  }, [bridge, onBindingChange, parsed])

  const queueSave = useCallback((document: PmNode) => {
    currentDocument.current = document
    generation.current += 1
    pending.current = { generation: generation.current, document }
    if (timer.current) clearTimeout(timer.current)
    setState('unsaved'); setMessage('Unsaved changes')
    timer.current = setTimeout(() => { timer.current = null; void saveNow() }, 750)
  }, [saveNow])

  const flush = useCallback(async () => {
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    if (inFlight.current && !await inFlight.current) return false
    while (pending.current && !paused.current) {
      if (!await saveNow()) return false
    }
    return !paused.current && persistedGeneration.current === generation.current
  }, [saveNow])

  const isDirtyNow = useCallback(() => (
    paused.current
    || pending.current !== null
    || inFlight.current !== null
    || persistedGeneration.current !== generation.current
  ), [])

  const currentGeneration = useCallback(() => generation.current, [])

  const markExternalConflict = useCallback(() => {
    paused.current = true
    setState('conflict'); setMessage('DOCX changed outside JobOS')
  }, [])

  const reset = useCallback((nextBinding: DocxBinding, document: PmNode) => {
    if (timer.current) clearTimeout(timer.current)
    bindingRef.current = nextBinding
    currentDocument.current = document
    generation.current = 0
    persistedGeneration.current = 0
    pending.current = null
    paused.current = false
    setState('saved'); setMessage('Saved')
  }, [])

  const resume = useCallback(() => {
    paused.current = false
    if (pending.current) { setState('unsaved'); setMessage('Unsaved changes') }
  }, [])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  return {
    currentDocument,
    currentGeneration,
    flush,
    isDirty: state !== 'saved',
    isDirtyNow,
    markExternalConflict,
    message,
    queueSave,
    reset,
    resume,
    saveNow,
    state
  }
}
