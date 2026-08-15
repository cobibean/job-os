// This file is part of JobOS's modified GenOffice-derived package; see this package's UPSTREAM.md.
import type { PmNode } from './editor/convert.js'

export interface ContextBlock {
  id: string
  index: number
  type: string
  text: string
  protected: boolean
  attrs?: Record<string, unknown>
}

export interface DocumentContext {
  revision: string
  blocks: ContextBlock[]
}

export type StructuredBlockInput = { type?: 'paragraph' | 'heading' | 'listItem'; text: string; level?: number }
export type StructuredDocumentOperation =
  | { type: 'replace_block_text'; blockId: string; expectedCurrentText: string; text: string }
  | { type: 'replace_blocks'; blockIds: string[]; blocks: StructuredBlockInput[] }
  | { type: 'insert_blocks'; afterBlockId: string | null; blocks: StructuredBlockInput[] }
  | { type: 'delete_blocks'; blockIds: string[] }

export interface OperationResult {
  document: PmNode
  revision: string
  changedBlockIds: string[]
}

function textOf(node: PmNode): string {
  if (typeof node.text === 'string') return node.text
  return (node.content ?? []).map(textOf).join('')
}

function idOf(node: PmNode, index: number): string {
  const anchor = node.attrs?.docxIndex
  return typeof anchor === 'number' ? `docx:${anchor}` : `new:${index}`
}

function hash(value: string): string {
  let state = 0x811c9dc5
  for (let index = 0; index < value.length; index++) {
    state ^= value.charCodeAt(index)
    state = Math.imul(state, 0x01000193)
  }
  return (state >>> 0).toString(16).padStart(8, '0')
}

export function serializeDocumentContext(document: PmNode): DocumentContext {
  const blocks = (document.content ?? []).map((node, index) => ({
    id: idOf(node, index),
    index,
    type: node.type,
    text: textOf(node),
    protected: node.type === 'docProtected',
    attrs: node.attrs,
  }))
  return { revision: hash(JSON.stringify(blocks)), blocks }
}

function inputNode(input: StructuredBlockInput): PmNode {
  const type = input.type === 'heading' ? 'docHeading' : input.type === 'listItem' ? 'docListItem' : 'docParagraph'
  return {
    type,
    attrs: {
      docxIndex: null,
      styleId: null,
      aiChanged: true,
      ...(type === 'docHeading' ? { level: input.level ?? 2 } : {}),
      ...(type === 'docListItem' ? { kind: 'bullet', numId: null, ilvl: 0 } : {}),
    },
    content: input.text ? [{ type: 'text', text: input.text }] : undefined,
  }
}

function replaceTextPreservingMarks(node: PmNode, replacement: string): PmNode {
  const content = [...(node.content ?? [])]
  const textIndexes = content.map((child, index) => typeof child.text === 'string' ? index : -1).filter(index => index >= 0)
  if (!textIndexes.length) return { ...node, content: replacement ? [{ type: 'text', text: replacement }] : undefined }
  const first = textIndexes[0]!
  const firstText = content[first]!
  content[first] = { ...firstText, text: replacement }
  for (const index of textIndexes.slice(1)) content[index] = { ...content[index]!, text: '' }
  return { ...node, attrs: { ...node.attrs, aiChanged: true }, content: content.filter(child => child.text !== '') }
}

export function applyStructuredOperations(document: PmNode, operations: StructuredDocumentOperation[]): OperationResult {
  let content = [...(document.content ?? [])]
  const changedBlockIds: string[] = []
  for (const operation of operations) {
    const byId = new Map(content.map((node, index) => [idOf(node, index), { node, index }]))
    if (operation.type === 'replace_block_text') {
      const target = byId.get(operation.blockId)
      if (!target) throw new Error(`document_block_not_found:${operation.blockId}`)
      if (target.node.type === 'docProtected') throw new Error(`document_block_protected:${operation.blockId}`)
      const current = textOf(target.node)
      if (current !== operation.expectedCurrentText) throw new Error(`document_stale_text:${operation.blockId}`)
      content[target.index] = replaceTextPreservingMarks(target.node, operation.text)
      changedBlockIds.push(operation.blockId)
      continue
    }
    if (operation.type === 'insert_blocks') {
      const at = operation.afterBlockId === null ? 0 : (byId.get(operation.afterBlockId)?.index ?? -2) + 1
      if (at < 0) throw new Error(`document_block_not_found:${operation.afterBlockId}`)
      content.splice(at, 0, ...operation.blocks.map(inputNode))
      changedBlockIds.push(...operation.blocks.map((_, index) => `new:${at + index}`))
      continue
    }
    const indexes = operation.blockIds.map(id => {
      const target = byId.get(id)
      if (!target) throw new Error(`document_block_not_found:${id}`)
      if (target.node.type === 'docProtected') throw new Error(`document_block_protected:${id}`)
      return target.index
    }).sort((left, right) => left - right)
    if (operation.type === 'delete_blocks') {
      for (const index of [...indexes].reverse()) content.splice(index, 1)
      changedBlockIds.push(...operation.blockIds)
    } else {
      const first = indexes[0] ?? 0
      for (const index of [...indexes].reverse()) content.splice(index, 1)
      content.splice(first, 0, ...operation.blocks.map(inputNode))
      changedBlockIds.push(...operation.blockIds)
    }
  }
  if (!content.length) content = [inputNode({ text: '' })]
  const next = { ...document, content }
  return { document: next, revision: serializeDocumentContext(next).revision, changedBlockIds }
}
