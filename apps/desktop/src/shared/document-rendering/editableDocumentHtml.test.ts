// @vitest-environment node

import { describe, expect, it } from 'vitest'

import type { EditableDocument } from '../editableDocuments.js'
import { createBlankDocument, defaultDocumentSettings } from '../editableDocumentSchema.js'
import { escapeDocumentHtml, renderEditableDocumentHtml } from './editableDocumentHtml.js'

function documentFixture(): EditableDocument {
  return {
    schemaVersion: 1,
    documentId: 'edoc_ABCDEFGHIJKLMNOPQRSTUVWX',
    jobId: 'job-7',
    documentKey: 'cover_letter',
    documentLabel: 'Cover Letter',
    revision: 2,
    content: createBlankDocument('cover_letter'),
    settings: defaultDocumentSettings(),
    comments: [],
    sourceArtifactId: null,
    sourceFilename: null,
    sourceSha256: null,
    publishedRevision: null,
    importReport: { sourceFilename: null, importedAt: null, issues: [] },
    createdAt: '2026-08-07T00:00:00Z',
    updatedAt: '2026-08-07T00:00:00Z'
  }
}

describe('trusted print HTML', () => {
  it('escapes content, labels, headers, and links without an HTML sanitizer bypass', () => {
    const document = documentFixture()
    document.settings.header.left = '<img src=x onerror=alert(1)>'
    const paragraph = document.content.content?.[1]?.content?.[0]
    if (!paragraph) throw new Error('Fixture paragraph missing')
    paragraph.content = [{ type: 'text', text: '<script>alert(1)</script>', marks: [{ type: 'link', attrs: { href: 'https://example.com/?a=<bad>' } }] }]
    const html = renderEditableDocumentHtml(document)
    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;')
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;')
    expect(html).not.toContain('<script>alert')
    expect(html).not.toContain('<img src=x')
  })

  it('maps page size, margins, page breaks, tables, and safe data images', () => {
    const document = documentFixture()
    document.settings.pageSize = 'a4'
    document.settings.marginsInches = { top: 0.5, right: 0.75, bottom: 1, left: 1.25 }
    document.content.content?.[1]?.content?.push({ type: 'pageBreak', attrs: { jobosId: `node_${crypto.randomUUID()}`, semanticRole: null, locked: false, origin: 'user', structuralSuggestion: null } })
    const html = renderEditableDocumentHtml(document)
    expect(html).toContain('@page{size:A4 portrait;margin:0.5in 0.75in 1in 1.25in;')
    expect(html).toContain('class="explicit-page-break"')
  })

  it('preserves text background color in authoritative PDF HTML', () => {
    const document = documentFixture()
    const paragraph = document.content.content?.[1]?.content?.[0]
    if (!paragraph) throw new Error('Fixture paragraph missing')
    paragraph.content = [{
      type: 'text',
      text: 'Highlighted',
      marks: [{ type: 'textStyle', attrs: { backgroundColor: '#fff59d' } }]
    }]
    expect(renderEditableDocumentHtml(document)).toContain('background-color:#fff59d')
  })

  it('allows preview but refuses export while a suggestion remains unresolved', () => {
    const document = documentFixture()
    const paragraph = document.content.content?.[1]?.content?.[0]
    if (!paragraph) throw new Error('Fixture paragraph missing')
    paragraph.content = [{ type: 'text', text: 'Pending', marks: [{ type: 'suggestion', attrs: { suggestionId: 'sug_pending', kind: 'insert', author: 'user', createdAt: '2026-08-07T00:00:00Z' } }] }]
    expect(() => renderEditableDocumentHtml(document)).toThrow('Resolve every suggestion')
    expect(renderEditableDocumentHtml(document, { allowUnresolvedSuggestions: true })).toContain('Pending')
  })

  it('escapes all special HTML characters', () => {
    expect(escapeDocumentHtml(`<>&"'`)).toBe('&lt;&gt;&amp;&quot;&#39;')
  })
})
