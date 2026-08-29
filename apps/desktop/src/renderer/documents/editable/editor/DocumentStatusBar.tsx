import type { DocumentSaveState } from './useDocumentAutosave'

interface DocumentStatusBarProps {
  characters: number
  estimatedPages: number
  saveState: DocumentSaveState
  words: number
  zoom: number
  onZoomChange: (zoom: number) => void
}

export function DocumentStatusBar(props: DocumentStatusBarProps) {
  return (
    <footer className="document-status-bar">
      <span>{props.words} words</span>
      <span>{props.characters} characters</span>
      <span>Page 1 of ~{Math.max(1, props.estimatedPages)}</span>
      <span className={`document-save-pill ${props.saveState}`}>{props.saveState === 'saving' ? 'Saving…' : props.saveState === 'unsaved' ? 'Unsaved changes' : props.saveState === 'conflict' ? 'Conflict' : props.saveState === 'error' ? 'Save failed' : 'Saved'}</span>
      <label className="document-zoom">Zoom<input aria-label="Editor zoom" max="200" min="50" onChange={event => props.onZoomChange(Number(event.target.value))} type="range" value={props.zoom} /><span>{props.zoom}%</span></label>
    </footer>
  )
}
