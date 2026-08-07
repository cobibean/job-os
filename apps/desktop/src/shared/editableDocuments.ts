export type DocumentKey = 'resume' | 'cover_letter' | 'references'
export type SemanticRole =
  | 'contact'
  | 'summary'
  | 'experience'
  | 'experience_achievement'
  | 'education'
  | 'skills'
  | 'reference'
  | 'cover_letter_body'
  | 'closing'
  | 'custom'
export type PageSize = 'letter' | 'a4'
export type SnapshotReason = 'import' | 'before_agent_edit' | 'manual' | 'before_publish' | 'before_restore'
export type DocumentActor = 'user' | 'jobhunter' | 'import' | 'system'
export type AllowedNodeType =
  | 'doc'
  | 'jobosSection'
  | 'paragraph'
  | 'heading'
  | 'bulletList'
  | 'orderedList'
  | 'listItem'
  | 'blockquote'
  | 'horizontalRule'
  | 'hardBreak'
  | 'pageBreak'
  | 'table'
  | 'tableRow'
  | 'tableHeader'
  | 'tableCell'
  | 'image'
  | 'text'
export type AllowedMarkType =
  | 'bold'
  | 'italic'
  | 'underline'
  | 'strike'
  | 'textStyle'
  | 'link'
  | 'jobosField'
  | 'suggestion'

export interface StructuralSuggestion {
  suggestionId: `sug_${string}`
  kind: 'insert' | 'delete'
  author: 'user'
  createdAt: string
}

export interface JobOsNodeAttrs {
  jobosId: `node_${string}`
  semanticRole: SemanticRole | null
  locked: boolean
  origin: DocumentActor
  structuralSuggestion: StructuralSuggestion | null
  label?: string
}

export interface TiptapMarkJson {
  type: string
  attrs?: Record<string, unknown>
}

export interface TiptapNodeJson {
  type: string
  attrs?: Record<string, unknown>
  content?: TiptapNodeJson[]
  marks?: TiptapMarkJson[]
  text?: string
}

export type TiptapDocumentJson = TiptapNodeJson

export interface HeaderFooterSettings {
  left: string
  center: string
  right: string
  firstPageDifferent: boolean
}

export interface DocumentSettings {
  pageSize: PageSize
  orientation: 'portrait'
  marginsInches: { top: number; right: number; bottom: number; left: number }
  defaultFontFamily: 'Arial' | 'Calibri' | 'Times New Roman' | 'Georgia' | 'Garamond'
  defaultFontSizePt: number
  header: HeaderFooterSettings
  footer: HeaderFooterSettings
  showPageNumbers: boolean
}

export interface DocumentComment {
  commentId: `comment_${string}`
  blockId: `node_${string}`
  author: 'user' | 'jobhunter'
  body: string
  createdAt: string
  resolvedAt: string | null
}

export interface DocumentImportIssue {
  code: string
  severity: 'normalized' | 'dropped'
  message: string
  count: number
}

export interface DocumentImportReport {
  sourceFilename: string | null
  importedAt: string | null
  issues: DocumentImportIssue[]
}

export interface EditableDocument {
  schemaVersion: 1
  documentId: `edoc_${string}`
  jobId: string
  documentKey: DocumentKey
  documentLabel: 'Resume' | 'Cover Letter' | 'References'
  revision: number
  content: TiptapDocumentJson
  settings: DocumentSettings
  comments: DocumentComment[]
  sourceArtifactId: string | null
  sourceFilename: string | null
  sourceSha256: string | null
  publishedRevision: number | null
  importReport: DocumentImportReport
  createdAt: string
  updatedAt: string
}

export interface EditableDocumentSummary {
  documentId: `edoc_${string}`
  jobId: string
  documentKey: DocumentKey
  documentLabel: 'Resume' | 'Cover Letter' | 'References'
  revision: number
  publishedRevision: number | null
  sourceArtifactId: string | null
  createdAt: string
  updatedAt: string
}

export interface EditableDocumentSnapshot {
  snapshotId: `dsnap_${string}`
  documentId: `edoc_${string}`
  documentRevision: number
  reason: SnapshotReason
  actor: DocumentActor
  label: string | null
  createdAt: string
}

export interface SemanticOutlineBlock {
  blockId: `node_${string}`
  parentSectionId: `node_${string}` | null
  nodeType: string
  semanticRole: SemanticRole | null
  locked: boolean
  text: string
}

export interface DocumentDraftOutline {
  documentId: `edoc_${string}`
  documentKey: DocumentKey
  documentLabel: string
  revision: number
  settings: DocumentSettings
  outline: SemanticOutlineBlock[]
  unresolvedSuggestionCount: number
  commentCount: number
}

export type JobHunterOperation =
  | { type: 'replace_block_text'; blockId: `node_${string}`; expectedText: string; replacementText: string }
  | { type: 'insert_block_after'; afterBlockId: `node_${string}`; nodeType: 'paragraph' | 'listItem'; semanticRole: SemanticRole; text: string }
  | { type: 'delete_block'; blockId: `node_${string}`; expectedText: string }
  | { type: 'move_block_after'; blockId: `node_${string}`; afterBlockId: `node_${string}` }
  | { type: 'set_block_role'; blockId: `node_${string}`; semanticRole: SemanticRole }

export interface OperationReceipt {
  document: EditableDocument
  changedBlockIds: `node_${string}`[]
  changes: { blockId: `node_${string}`; before: string; after: string }[]
  snapshotId: `dsnap_${string}`
}

export type CreateEditableDocumentRequest = {
  mode: 'blank'
  documentKey: DocumentKey
  idempotencyKey: string
}

export interface SaveEditableDocumentRequest {
  baseRevision: number
  content: TiptapDocumentJson
  settings: DocumentSettings
  comments: DocumentComment[]
  idempotencyKey: string
}

export interface CreateEditableDocumentSnapshotRequest {
  baseRevision: number
  reason: 'manual'
  label: string
  idempotencyKey: string
}

export interface RestoreEditableDocumentSnapshotRequest {
  baseRevision: number
  idempotencyKey: string
}

export interface ApplyEditableDocumentOperationsRequest {
  baseRevision: number
  operations: JobHunterOperation[]
  idempotencyKey: string
}

export interface UnavailableEditableDocumentOperation {
  available: false
  reason: string
}

export interface EditableDocumentPreview {
  documentId: `edoc_${string}`
  revision: number
  filename: string
  sha256: string
  bytes: ArrayBuffer
}

export interface EditableDocumentExportResult {
  cancelled: boolean
  filename: string | null
  message: string
}
