import { describe, expect, it } from 'vitest'
import { collectDocumentSuggestions, createBlankDocument, defaultDocumentSettings, resolveDocumentSuggestion, semanticOutline, stableSerialize, unresolvedSuggestionCount, validateEditableContent } from './editableDocumentSchema.js'

describe('editable document schema', () => {
  it.each(['resume', 'cover_letter', 'references'] as const)('creates and validates a stable %s template', key => {
    const content = createBlankDocument(key)
    expect(() => validateEditableContent(content)).not.toThrow()
    const outline = semanticOutline(content)
    expect(outline.length).toBeGreaterThan(2)
    expect(new Set(outline.map(block => block.blockId)).size).toBe(outline.length)
    expect(stableSerialize(JSON.parse(stableSerialize(content)))).toBe(stableSerialize(content))
  })
  it('locks the required protected template sections', () => {
    const resume = semanticOutline(createBlankDocument('resume'))
    expect(resume.find(block => block.semanticRole === 'contact')?.locked).toBe(true)
    const cover = semanticOutline(createBlankDocument('cover_letter'))
    expect(cover.find(block => block.semanticRole === 'closing')?.locked).toBe(true)
  })
  it('rejects duplicate IDs, unknown nodes, unsafe links, and invalid settings', () => {
    const content = createBlankDocument('resume')
    const section = content.content![0]!
    section.content![0]!.attrs!.jobosId = section.attrs!.jobosId
    expect(() => validateEditableContent(content)).toThrow('Duplicate')
    expect(() => validateEditableContent({ type: 'doc', content: [{ type: 'script' }] })).toThrow('Unknown node')
    const link = createBlankDocument('references'); link.content![1]!.content![0]!.content = [{ type: 'text', text: 'x', marks: [{ type: 'link', attrs: { href: 'javascript:alert(1)' } }] }]
    expect(() => validateEditableContent(link)).toThrow('Unsafe link')
    const settings = defaultDocumentSettings(); settings.marginsInches.top = .33
    expect(() => validateEditableContent(createBlankDocument('resume'), settings)).toThrow('margin')
  })
  it('projects bounded plain text and counts unique unresolved suggestions', () => {
    const content = createBlankDocument('references'); const block = content.content![1]!.content![0]!
    block.content = [{ type: 'text', text: 'Reference text', marks: [{ type: 'suggestion', attrs: { suggestionId: 'sug_one', kind: 'insert', author: 'user', createdAt: '2026-08-07T00:00:00Z' } }] }]
    expect(unresolvedSuggestionCount(content)).toBe(1)
    expect(semanticOutline(content).at(-1)?.text).toBe('Reference text')
  })
  it('accepts and rejects inline and structural suggestions without changing stable IDs', () => {
    const content = createBlankDocument('references'); const block = content.content![1]!.content![0]!
    const blockId = block.attrs!.jobosId
    block.content = [{ type: 'text', text: 'Reference text', marks: [{ type: 'suggestion', attrs: { suggestionId: 'sug_inline', kind: 'insert', author: 'user', createdAt: '2026-08-07T00:00:00Z' } }] }]
    expect(collectDocumentSuggestions(content)).toEqual([{ suggestionId: 'sug_inline', kind: 'insert', blockId, preview: 'Reference text', structural: false }])
    const accepted = resolveDocumentSuggestion(structuredClone(content), 'sug_inline', 'accept')
    expect(semanticOutline(accepted).at(-1)?.text).toBe('Reference text')
    expect(semanticOutline(accepted).at(-1)?.blockId).toBe(blockId)
    expect(unresolvedSuggestionCount(accepted)).toBe(0)
    const rejected = resolveDocumentSuggestion(structuredClone(content), 'sug_inline', 'reject')
    expect(semanticOutline(rejected).at(-1)?.text).toBe('')

    block.attrs!.structuralSuggestion = { suggestionId: 'sug_delete', kind: 'delete', author: 'user', createdAt: '2026-08-07T00:00:00Z' }
    const restored = resolveDocumentSuggestion(structuredClone(content), 'sug_delete', 'reject')
    expect(semanticOutline(restored).at(-1)?.blockId).toBe(blockId)
    expect(unresolvedSuggestionCount(restored)).toBe(1)
    const deleted = resolveDocumentSuggestion(structuredClone(content), 'sug_delete', 'accept')
    expect(semanticOutline(deleted).some(entry => entry.blockId === blockId)).toBe(false)
  })
  it('validates and rejects a structurally suggested image insertion', () => {
    const content = createBlankDocument('cover_letter')
    content.content![1]!.content!.push({
      type: 'image',
      attrs: {
        jobosId: `node_${crypto.randomUUID()}`,
        semanticRole: null,
        locked: false,
        origin: 'user',
        structuralSuggestion: { suggestionId: 'sug_image', kind: 'insert', author: 'user', createdAt: '2026-08-07T00:00:00Z' },
        src: 'data:image/png;base64,iVBORw0KGgo=',
        alt: 'Diagram',
        title: 'Diagram'
      }
    })
    expect(() => validateEditableContent(content)).not.toThrow()
    expect(unresolvedSuggestionCount(content)).toBe(1)
    const rejected = resolveDocumentSuggestion(content, 'sug_image', 'reject')
    expect(JSON.stringify(rejected)).not.toContain('sug_image')
  })
})
