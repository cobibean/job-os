import type { Editor } from '@tiptap/core'
import { useEffect, useState, type KeyboardEvent } from 'react'

interface DocumentRibbonProps {
  editor: Editor
}

function ToolButton({ active = false, disabled = false, label, onClick, shortcut }: {
  active?: boolean
  disabled?: boolean
  label: string
  onClick: () => void
  shortcut?: string
}) {
  return (
    <button
      aria-label={shortcut ? `${label} (${shortcut})` : label}
      aria-pressed={active || undefined}
      className={active ? 'is-active' : undefined}
      disabled={disabled}
      onClick={onClick}
      title={shortcut ? `${label} · ${shortcut}` : label}
      type="button"
    >
      {label}
    </button>
  )
}

export function DocumentRibbon({ editor }: DocumentRibbonProps) {
  const [, setRevision] = useState(0)
  const [linkOpen, setLinkOpen] = useState(false)
  const [linkValue, setLinkValue] = useState('https://')
  const [imageFile, setImageFile] = useState<File | null>(null)
  const [imageAlt, setImageAlt] = useState('')
  const [imageOpen, setImageOpen] = useState(false)

  useEffect(() => {
    const update = () => setRevision(value => value + 1)
    editor.on('selectionUpdate', update)
    editor.on('transaction', update)
    return () => {
      editor.off('selectionUpdate', update)
      editor.off('transaction', update)
    }
  }, [editor])

  const setLink = () => {
    const href = linkValue.trim()
    if (!/^(https?:|mailto:)/i.test(href)) return
    editor.chain().focus().extendMarkRange('link').setLink({ href }).run()
    setLinkOpen(false)
  }
  const suggestionAttributes = (kind: 'insert' | 'delete') => ({
    suggestionId: `sug_${globalThis.crypto.randomUUID()}`,
    kind,
    author: 'user',
    createdAt: new Date().toISOString()
  })
  const toggleSuggesting = () => {
    if (editor.isActive('suggestion', { kind: 'insert' })) editor.chain().focus().unsetMark('suggestion').run()
    else editor.chain().focus().setMark('suggestion', suggestionAttributes('insert')).run()
  }
  const suggestDeletion = () => {
    if (!editor.state.selection.empty) editor.chain().focus().setMark('suggestion', suggestionAttributes('delete')).run()
  }
  const suggestBlockDeletion = () => {
    const { $from } = editor.state.selection
    for (let depth = $from.depth; depth > 0; depth -= 1) {
      const node = $from.node(depth)
      if (typeof node.attrs.jobosId === 'string') {
        editor.chain().focus().updateAttributes(node.type.name, {
          structuralSuggestion: suggestionAttributes('delete')
        }).run()
        return
      }
    }
  }
  const structuralInsertion = () => editor.isActive('suggestion', { kind: 'insert' })
    ? suggestionAttributes('insert')
    : null
  const insertTable = () => {
    const suggestion = structuralInsertion()
    const chain = editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true })
    if (suggestion) chain.updateAttributes('table', { structuralSuggestion: suggestion })
    chain.run()
  }
  const insertPageBreak = () => {
    const suggestion = structuralInsertion()
    editor.chain().focus().insertContent({
      type: 'pageBreak',
      ...(suggestion ? { attrs: { structuralSuggestion: suggestion } } : {})
    }).run()
  }
  const insertImage = async () => {
    const alt = imageAlt.trim()
    if (!imageFile || !alt || imageFile.size > 1024 * 1024
      || !['image/png', 'image/jpeg', 'image/gif'].includes(imageFile.type)) return
    const bytes = new Uint8Array(await imageFile.arrayBuffer())
    let binary = ''
    for (const byte of bytes) binary += String.fromCharCode(byte)
    const suggestion = structuralInsertion()
    editor.chain().focus().insertContent({
      type: 'image',
      attrs: {
        src: `data:${imageFile.type};base64,${btoa(binary)}`,
        alt,
        title: alt,
        ...(suggestion ? { structuralSuggestion: suggestion } : {})
      }
    }).run()
    setImageFile(null)
    setImageAlt('')
    setImageOpen(false)
  }
  const handleToolbarKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),select:not(:disabled),input:not(:disabled)')]
    const current = controls.indexOf(document.activeElement as HTMLElement)
    if (current < 0 || controls.length === 0) return
    event.preventDefault()
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1
      : (current + (event.key === 'ArrowRight' ? 1 : -1) + controls.length) % controls.length
    controls[next]?.focus()
  }

  return (
    <div aria-label="Document formatting" className="document-ribbon" onKeyDown={handleToolbarKeys} role="toolbar">
      <div aria-label="History" className="ribbon-group">
        <ToolButton disabled={!editor.can().undo()} label="Undo" onClick={() => editor.chain().focus().undo().run()} shortcut="⌘Z" />
        <ToolButton disabled={!editor.can().redo()} label="Redo" onClick={() => editor.chain().focus().redo().run()} shortcut="⇧⌘Z" />
      </div>
      <div aria-label="Paragraph style" className="ribbon-group">
        <select
          aria-label="Paragraph style"
          onChange={event => {
            const value = event.target.value
            if (value === 'normal') editor.chain().focus().setParagraph().run()
            else if (value === 'quote') editor.chain().focus().toggleBlockquote().run()
            else editor.chain().focus().setHeading({ level: Number(value) as 1 | 2 | 3 }).run()
          }}
          value={editor.isActive('heading', { level: 1 }) ? '1'
            : editor.isActive('heading', { level: 2 }) ? '2'
              : editor.isActive('heading', { level: 3 }) ? '3'
                : editor.isActive('blockquote') ? 'quote' : 'normal'}
        >
          <option value="normal">Normal</option><option value="1">Title</option><option value="2">Subtitle</option><option value="3">Heading 3</option><option value="quote">Quote</option>
        </select>
        <select aria-label="Font family" defaultValue="Calibri" onChange={event => editor.chain().focus().setFontFamily(event.target.value).run()}>
          {['Arial', 'Calibri', 'Times New Roman', 'Georgia', 'Garamond'].map(font => <option key={font}>{font}</option>)}
        </select>
        <select aria-label="Font size" defaultValue="11" onChange={event => editor.chain().focus().setFontSize(`${event.target.value}pt`).run()}>
          {[8, 9, 10, 11, 12, 14, 16, 18, 24, 36, 48, 72].map(size => <option key={size}>{size}</option>)}
        </select>
        <select aria-label="Line spacing" defaultValue="1.15" onChange={event => editor.chain().focus().setLineHeight(event.target.value).run()}>
          <option value="1">Single</option><option value="1.15">1.15</option><option value="1.5">1.5</option><option value="2">Double</option>
        </select>
      </div>
      <div aria-label="Text formatting" className="ribbon-group">
        <ToolButton active={editor.isActive('bold')} label="Bold" onClick={() => editor.chain().focus().toggleBold().run()} shortcut="⌘B" />
        <ToolButton active={editor.isActive('italic')} label="Italic" onClick={() => editor.chain().focus().toggleItalic().run()} shortcut="⌘I" />
        <ToolButton active={editor.isActive('underline')} label="Underline" onClick={() => editor.chain().focus().toggleUnderline().run()} shortcut="⌘U" />
        <ToolButton active={editor.isActive('strike')} label="Strike" onClick={() => editor.chain().focus().toggleStrike().run()} />
        <label className="ribbon-color" title="Text color"><span>Text</span><input aria-label="Text color" onChange={event => editor.chain().focus().setColor(event.target.value).run()} type="color" /></label>
        <label className="ribbon-color" title="Highlight color"><span>Highlight</span><input aria-label="Highlight color" onChange={event => editor.chain().focus().setBackgroundColor(event.target.value).run()} type="color" /></label>
      </div>
      <div aria-label="Lists and alignment" className="ribbon-group">
        <ToolButton active={editor.isActive('bulletList')} label="Bullets" onClick={() => editor.chain().focus().toggleBulletList().run()} />
        <ToolButton active={editor.isActive('orderedList')} label="Numbering" onClick={() => editor.chain().focus().toggleOrderedList().run()} />
        <ToolButton disabled={!editor.can().sinkListItem('listItem')} label="Indent" onClick={() => editor.chain().focus().sinkListItem('listItem').run()} />
        <ToolButton disabled={!editor.can().liftListItem('listItem')} label="Outdent" onClick={() => editor.chain().focus().liftListItem('listItem').run()} />
        {(['left', 'center', 'right', 'justify'] as const).map(alignment => <ToolButton active={editor.isActive({ textAlign: alignment })} key={alignment} label={alignment[0]!.toUpperCase() + alignment.slice(1)} onClick={() => editor.chain().focus().setTextAlign(alignment).run()} />)}
      </div>
      <div aria-label="Insert" className="ribbon-group">
        <ToolButton label="Link" onClick={() => setLinkOpen(value => !value)} shortcut="⌘K" />
        <ToolButton label="Table" onClick={insertTable} />
        <ToolButton label="Image" onClick={() => setImageOpen(value => !value)} />
        <ToolButton label="Page break" onClick={insertPageBreak} shortcut="⌘↵" />
      </div>
      <div aria-label="Review suggestions" className="ribbon-group">
        <ToolButton active={editor.isActive('suggestion', { kind: 'insert' })} label="Suggesting" onClick={toggleSuggesting} />
        <ToolButton disabled={editor.state.selection.empty} label="Suggest deletion" onClick={suggestDeletion} />
        <ToolButton label="Suggest block deletion" onClick={suggestBlockDeletion} />
      </div>
      {editor.isActive('table') ? <div aria-label="Table layout" className="ribbon-group">
        <ToolButton label="Add row" onClick={() => editor.chain().focus().addRowAfter().run()} /><ToolButton label="Delete row" onClick={() => editor.chain().focus().deleteRow().run()} />
        <ToolButton label="Add column" onClick={() => editor.chain().focus().addColumnAfter().run()} /><ToolButton label="Delete column" onClick={() => editor.chain().focus().deleteColumn().run()} />
        <ToolButton disabled={!editor.can().mergeCells()} label="Merge cells" onClick={() => editor.chain().focus().mergeCells().run()} /><ToolButton disabled={!editor.can().splitCell()} label="Split cell" onClick={() => editor.chain().focus().splitCell().run()} />
        <ToolButton label="Toggle header row" onClick={() => editor.chain().focus().toggleHeaderRow().run()} /><ToolButton label="Delete table" onClick={() => editor.chain().focus().deleteTable().run()} />
      </div> : null}
      {linkOpen ? <div className="ribbon-link-popover"><label>Link address<input autoFocus onChange={event => setLinkValue(event.target.value)} value={linkValue} /></label><button onClick={setLink} type="button">Apply link</button><button onClick={() => { editor.chain().focus().unsetLink().run(); setLinkOpen(false) }} type="button">Remove</button></div> : null}
      {imageOpen ? <div className="ribbon-link-popover"><label>Image file<input accept="image/png,image/jpeg,image/gif" aria-label="Image file" onChange={event => setImageFile(event.target.files?.[0] ?? null)} type="file" /></label><label>Alternative text<input aria-label="Image alternative text" maxLength={500} onChange={event => setImageAlt(event.target.value)} value={imageAlt} /></label><button disabled={!imageFile || !imageAlt.trim()} onClick={() => void insertImage()} type="button">Insert image</button><button onClick={() => setImageOpen(false)} type="button">Cancel</button></div> : null}
    </div>
  )
}
