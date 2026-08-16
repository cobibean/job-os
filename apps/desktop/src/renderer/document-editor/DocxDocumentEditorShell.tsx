import { buildPatchedDocx, editorExtensions, parseDocxForEditing, serializeDocumentContext, type EditingDocument, type PmNode } from '@jobos/docx-editor-core'
import { EditorContent, useEditor } from '@tiptap/react'
import { AlignCenter, AlignJustify, AlignLeft, AlignRight, ArrowLeft, Bold, FileDown, History, Italic, RotateCcw, Save, Underline } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { DocxBinding, DocxExternalChangeEvent, DocxOpenResult, DocxRecoveryEntry } from '../../shared/docxDocuments'
import { displayDocxFilename } from './docxDisplay'
import { useDocxAutosave } from './useDocxAutosave'
import { useDocxPageStyle, useDocxPagination } from './useDocxPagination'

interface Props {
  opened: DocxOpenResult
  jobLabel: string
  onExit: () => void
  onPrepareClose: (handler: () => Promise<boolean>) => void
}

interface LoadedSource {
  binding: DocxBinding
  bytes: Uint8Array
  parsed: EditingDocument
  sourceVersion: number
}

interface SourceMutation {
  epoch: number
  event: DocxExternalChangeEvent
}

type SourceLoadResult = 'loaded' | 'stale' | 'generation_changed'

function toBytes(buffer: ArrayBuffer): Uint8Array { return new Uint8Array(buffer) }

export function DocxDocumentEditorShell({ opened, jobLabel, onExit, onPrepareClose }: Props) {
  const bridge = useRef(window.jobos?.docxDocuments).current
  const sourceEpoch = useRef(0)
  const [loaded, setLoaded] = useState<LoadedSource | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sourceMutation, setSourceMutation] = useState<SourceMutation | null>(null)

  useEffect(() => {
    if (!bridge) return
    return bridge.subscribe(event => {
      if (event.bindingId !== opened.binding.bindingId) return
      const epoch = sourceEpoch.current + 1
      sourceEpoch.current = epoch
      setSourceMutation({ epoch, event })
    })
  }, [bridge, opened.binding.bindingId])

  useEffect(() => {
    let active = true
    setLoaded(null); setError(null)
    const loadLatest = async () => {
      while (active) {
        const epoch = sourceEpoch.current
        const current = bridge ? await bridge.reload(opened.binding.bindingId) : opened
        const bytes = toBytes(current.bytes)
        const parsed = await parseDocxForEditing(bytes)
        if (!active) return
        if (epoch !== sourceEpoch.current) continue
        setLoaded({ binding: current.binding, bytes, parsed, sourceVersion: 0 })
        return
      }
    }
    void loadLatest().catch(value => {
      if (active) setError(value instanceof Error ? value.message : 'Could not open DOCX')
    })
    return () => { active = false }
  }, [bridge, opened])

  if (error) return <main className="document-editor-shell"><div className="docx-editor-fatal" role="alert"><h1>Could not open this DOCX</h1><p>{error}</p><button onClick={onExit}>Back to Review</button></div></main>
  if (!loaded) return <main className="document-editor-shell"><div className="docx-editor-loading" role="status">Opening the original DOCX…</div></main>
  return (
    <DocxSession
      externalMutation={sourceMutation}
      initial={loaded}
      jobLabel={jobLabel}
      key={`${loaded.binding.bindingId}:${loaded.sourceVersion}`}
      onExit={onExit}
      onPrepareClose={onPrepareClose}
      onSourceChange={setLoaded}
      sourceEpoch={sourceEpoch}
    />
  )
}

function DocxSession({ externalMutation, initial, jobLabel, onExit, onPrepareClose, onSourceChange, sourceEpoch }: {
  externalMutation: SourceMutation | null
  initial: LoadedSource
  jobLabel: string
  onExit: () => void
  onPrepareClose: (handler: () => Promise<boolean>) => void
  onSourceChange: (source: LoadedSource) => void
  sourceEpoch: { current: number }
}) {
  const bridge = window.jobos?.docxDocuments
  const [binding, setBinding] = useState(initial.binding)
  const [exitBlocked, setExitBlocked] = useState(false)
  const [recoveries, setRecoveries] = useState<DocxRecoveryEntry[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [zoom, setZoom] = useState(100)
  const [sourceActionPending, setSourceActionPending] = useState(false)
  const sourceActionPendingRef = useRef(false)
  const mounted = useRef(true)
  const autosave = useDocxAutosave({ binding, parsed: initial.parsed, onBindingChange: setBinding })
  const styleText = useMemo(() => initial.parsed.styleCss, [initial.parsed])
  const context = useMemo(() => serializeDocumentContext(initial.parsed.pmDoc), [initial.parsed.pmDoc])

  const editor = useEditor({
    content: initial.parsed.pmDoc,
    extensions: editorExtensions,
    immediatelyRender: false,
    editorProps: { attributes: { 'aria-label': 'DOCX document content', class: 'jobos-docx-prosemirror', spellcheck: 'true' } },
    onUpdate: ({ editor: current }) => autosave.queueSave(current.getJSON() as PmNode)
  })
  const pageStyle = useDocxPageStyle(initial.parsed.parsed)
  const pageCount = useDocxPagination(editor, initial.parsed.parsed)

  useEffect(() => () => { mounted.current = false }, [])

  const loadSource = useCallback(async (
    next: DocxOpenResult,
    message: string,
    operationEpoch: number,
    expectedGeneration?: number,
    allowExistingDirtyState = false
  ): Promise<SourceLoadResult> => {
    const bytes = toBytes(next.bytes)
    const parsed = await parseDocxForEditing(bytes)
    if (!mounted.current || sourceEpoch.current !== operationEpoch) return 'stale'
    if (expectedGeneration !== undefined && (
      autosave.currentGeneration() !== expectedGeneration
      || (!allowExistingDirtyState && autosave.isDirtyNow())
    )) {
      autosave.markExternalConflict()
      return 'generation_changed'
    }
    onSourceChange({ binding: next.binding, bytes, parsed, sourceVersion: initial.sourceVersion + 1 })
    setNotice(message)
    return 'loaded'
  }, [
    autosave.currentGeneration,
    autosave.isDirtyNow,
    autosave.markExternalConflict,
    initial.sourceVersion,
    onSourceChange,
    sourceEpoch
  ])

  useEffect(() => {
    if (!bridge || !externalMutation || externalMutation.event.bindingId !== binding.bindingId) return
    const { epoch, event } = externalMutation
    if (event.kind === 'missing') {
      autosave.markExternalConflict()
      return
    }
    if (event.sha256 === binding.sha256) return
    const generationAtReload = autosave.currentGeneration()
    if (autosave.isDirtyNow()) autosave.markExternalConflict()
    else void bridge.reload(binding.bindingId)
      .then(next => loadSource(next, 'Reloaded changes made outside JobOS', epoch, generationAtReload))
      .catch(() => {
        if (sourceEpoch.current === epoch) autosave.markExternalConflict()
      })
  }, [
    autosave.currentGeneration,
    autosave.isDirtyNow,
    autosave.markExternalConflict,
    binding.bindingId,
    binding.sha256,
    bridge,
    externalMutation,
    loadSource,
    sourceEpoch
  ])

  useEffect(() => {
    if (!bridge) return
    void bridge.listRecoveries(binding.bindingId).then(setRecoveries).catch(() => undefined)
  }, [binding.bindingId, binding.revision, bridge])

  const reloadExternal = async () => {
    if (!bridge || !editor || sourceActionPendingRef.current) return
    const operationEpoch = sourceEpoch.current + 1
    sourceEpoch.current = operationEpoch
    const generationAtReload = autosave.currentGeneration()
    sourceActionPendingRef.current = true
    setSourceActionPending(true)
    editor.setEditable(false)
    try {
      await loadSource(
        await bridge.reload(binding.bindingId),
        'Reloaded external version',
        operationEpoch,
        generationAtReload,
        true
      )
    } catch (value) {
      setNotice(value instanceof Error ? value.message : 'Could not reload the external DOCX')
    } finally {
      sourceActionPendingRef.current = false
      setSourceActionPending(false)
      if (!editor.isDestroyed) editor.setEditable(true)
    }
  }

  const saveMineAs = async () => {
    if (!bridge || !editor || sourceActionPendingRef.current) return
    const operationEpoch = sourceEpoch.current + 1
    sourceEpoch.current = operationEpoch
    const generationAtSnapshot = autosave.currentGeneration()
    const snapshot = editor.getJSON() as PmNode
    sourceActionPendingRef.current = true
    setSourceActionPending(true)
    editor.setEditable(false)
    try {
      const bytes = await buildPatchedDocx(initial.parsed, snapshot)
      const next = await bridge.saveAs(
        binding.bindingId,
        bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
      )
      if (!next) return
      const loaded = await loadSource(
        next,
        'Saved and rebound to the new DOCX',
        operationEpoch,
        generationAtSnapshot,
        true
      )
      if (loaded === 'generation_changed') {
        autosave.rebind(next.binding)
        autosave.resume()
        autosave.queueSave(editor.getJSON() as PmNode)
        const preserved = await autosave.flush()
        setNotice(preserved
          ? 'Saved the copy and preserved newer edits made during the operation'
          : 'The copy was created, but newer edits still need attention')
      } else if (loaded === 'stale') {
        setNotice('A newer canonical change arrived while the copy was being saved')
      }
    } catch (value) {
      setNotice(value instanceof Error ? value.message : 'Could not save a DOCX copy')
    } finally {
      sourceActionPendingRef.current = false
      setSourceActionPending(false)
      if (!editor.isDestroyed) editor.setEditable(true)
    }
  }

  const checkpoint = async () => {
    if (!bridge || sourceActionPendingRef.current) return
    sourceActionPendingRef.current = true
    setSourceActionPending(true)
    try {
      if (!await autosave.flush()) return
      const saved = await bridge.createRecovery(binding.bindingId, 'manual')
      setRecoveries(await bridge.listRecoveries(binding.bindingId))
      setNotice(`Checkpoint created at ${new Date(saved.createdAt).toLocaleTimeString()}`)
    } finally {
      sourceActionPendingRef.current = false
      setSourceActionPending(false)
    }
  }

  const restore = async (entry: DocxRecoveryEntry) => {
    if (!bridge || !editor || sourceActionPendingRef.current || autosave.isDirtyNow()) {
      setNotice('Save or resolve the current changes before restoring a checkpoint.')
      return
    }
    const operationEpoch = sourceEpoch.current + 1
    sourceEpoch.current = operationEpoch
    const generationAtRestore = autosave.currentGeneration()
    sourceActionPendingRef.current = true
    setSourceActionPending(true)
    editor.setEditable(false)
    try {
      await loadSource(
        await bridge.restoreRecovery(binding.bindingId, entry.recoveryId),
        'Checkpoint restored',
        operationEpoch,
        generationAtRestore
      )
    } catch (value) {
      setNotice(value instanceof Error ? value.message : 'Could not restore this checkpoint')
    } finally {
      sourceActionPendingRef.current = false
      setSourceActionPending(false)
      if (!editor.isDestroyed) editor.setEditable(true)
    }
  }

  const prepareClose = useCallback(async () => {
    const safe = !sourceActionPendingRef.current
      && autosave.state !== 'conflict'
      && autosave.state !== 'error'
      && await autosave.flush()
    if (!safe) setExitBlocked(true)
    return safe
  }, [autosave.flush, autosave.state])

  useEffect(() => {
    onPrepareClose(prepareClose)
    return () => onPrepareClose(async () => true)
  }, [onPrepareClose, prepareClose])

  const close = async () => {
    if (await prepareClose()) onExit()
  }

  if (!editor) return <div className="docx-editor-loading" role="status">Preparing document canvas…</div>

  const setParagraphAlignment = (align: 'left' | 'center' | 'right' | 'justify') => {
    editor.chain()
      .focus(undefined, { scrollIntoView: false })
      .updateAttributes('docParagraph', { align })
      .updateAttributes('docHeading', { align })
      .updateAttributes('docListItem', { align })
      .run()
  }

  return (
    <main className="document-editor-shell docx-document-editor">
      <style>{styleText}</style>
      <header className="document-editor-titlebar">
        <button className="document-back" onClick={() => { void close() }} type="button"><ArrowLeft aria-hidden="true" size={16} />Back to Review</button>
        <div className="document-editor-title"><span>{jobLabel}</span><strong>{binding.documentLabel}</strong><small>{displayDocxFilename(binding)}</small></div>
        <span className={`document-save-state ${autosave.state}`} role="status">{autosave.message}</span>
        <button disabled={sourceActionPending} onClick={() => { void autosave.flush() }} type="button"><Save aria-hidden="true" size={15} />Save</button>
        <button disabled={sourceActionPending} onClick={() => { void checkpoint() }} type="button"><History aria-hidden="true" size={15} />Checkpoint</button>
        <button disabled={sourceActionPending} onClick={() => { void saveMineAs() }} type="button"><FileDown aria-hidden="true" size={15} />Save a Copy…</button>
      </header>
      <div className="docx-editor-toolbar" role="toolbar" aria-label="DOCX formatting">
        <button aria-label="Bold" aria-pressed={editor.isActive('bold')} disabled={sourceActionPending} onClick={() => editor.chain().focus().toggleBold().run()} title="Bold" type="button"><Bold aria-hidden="true" size={15} /></button>
        <button aria-label="Italic" aria-pressed={editor.isActive('italic')} disabled={sourceActionPending} onClick={() => editor.chain().focus().toggleItalic().run()} title="Italic" type="button"><Italic aria-hidden="true" size={15} /></button>
        <button aria-label="Underline" aria-pressed={editor.isActive('underline')} disabled={sourceActionPending} onClick={() => editor.chain().focus().toggleUnderline().run()} title="Underline" type="button"><Underline aria-hidden="true" size={15} /></button>
        <span aria-hidden="true" className="docx-toolbar-divider" />
        <button aria-label="Align left" aria-pressed={editor.isActive({ align: 'left' })} disabled={sourceActionPending} onClick={() => setParagraphAlignment('left')} title="Align left" type="button"><AlignLeft aria-hidden="true" size={15} /></button>
        <button aria-label="Align center" aria-pressed={editor.isActive({ align: 'center' })} disabled={sourceActionPending} onClick={() => setParagraphAlignment('center')} title="Align center" type="button"><AlignCenter aria-hidden="true" size={15} /></button>
        <button aria-label="Align right" aria-pressed={editor.isActive({ align: 'right' })} disabled={sourceActionPending} onClick={() => setParagraphAlignment('right')} title="Align right" type="button"><AlignRight aria-hidden="true" size={15} /></button>
        <button aria-label="Justify" aria-pressed={editor.isActive({ align: 'justify' })} disabled={sourceActionPending} onClick={() => setParagraphAlignment('justify')} title="Justify" type="button"><AlignJustify aria-hidden="true" size={15} /></button>
        <span aria-hidden="true" className="docx-toolbar-divider" />
        <button aria-label="Undo" disabled={sourceActionPending || !editor.can().undo()} onClick={() => editor.chain().focus().undo().run()} title="Undo" type="button"><RotateCcw aria-hidden="true" size={15} /></button>
        <span className="docx-capability-note">{binding.capabilities.protectedBlockCount ? `${binding.capabilities.protectedBlockCount} complex item(s) protected` : 'Full editable text path'} · {context.blocks.length} blocks</span>
        <label>Zoom <input max="150" min="60" onChange={event => setZoom(Number(event.target.value))} type="range" value={zoom} /></label>
      </div>
      <div className="docx-editor-banners">
        {autosave.state === 'conflict' ? (
          <div className="docx-conflict-banner" role="alert"><strong>This file changed outside JobOS.</strong><span>Your unsaved version is still here.</span><button disabled={sourceActionPending} onClick={() => { void reloadExternal() }} type="button">Reload External Version</button><button disabled={sourceActionPending} onClick={() => { void saveMineAs() }} type="button">Save Mine As…</button></div>
        ) : null}
        {notice ? <div className="docx-editor-notice" role="status">{notice}<button aria-label="Dismiss notice" onClick={() => setNotice(null)}>×</button></div> : null}
      </div>
      <div className="document-editor-main docx-editor-main">
        <section className="jobos-docx-canvas" style={{ '--docx-zoom': zoom / 100 } as React.CSSProperties}>
          <article className="jobos-docx-page" style={pageStyle}><EditorContent editor={editor} /></article>
        </section>
        <aside className="docx-recovery-panel"><h2>Recovery</h2><p>Baseline, autosave, and manual copies are kept locally.</p>{recoveries.slice(0, 8).map(entry => <button disabled={sourceActionPending} key={entry.recoveryId} onClick={() => { void restore(entry) }} type="button"><strong>{entry.reason}</strong><span>{new Date(entry.createdAt).toLocaleString()}</span></button>)}</aside>
      </div>
      <footer className="document-status-bar"><span>{displayDocxFilename(binding)}</span><span>Page 1 of {pageCount}</span><span>{Math.round(binding.byteLength / 1024)} KB · {zoom}%</span></footer>
      {exitBlocked ? <div className="document-exit-dialog" role="alertdialog" aria-modal="true" aria-labelledby="docx-exit-title"><div><Save size={22} /><h2 id="docx-exit-title">This DOCX is not safely saved</h2><p>Stay to resolve the save, save your version as a copy, or reload the canonical file and discard these unsaved edits.</p><div><button onClick={() => setExitBlocked(false)}>Stay</button><button onClick={() => { void saveMineAs() }}>Save Mine As…</button><button className="danger" onClick={() => { void reloadExternal() }}>Reload and discard mine</button></div></div></div> : null}
    </main>
  )
}
