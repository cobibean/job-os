import { ArrowLeft, Download, Eye, Save, Send } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type {
  DocumentComment,
  DocumentSettings,
  EditableDocument,
  EditableDocumentSnapshot,
  TiptapDocumentJson,
  TiptapNodeJson
} from '../../shared/editableDocuments'
import { plainText, resolveDocumentSuggestion, type DocumentSuggestion, unresolvedSuggestionCount } from '../../shared/editableDocumentSchema'
import { DocumentEditor } from './DocumentEditor'
import { DocumentInspector } from './DocumentInspector'
import { DocumentStatusBar } from './DocumentStatusBar'
import { OriginalDocxPreview } from './OriginalDocxPreview'
import { useDocumentAutosave } from './useDocumentAutosave'

interface DocumentEditorShellProps {
  document: EditableDocument
  externalGeneration?: number
  jobLabel: string
  onDocumentChange: (document: EditableDocument) => void
  onExit: () => void
}

type ViewTab = 'edit' | 'preview' | 'original'
type InspectorTab = 'format' | 'page' | 'history' | 'comments' | 'import'

export function DocumentEditorShell(props: DocumentEditorShellProps) {
  const [content, setContent] = useState<TiptapDocumentJson>(props.document.content)
  const [settings, setSettings] = useState<DocumentSettings>(props.document.settings)
  const [comments, setComments] = useState<DocumentComment[]>(props.document.comments)
  const [view, setView] = useState<ViewTab>('edit')
  const [inspector, setInspector] = useState<InspectorTab>('format')
  const [zoom, setZoom] = useState(100)
  const [selectedBlockId, setSelectedBlockId] = useState<`node_${string}` | null>(null)
  const [snapshots, setSnapshots] = useState<EditableDocumentSnapshot[]>([])
  const [historyBusy, setHistoryBusy] = useState(false)
  const [exitBlocked, setExitBlocked] = useState(false)
  const [publicationBusy, setPublicationBusy] = useState(false)
  const [publicationMessage, setPublicationMessage] = useState<string | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const autosave = useDocumentAutosave({
    document: props.document,
    onDocumentChange: props.onDocumentChange
  })

  useEffect(() => {
    setContent(props.document.content)
    setSettings(props.document.settings)
    setComments(props.document.comments)
  }, [props.document.documentId, props.document.revision])

  const queue = (nextContent: TiptapDocumentJson, nextSettings = settings, nextComments = comments) => {
    autosave.queueSave({ content: nextContent, settings: nextSettings, comments: nextComments })
  }
  const updateContent = (next: TiptapDocumentJson) => {
    setContent(next)
    queue(next)
  }
  const updateSettings = (next: DocumentSettings) => {
    setSettings(next)
    queue(content, next)
  }
  const updateComments = (next: DocumentComment[]) => {
    setComments(next)
    queue(content, settings, next)
  }
  const resolveSuggestion = (suggestion: DocumentSuggestion, resolution: 'accept' | 'reject') => {
    const next = resolveDocumentSuggestion(content, suggestion.suggestionId, resolution)
    setContent(next)
    queue(next)
  }
  const selectedBlockLocked = useMemo(() => {
    let locked = false
    const visit = (node: TiptapNodeJson) => {
      if (node.attrs?.jobosId === selectedBlockId) locked = node.attrs.locked === true
      for (const child of node.content ?? []) visit(child)
    }
    visit(content)
    return locked
  }, [content, selectedBlockId])
  const toggleSelectedBlockLock = () => {
    if (!selectedBlockId) return
    const update = (node: TiptapNodeJson): TiptapNodeJson => ({
      ...node,
      ...(node.attrs?.jobosId === selectedBlockId
        ? { attrs: { ...node.attrs, locked: !selectedBlockLocked } }
        : {}),
      ...(node.content ? { content: node.content.map(update) } : {})
    })
    const next = update(content) as TiptapDocumentJson
    setContent(next)
    queue(next)
  }
  const text = useMemo(() => plainText(content), [content])
  const words = useMemo(() => text.trim() ? text.trim().split(/\s+/u).length : 0, [text])
  const estimatedPages = Math.max(1, Math.ceil(Math.max(1, text.length) / 3_000))
  const unresolved = unresolvedSuggestionCount(content)

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  const generatePreview = async () => {
    const bridge = window.jobos?.editableDocuments
    if (!bridge || !(await autosave.flush())) return
    setPublicationBusy(true)
    setPublicationMessage('Preparing exact PDF preview…')
    try {
      const preview = await bridge.preview(props.document.documentId)
      const nextUrl = URL.createObjectURL(new Blob([preview.bytes], { type: 'application/pdf' }))
      setPreviewUrl(current => {
        if (current) URL.revokeObjectURL(current)
        return nextUrl
      })
      setView('preview')
      setPublicationMessage(`Previewing ${preview.filename}`)
    } catch (error) {
      setPublicationMessage(error instanceof Error ? error.message : 'Preview failed')
    } finally {
      setPublicationBusy(false)
    }
  }

  const exportDocument = async (format: 'docx' | 'pdf') => {
    const bridge = window.jobos?.editableDocuments
    if (!bridge || !(await autosave.flush())) return
    setPublicationBusy(true)
    setPublicationMessage(`Preparing ${format.toUpperCase()}…`)
    try {
      const result = await bridge.export(props.document.documentId, format)
      setPublicationMessage(result.message)
    } catch (error) {
      setPublicationMessage(error instanceof Error ? error.message : 'Export failed')
    } finally {
      setPublicationBusy(false)
    }
  }

  const publishRevision = async () => {
    const bridge = window.jobos?.editableDocuments
    if (!bridge || !(await autosave.flush())) return
    setPublicationBusy(true)
    setPublicationMessage('Publishing paired DOCX and PDF…')
    try {
      const published = await bridge.publish(props.document.documentId)
      props.onDocumentChange(published)
      setPublicationMessage(`Revision ${published.publishedRevision} published`)
      props.onExit()
    } catch (error) {
      setPublicationMessage(error instanceof Error ? error.message : 'Publication failed')
    } finally {
      setPublicationBusy(false)
    }
  }

  useEffect(() => {
    const bridge = window.jobos?.editableDocuments
    if (!bridge || autosave.state !== 'saved' || (props.externalGeneration ?? 0) === 0) return
    let active = true
    bridge.get(props.document.documentId).then(latest => {
      if (active && latest.revision > props.document.revision) props.onDocumentChange(latest)
    }).catch(() => undefined)
    return () => { active = false }
  }, [autosave.state, props.document.documentId, props.document.revision, props.externalGeneration, props.onDocumentChange])

  useEffect(() => {
    if (inspector !== 'history' || !window.jobos?.editableDocuments) return
    let active = true
    setHistoryBusy(true)
    window.jobos.editableDocuments.listSnapshots(props.document.documentId)
      .then(value => { if (active) setSnapshots(value) })
      .catch(() => { if (active) setSnapshots([]) })
      .finally(() => { if (active) setHistoryBusy(false) })
    return () => { active = false }
  }, [inspector, props.document.documentId, props.document.revision])

  const createSnapshot = async () => {
    const bridge = window.jobos?.editableDocuments
    if (!bridge || !(await autosave.flush())) return
    setHistoryBusy(true)
    try {
      await bridge.createSnapshot(props.document.documentId, {
        baseRevision: props.document.revision,
        reason: 'manual',
        label: `Manual checkpoint · revision ${props.document.revision}`,
        idempotencyKey: `checkpoint-${props.document.documentId}-${props.document.revision}`
      })
      setSnapshots(await bridge.listSnapshots(props.document.documentId))
    } finally {
      setHistoryBusy(false)
    }
  }

  const restoreSnapshot = async (snapshot: EditableDocumentSnapshot) => {
    const bridge = window.jobos?.editableDocuments
    if (!bridge || !(await autosave.flush())) return
    setHistoryBusy(true)
    try {
      const restored = await bridge.restoreSnapshot(
        props.document.documentId,
        snapshot.snapshotId,
        {
          baseRevision: props.document.revision,
          idempotencyKey: `restore-${snapshot.snapshotId}-${props.document.revision}`
        }
      )
      setContent(restored.content)
      setSettings(restored.settings)
      setComments(restored.comments)
      props.onDocumentChange(restored)
      setSnapshots(await bridge.listSnapshots(restored.documentId))
    } finally {
      setHistoryBusy(false)
    }
  }

  const backToReview = async () => {
    if (autosave.state === 'conflict' || autosave.state === 'error') {
      setExitBlocked(true)
      return
    }
    if (await autosave.flush()) props.onExit()
    else setExitBlocked(true)
  }

  const copyUnsavedText = async () => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      // The recovery dialog remains visible when clipboard permission is unavailable.
    }
  }

  return (
    <main className="document-editor-shell">
      <header className="document-editor-titlebar">
        <button className="document-back" onClick={() => { void backToReview() }} type="button"><ArrowLeft aria-hidden="true" size={16} />Back to Review</button>
        <div className="document-editor-title"><span>{props.jobLabel}</span><strong>{props.document.documentLabel}</strong></div>
        <span className={`document-save-state ${autosave.state}`} role="status">{autosave.message}</span>
        <button disabled={publicationBusy} onClick={() => { void generatePreview() }} type="button"><Eye aria-hidden="true" size={15} />Preview</button>
        <button disabled={publicationBusy} onClick={() => { void exportDocument('docx') }} title={unresolved ? 'Review a warning, then export the exact current state' : 'Export Word document'} type="button"><Download aria-hidden="true" size={15} />DOCX</button>
        <button disabled={publicationBusy} onClick={() => { void exportDocument('pdf') }} title={unresolved ? 'Review a warning, then export the exact current state' : 'Export PDF document'} type="button"><Download aria-hidden="true" size={15} />PDF</button>
        <button className="publish-document" disabled={publicationBusy} onClick={() => { void publishRevision() }} title={unresolved ? 'Review a warning, then publish the exact current state' : 'Publish revision'} type="button"><Send aria-hidden="true" size={15} />Publish revision</button>
      </header>
      <div className="document-editor-viewtabs" role="tablist" aria-label="Document views">
        <button aria-selected={view === 'edit'} onClick={() => setView('edit')} role="tab" type="button">Edit</button>
        <button aria-selected={view === 'preview'} onClick={() => { void generatePreview() }} role="tab" type="button">Print preview</button>
        {props.document.sourceArtifactId ? <button aria-selected={view === 'original'} onClick={() => setView('original')} role="tab" type="button">Original</button> : null}
        {publicationMessage ? <span className="document-publication-message" role="status">{publicationMessage}</span> : null}
      </div>
      <div className="document-editor-main">
        <section className={`document-editor-workarea${view === 'edit' ? '' : ' document-editor-preview-mode'}`} style={{ '--editor-zoom': zoom / 100 } as React.CSSProperties}>
          {view === 'edit' ? (
            <DocumentEditor content={content} documentRevision={props.document.revision} onChange={updateContent} onSelectedBlockChange={setSelectedBlockId} settings={settings} />
          ) : view === 'original' && props.document.sourceArtifactId ? (
            <OriginalDocxPreview
              artifactId={props.document.sourceArtifactId}
              sourceFilename={props.document.sourceFilename}
            />
          ) : previewUrl ? (
            <iframe className="document-pdf-preview" src={previewUrl} title="Generated PDF preview" />
          ) : (
            <div className="document-preview-placeholder">
              <Eye aria-hidden="true" size={28} />
              <h2>Generate a print preview</h2>
              <p>JobOS will render the saved revision locally using the same PDF pipeline used for export.</p>
              <button disabled={publicationBusy} onClick={() => { void generatePreview() }} type="button">Generate preview</button>
            </div>
          )}
        </section>
        <DocumentInspector
          activeTab={inspector}
          comments={comments}
          content={content}
          document={props.document}
          historyBusy={historyBusy}
          onCommentsChange={updateComments}
          onCreateSnapshot={() => { void createSnapshot() }}
          onResolveSuggestion={resolveSuggestion}
          onRestoreSnapshot={snapshot => { void restoreSnapshot(snapshot) }}
          onSettingsChange={updateSettings}
          onTabChange={setInspector}
          onToggleSelectedBlockLock={toggleSelectedBlockLock}
          selectedBlockId={selectedBlockId}
          selectedBlockLocked={selectedBlockLocked}
          settings={settings}
          snapshots={snapshots}
        />
      </div>
      <DocumentStatusBar characters={text.length} estimatedPages={estimatedPages} onZoomChange={setZoom} saveState={autosave.state} words={words} zoom={zoom} />
      {exitBlocked ? (
        <div className="document-exit-dialog" role="alertdialog" aria-modal="true" aria-labelledby="document-exit-title">
          <div><Save aria-hidden="true" size={22} /><h2 id="document-exit-title">Unsaved document changes</h2><p>Saving did not finish. Stay and retry, copy the unsaved text, or explicitly discard these changes.</p>
            <div><button onClick={() => { setExitBlocked(false); autosave.retry() }} type="button">Stay and retry</button><button onClick={() => { void copyUnsavedText() }} type="button">Copy unsaved text</button><button className="danger" onClick={() => { autosave.discardPending(); props.onExit() }} type="button">Discard changes</button></div>
          </div>
        </div>
      ) : null}
    </main>
  )
}
