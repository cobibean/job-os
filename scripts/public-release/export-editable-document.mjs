#!/usr/bin/env node
import { createHash } from 'node:crypto'
import { open, readFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

import { app } from 'electron'

function argument(name) {
  const index = process.argv.indexOf(name)
  const value = index >= 0 ? process.argv[index + 1] : undefined
  if (!value || !path.isAbsolute(value)) throw new Error(`${name} requires an absolute path`)
  return value
}

function settings(value) {
  return {
    pageSize: value.page_size,
    orientation: value.orientation,
    marginsInches: value.margins_inches,
    defaultFontFamily: value.default_font_family,
    defaultFontSizePt: value.default_font_size_pt,
    header: {
      left: value.header.left,
      center: value.header.center,
      right: value.header.right,
      firstPageDifferent: value.header.first_page_different
    },
    footer: {
      left: value.footer.left,
      center: value.footer.center,
      right: value.footer.right,
      firstPageDifferent: value.footer.first_page_different
    },
    showPageNumbers: value.show_page_numbers
  }
}

function canonicalDocument(value) {
  return {
    schemaVersion: value.schema_version,
    documentId: value.document_id,
    jobId: value.job_id,
    documentKey: value.document_key,
    documentLabel: value.document_label,
    revision: value.revision,
    content: value.content,
    settings: settings(value.settings),
    comments: value.comments.map(comment => ({
      commentId: comment.comment_id,
      blockId: comment.block_id,
      author: comment.author,
      body: comment.body,
      createdAt: comment.created_at,
      resolvedAt: comment.resolved_at
    })),
    sourceArtifactId: value.source_artifact_id,
    sourceFilename: value.source_filename,
    sourceSha256: value.source_sha256,
    publishedRevision: value.published_revision,
    importReport: {
      sourceFilename: value.import_report.source_filename,
      importedAt: value.import_report.imported_at,
      issues: value.import_report.issues
    },
    createdAt: value.created_at,
    updatedAt: value.updated_at
  }
}

async function writeExclusive(target, bytes) {
  const handle = await open(target, 'wx', 0o600)
  try {
    await handle.writeFile(bytes)
    await handle.sync()
  } finally {
    await handle.close()
  }
}

const input = argument('--input')
const docxPath = argument('--docx')
const pdfPath = argument('--pdf')
const resultPath = argument('--result')
if (path.extname(docxPath).toLowerCase() !== '.docx' || path.extname(pdfPath).toLowerCase() !== '.pdf') {
  throw new Error('output paths must use .docx and .pdf extensions')
}
if (path.extname(resultPath).toLowerCase() !== '.json') throw new Error('result path must use a .json extension')

async function exportDocuments() {
  const [{ exportEditableDocumentDocx }, { exportEditableDocumentPdf }] = await Promise.all([
    import('../../apps/desktop/dist/main/document-export/documentDocx.js'),
    import('../../apps/desktop/dist/main/document-export/pdfExporter.js')
  ])
  const document = canonicalDocument(JSON.parse(await readFile(input, 'utf8')))
  const docx = await exportEditableDocumentDocx(document)
  const pdf = await exportEditableDocumentPdf(document)
  await writeExclusive(docxPath, docx)
  await writeExclusive(pdfPath, pdf)
  const result = JSON.stringify({
    docx: { filename: path.basename(docxPath), sha256: createHash('sha256').update(docx).digest('hex') },
    pdf: { filename: path.basename(pdfPath), sha256: createHash('sha256').update(pdf).digest('hex') }
  })
  await writeExclusive(resultPath, Buffer.from(`${result}\n`, 'utf8'))
}

async function run() {
  const keepAlive = setInterval(() => {}, 1_000)
  try {
    await exportDocuments()
    app.exit(0)
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : 'Document export failed'}\n`)
    app.exit(1)
  } finally {
    clearInterval(keepAlive)
  }
}

app.on('window-all-closed', event => event.preventDefault())

if (app.isReady()) void run()
else app.once('ready', () => { void run() })
