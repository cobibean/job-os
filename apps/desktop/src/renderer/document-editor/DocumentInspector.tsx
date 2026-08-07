import { useMemo, useState } from 'react'

import type {
  DocumentComment,
  DocumentSettings,
  EditableDocument,
  EditableDocumentSnapshot,
  TiptapDocumentJson
} from '../../shared/editableDocuments'
import { collectDocumentSuggestions, type DocumentSuggestion } from '../../shared/editableDocumentSchema'

interface DocumentInspectorProps {
  activeTab: 'format' | 'page' | 'history' | 'comments' | 'import'
  comments: DocumentComment[]
  content: TiptapDocumentJson
  document: EditableDocument
  historyBusy: boolean
  onCommentsChange: (comments: DocumentComment[]) => void
  onCreateSnapshot: () => void
  onResolveSuggestion: (suggestion: DocumentSuggestion, resolution: 'accept' | 'reject') => void
  onRestoreSnapshot: (snapshot: EditableDocumentSnapshot) => void
  onSettingsChange: (settings: DocumentSettings) => void
  onTabChange: (tab: DocumentInspectorProps['activeTab']) => void
  onToggleSelectedBlockLock: () => void
  selectedBlockId: `node_${string}` | null
  selectedBlockLocked: boolean
  settings: DocumentSettings
  snapshots: EditableDocumentSnapshot[]
}

const tabs: Array<{ id: DocumentInspectorProps['activeTab']; label: string }> = [
  { id: 'format', label: 'Format' },
  { id: 'page', label: 'Page setup' },
  { id: 'comments', label: 'Review' },
  { id: 'history', label: 'History' },
  { id: 'import', label: 'Import report' }
]

function dateLabel(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

export function DocumentInspector(props: DocumentInspectorProps) {
  const [commentBody, setCommentBody] = useState('')
  const suggestions = useMemo(() => collectDocumentSuggestions(props.content), [props.content])

  const updateMargin = (side: keyof DocumentSettings['marginsInches'], raw: string) => {
    const value = Math.round(Number(raw) * 20) / 20
    if (!Number.isFinite(value) || value < 0.25 || value > 2) return
    props.onSettingsChange({
      ...props.settings,
      marginsInches: { ...props.settings.marginsInches, [side]: value }
    })
  }
  const addComment = () => {
    const body = commentBody.trim()
    if (!body || !props.selectedBlockId) return
    props.onCommentsChange([...props.comments, {
      commentId: `comment_${globalThis.crypto.randomUUID()}`,
      blockId: props.selectedBlockId,
      author: 'user',
      body,
      createdAt: new Date().toISOString(),
      resolvedAt: null
    }])
    setCommentBody('')
  }
  const toggleComment = (comment: DocumentComment) => {
    props.onCommentsChange(props.comments.map(item => item.commentId === comment.commentId
      ? { ...item, resolvedAt: item.resolvedAt ? null : new Date().toISOString() }
      : item))
  }

  return (
    <aside className="document-inspector" aria-label="Document inspector">
      <div className="document-inspector-tabs" role="tablist" aria-label="Inspector tabs">
        {tabs.map(tab => (
          <button aria-selected={props.activeTab === tab.id} key={tab.id} onClick={() => props.onTabChange(tab.id)} role="tab" tabIndex={props.activeTab === tab.id ? 0 : -1} type="button">{tab.label}</button>
        ))}
      </div>
      <div className="document-inspector-body" role="tabpanel">
        {props.activeTab === 'page' ? (
          <div className="inspector-stack">
            <label>Page size<select aria-label="Page size" value={props.settings.pageSize} onChange={event => props.onSettingsChange({ ...props.settings, pageSize: event.target.value as 'letter' | 'a4' })}><option value="letter">US Letter</option><option value="a4">A4</option></select></label>
            <fieldset><legend>Margins (inches)</legend>{(['top', 'right', 'bottom', 'left'] as const).map(side => <label key={side}>{side}<input max="2" min="0.25" onChange={event => updateMargin(side, event.target.value)} step="0.05" type="number" value={props.settings.marginsInches[side]} /></label>)}</fieldset>
            <label>Header left<input maxLength={500} onChange={event => props.onSettingsChange({ ...props.settings, header: { ...props.settings.header, left: event.target.value } })} value={props.settings.header.left} /></label>
            <label>Header center<input maxLength={500} onChange={event => props.onSettingsChange({ ...props.settings, header: { ...props.settings.header, center: event.target.value } })} value={props.settings.header.center} /></label>
            <label>Header right<input maxLength={500} onChange={event => props.onSettingsChange({ ...props.settings, header: { ...props.settings.header, right: event.target.value } })} value={props.settings.header.right} /></label>
            <label>Footer center<input maxLength={500} onChange={event => props.onSettingsChange({ ...props.settings, footer: { ...props.settings.footer, center: event.target.value } })} value={props.settings.footer.center} /></label>
            <label className="inspector-check"><input checked={props.settings.header.firstPageDifferent} onChange={event => props.onSettingsChange({ ...props.settings, header: { ...props.settings.header, firstPageDifferent: event.target.checked }, footer: { ...props.settings.footer, firstPageDifferent: event.target.checked } })} type="checkbox" />Different first page</label>
            <label className="inspector-check"><input checked={props.settings.showPageNumbers} onChange={event => props.onSettingsChange({ ...props.settings, showPageNumbers: event.target.checked })} type="checkbox" />Show page numbers</label>
          </div>
        ) : props.activeTab === 'import' ? (
          <div className="inspector-stack">
            <strong>{props.document.importReport.sourceFilename ?? 'Created in JobOS'}</strong>
            {props.document.importReport.issues.length ? <ul>{props.document.importReport.issues.map(issue => <li key={issue.code}><span>{issue.severity}</span>{issue.message} · {issue.count}</li>)}</ul> : <p>No import warnings.</p>}
          </div>
        ) : props.activeTab === 'comments' ? (
          <div className="inspector-stack document-review-stack">
            <section><strong>Suggestions</strong><small>{suggestions.length} unresolved</small>
              {suggestions.length ? <ul className="suggestion-list">{suggestions.map(suggestion => <li key={suggestion.suggestionId}><span>{suggestion.kind} {suggestion.structural ? 'block' : 'text'}</span><p>{suggestion.preview || 'Empty block'}</p><div><button onClick={() => props.onResolveSuggestion(suggestion, 'accept')} type="button">Accept</button><button onClick={() => props.onResolveSuggestion(suggestion, 'reject')} type="button">Reject</button></div></li>)}</ul> : <p>No unresolved suggestions.</p>}
            </section>
            <section><strong>Comments</strong><small>{props.selectedBlockId ? `Selected ${props.selectedBlockId.slice(0, 16)}…` : 'Place the cursor in a block to comment'}</small>
              <label>Add comment<textarea aria-label="Comment text" disabled={!props.selectedBlockId} maxLength={2000} onChange={event => setCommentBody(event.target.value)} rows={3} value={commentBody} /></label><button disabled={!props.selectedBlockId || !commentBody.trim()} onClick={addComment} type="button">Add comment</button>
              {props.comments.length ? <ul className="comment-list">{props.comments.map(comment => <li className={comment.resolvedAt ? 'resolved' : undefined} key={comment.commentId}><span>{comment.author}</span><p>{comment.body}</p><small>{dateLabel(comment.createdAt)}</small><button onClick={() => toggleComment(comment)} type="button">{comment.resolvedAt ? 'Reopen' : 'Resolve'}</button></li>)}</ul> : <p>No comments yet.</p>}
            </section>
          </div>
        ) : props.activeTab === 'history' ? (
          <div className="inspector-stack"><div className="history-heading"><strong>Revision {props.document.revision}</strong><button disabled={props.historyBusy} onClick={props.onCreateSnapshot} type="button">Create checkpoint</button></div>{props.historyBusy ? <p role="status">Loading history…</p> : props.snapshots.length ? <ul className="snapshot-list">{props.snapshots.map(snapshot => <li key={snapshot.snapshotId}><span>Revision {snapshot.documentRevision} · {snapshot.reason.replaceAll('_', ' ')}</span><p>{snapshot.label || `${snapshot.actor} checkpoint`}</p><small>{dateLabel(snapshot.createdAt)}</small><button onClick={() => props.onRestoreSnapshot(snapshot)} type="button">Restore</button></li>)}</ul> : <p>No checkpoints yet.</p>}</div>
        ) : (
          <div className="inspector-stack"><strong>Selection formatting</strong><p>Use the ribbon for text, paragraph, list, table, link, and page-break controls.</p><button disabled={!props.selectedBlockId} onClick={props.onToggleSelectedBlockLock} type="button">{props.selectedBlockLocked ? 'Unlock selected block' : 'Lock selected block'}</button></div>
        )}
      </div>
    </aside>
  )
}
