import { buildPatchedDocx, editorExtensions, parseDocxForEditing, serializeDocumentContext, type EditingDocument, type PmNode } from '@jobos/docx-editor-core'
import { EditorContent, useEditor } from '@tiptap/react'
import { ArrowLeft, Bold, FileDown, History, Italic, RotateCcw, Save, Underline } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import type { DocxBinding, DocxOpenResult, DocxRecoveryEntry } from '../../shared/docxDocuments'
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

function toBytes(buffer: ArrayBuffer): Uint8Array { return new Uint8Array(buffer) }

export function DocxDocumentEditorShell({ opened, jobLabel, onExit, onPrepareClose }: Props) {
  const [loaded, setLoaded] = useState<LoadedSource | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    setLoaded(null); setError(null)
    const bytes = toBytes(opened.bytes)
    parseDocxForEditing(bytes).then(parsed => {
      if (active) setLoaded({ binding: opened.binding, bytes, parsed, sourceVersion: 0 })
    }).catch(value => { if (active) setError(value instanceof Error ? value.message : 'Could not open DOCX') })
    return () => { active = false }
  }, [opened])

  if (error) return <main className="document-editor-shell"><div className="docx-editor-fatal" role="alert"><h1>Could not open this DOCX</h1><p>{error}</p><button onClick={onExit}>Back to Review</button></div></main>
  if (!loaded) return <main className="document-editor-shell"><div className="docx-editor-loading" role="status">Opening the original DOCX…</div></main>
  return <DocxSession key={`${loaded.binding.bindingId}:${loaded.sourceVersion}`} initial={loaded} jobLabel={jobLabel} onExit={onExit} onPrepareClose={onPrepareClose} onSourceChange={setLoaded} />
}

function DocxSession({ initial, jobLabel, onExit, onPrepareClose, onSourceChange }: {
  initial: LoadedSource
  jobLabel: string
  onExit: () => void
  onPrepareClose: (handler: () => Promise<boolean>) => void
  onSourceChange: (source: LoadedSource) => void
}) {
  const bridge = window.jobos?.docxDocuments
  const [binding, setBinding] = useState(initial.binding)
  const [exitBlocked, setExitBlocked] = useState(false)
  const [recoveries, setRecoveries] = useState<DocxRecoveryEntry[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [zoom, setZoom] = useState(100)
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

  const loadSource = useCallback(async (
    next: DocxOpenResult,
    message: string,
    expectedGeneration?: number
  ) => {
    const bytes = toBytes(next.bytes)
    const parsed = await parseDocxForEditing(bytes)
    if (expectedGeneration !== undefined && (
      autosave.currentGeneration() !== expectedGeneration || autosave.isDirtyNow()
    )) {
      autosave.markExternalConflict()
      return false
    }
    onSourceChange({ binding: next.binding, bytes, parsed, sourceVersion: initial.sourceVersion + 1 })
    setNotice(message)
    return true
  }, [
    autosave.currentGeneration,
    autosave.isDirtyNow,
    autosave.markExternalConflict,
    initial.sourceVersion,
    onSourceChange
  ])

  useEffect(() => {
    if (!bridge) return
    return bridge.subscribe(event => {
      if (event.bindingId !== binding.bindingId || event.kind === 'missing') {
        if (event.bindingId === binding.bindingId && event.kind === 'missing') autosave.markExternalConflict()
        return
      }
      if (event.sha256 === binding.sha256) return
      const generationAtReload = autosave.currentGeneration()
      if (autosave.isDirtyNow()) autosave.markExternalConflict()
      else void bridge.reload(binding.bindingId)
        .then(next => loadSource(next, 'Reloaded changes made outside JobOS', generationAtReload))
        .catch(() => autosave.markExternalConflict())
    })
  }, [
    autosave.currentGeneration,
    autosave.isDirtyNow,
    autosave.markExternalConflict,
    binding.bindingId,
    binding.sha256,
    bridge,
    loadSource
  ])

  useEffect(() => {
    if (!bridge) return
    void bridge.listRecoveries(binding.bindingId).then(setRecoveries).catch(() => undefined)
  }, [binding.bindingId, binding.revision, bridge])

  const reloadExternal = async () => {
    if (!bridge) return
    await loadSource(await bridge.reload(binding.bindingId), 'Reloaded external version')
  }

  const saveMineAs = async () => {
    if (!bridge || !editor) return
    const bytes = await buildPatchedDocx(initial.parsed, editor.getJSON() as PmNode)
    const next = await bridge.saveAs(binding.bindingId, bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer)
    if (next) await loadSource(next, 'Saved and rebound to the new DOCX')
  }

  const checkpoint = async () => {
    if (!bridge || !await autosave.flush()) return
    const saved = await bridge.createRecovery(binding.bindingId, 'manual')
    setRecoveries(await bridge.listRecoveries(binding.bindingId))
    setNotice(`Checkpoint created at ${new Date(saved.createdAt).toLocaleTimeString()}`)
  }

  const restore = async (entry: DocxRecoveryEntry) => {
    if (!bridge || autosave.isDirty) { setNotice('Save or resolve the current changes before restoring a checkpoint.'); return }
    await loadSource(await bridge.restoreRecovery(binding.bindingId, entry.recoveryId), 'Checkpoint restored')
  }

  const prepareClose = useCallback(async () => {
    const safe = autosave.state !== 'conflict'
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

  return (
    <main className="document-editor-shell docx-document-editor">
      <style>{styleText}</style>
      <header className="document-editor-titlebar">
        <button className="document-back" onClick={() => { void close() }} type="button"><ArrowLeft aria-hidden="true" size={16} />Back to Review</button>
        <div className="document-editor-title"><span>{jobLabel}</span><strong>{binding.documentLabel}</strong><small>{binding.filename}</small></div>
        <span className={`document-save-state ${autosave.state}`} role="status">{autosave.message}</span>
        <button onClick={() => { void autosave.flush() }} type="button"><Save aria-hidden="true" size={15} />Save</button>
        <button onClick={() => { void checkpoint() }} type="button"><History aria-hidden="true" size={15} />Checkpoint</button>
        <button onClick={() => { void saveMineAs() }} type="button"><FileDown aria-hidden="true" size={15} />Save a Copy…</button>
      </header>
      <div className="docx-editor-toolbar" role="toolbar" aria-label="DOCX formatting">
        <button aria-label="Bold" aria-pressed={editor.isActive('bold')} onClick={() => editor.chain().focus().toggleBold().run()} type="button"><Bold size={15} /></button>
        <button aria-label="Italic" aria-pressed={editor.isActive('italic')} onClick={() => editor.chain().focus().toggleItalic().run()} type="button"><Italic size={15} /></button>
        <button aria-label="Underline" aria-pressed={editor.isActive('underline')} onClick={() => editor.chain().focus().toggleUnderline().run()} type="button"><Underline size={15} /></button>
        <button aria-label="Undo" disabled={!editor.can().undo()} onClick={() => editor.chain().focus().undo().run()} type="button"><RotateCcw size={15} /></button>
        <span className="docx-capability-note">{binding.capabilities.protectedBlockCount ? `${binding.capabilities.protectedBlockCount} complex item(s) protected` : 'Full editable text path'} · {context.blocks.length} blocks</span>
        <label>Zoom <input max="150" min="60" onChange={event => setZoom(Number(event.target.value))} type="range" value={zoom} /></label>
      </div>
      {autosave.state === 'conflict' ? (
        <div className="docx-conflict-banner" role="alert"><strong>This file changed outside JobOS.</strong><span>Your unsaved version is still here.</span><button onClick={() => { void reloadExternal() }} type="button">Reload External Version</button><button onClick={() => { void saveMineAs() }} type="button">Save Mine As…</button></div>
      ) : null}
      {notice ? <div className="docx-editor-notice" role="status">{notice}<button aria-label="Dismiss notice" onClick={() => setNotice(null)}>×</button></div> : null}
      <div className="document-editor-main docx-editor-main">
        <section className="jobos-docx-canvas" style={{ '--docx-zoom': zoom / 100 } as React.CSSProperties}>
          <article className="jobos-docx-page" style={pageStyle}><EditorContent editor={editor} /></article>
        </section>
        <aside className="docx-recovery-panel"><h2>Recovery</h2><p>Baseline, autosave, and manual copies are kept locally.</p>{recoveries.slice(0, 8).map(entry => <button key={entry.recoveryId} onClick={() => { void restore(entry) }} type="button"><strong>{entry.reason}</strong><span>{new Date(entry.createdAt).toLocaleString()}</span></button>)}</aside>
      </div>
      <footer className="document-status-bar"><span>{binding.canonicalPath}</span><span>Page 1 of {pageCount}</span><span>{Math.round(binding.byteLength / 1024)} KB · {zoom}%</span></footer>
      {exitBlocked ? <div className="document-exit-dialog" role="alertdialog" aria-modal="true" aria-labelledby="docx-exit-title"><div><Save size={22} /><h2 id="docx-exit-title">This DOCX is not safely saved</h2><p>Stay to resolve the save, save your version as a copy, or reload the canonical file and discard these unsaved edits.</p><div><button onClick={() => setExitBlocked(false)}>Stay</button><button onClick={() => { void saveMineAs() }}>Save Mine As…</button><button className="danger" onClick={() => { void reloadExternal() }}>Reload and discard mine</button></div></div></div> : null}
    </main>
  )
}
