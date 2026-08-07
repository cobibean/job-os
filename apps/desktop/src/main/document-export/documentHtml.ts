import type { DocumentSettings, EditableDocument, TiptapMarkJson, TiptapNodeJson } from '../../shared/editableDocuments.js'
import { unresolvedSuggestionCount, validateEditableContent } from '../../shared/editableDocumentSchema.js'

const SAFE_LINK = /^(https?:|mailto:)/i
const SAFE_IMAGE = /^data:image\/(png|jpeg|gif);base64,[A-Za-z0-9+/]*={0,2}$/
const SAFE_COLOR = /^#[0-9a-f]{6}$/i

export function escapeDocumentHtml(value: string): string {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;')
}

function markText(value: string, marks: TiptapMarkJson[]): string {
  return marks.reduce((html, mark) => {
    const attrs = mark.attrs ?? {}
    if (mark.type === 'bold') return `<strong>${html}</strong>`
    if (mark.type === 'italic') return `<em>${html}</em>`
    if (mark.type === 'underline') return `<u>${html}</u>`
    if (mark.type === 'strike') return `<s>${html}</s>`
    if (mark.type === 'code') return `<code>${html}</code>`
    if (mark.type === 'link') {
      const href = typeof attrs.href === 'string' && SAFE_LINK.test(attrs.href) ? escapeDocumentHtml(attrs.href) : ''
      return href ? `<a href="${href}">${html}</a>` : html
    }
    if (mark.type === 'highlight') {
      const color = typeof attrs.color === 'string' && SAFE_COLOR.test(attrs.color) ? attrs.color : '#fff59d'
      return `<mark style="background:${color}">${html}</mark>`
    }
    if (mark.type === 'textStyle') {
      const styles: string[] = []
      if (typeof attrs.color === 'string' && SAFE_COLOR.test(attrs.color)) styles.push(`color:${attrs.color}`)
      if (typeof attrs.fontFamily === 'string' && /^[A-Za-z ]{1,40}$/.test(attrs.fontFamily)) styles.push(`font-family:${attrs.fontFamily}`)
      if (typeof attrs.fontSize === 'string' && /^([8-9]|[1-6][0-9]|7[0-2])pt$/.test(attrs.fontSize)) styles.push(`font-size:${attrs.fontSize}`)
      if (typeof attrs.lineHeight === 'string' && /^(1|1\.15|1\.5|2)$/.test(attrs.lineHeight)) styles.push(`line-height:${attrs.lineHeight}`)
      if (typeof attrs.backgroundColor === 'string' && SAFE_COLOR.test(attrs.backgroundColor)) styles.push(`background-color:${attrs.backgroundColor}`)
      return styles.length ? `<span style="${styles.join(';')}">${html}</span>` : html
    }
    return html
  }, escapeDocumentHtml(value))
}

function childHtml(node: TiptapNodeJson): string {
  return (node.content ?? []).map(renderNode).join('')
}

function alignment(node: TiptapNodeJson): string {
  const value = node.attrs?.textAlign
  return typeof value === 'string' && ['left', 'center', 'right', 'justify'].includes(value) ? ` style="text-align:${value}"` : ''
}

function renderNode(node: TiptapNodeJson): string {
  if (node.type === 'text') return markText(node.text ?? '', node.marks ?? [])
  if (node.type === 'hardBreak') return '<br>'
  if (node.type === 'doc') return childHtml(node)
  if (node.type === 'jobosSection') return `<section data-jobos-id="${escapeDocumentHtml(String(node.attrs?.jobosId ?? ''))}">${childHtml(node)}</section>`
  if (node.type === 'paragraph') return `<p${alignment(node)}>${childHtml(node) || '<br>'}</p>`
  if (node.type === 'heading') {
    const level = [1, 2, 3].includes(Number(node.attrs?.level)) ? Number(node.attrs?.level) : 2
    return `<h${level}${alignment(node)}>${childHtml(node)}</h${level}>`
  }
  if (node.type === 'bulletList') return `<ul>${childHtml(node)}</ul>`
  if (node.type === 'orderedList') return `<ol start="${Math.max(1, Number(node.attrs?.start) || 1)}">${childHtml(node)}</ol>`
  if (node.type === 'listItem') return `<li>${childHtml(node)}</li>`
  if (node.type === 'blockquote') return `<blockquote>${childHtml(node)}</blockquote>`
  if (node.type === 'horizontalRule') return '<hr>'
  if (node.type === 'pageBreak') return '<div class="explicit-page-break" aria-hidden="true"></div>'
  if (node.type === 'table') return `<table><tbody>${childHtml(node)}</tbody></table>`
  if (node.type === 'tableRow') return `<tr>${childHtml(node)}</tr>`
  if (node.type === 'tableHeader' || node.type === 'tableCell') {
    const tag = node.type === 'tableHeader' ? 'th' : 'td'
    const background = typeof node.attrs?.backgroundColor === 'string' && SAFE_COLOR.test(node.attrs.backgroundColor)
      ? ` style="background-color:${node.attrs.backgroundColor}"`
      : ''
    return `<${tag} colspan="${Math.max(1, Number(node.attrs?.colspan) || 1)}" rowspan="${Math.max(1, Number(node.attrs?.rowspan) || 1)}"${background}>${childHtml(node)}</${tag}>`
  }
  if (node.type === 'image') {
    const src = typeof node.attrs?.src === 'string' && SAFE_IMAGE.test(node.attrs.src) ? node.attrs.src : ''
    if (!src) return ''
    const alt = escapeDocumentHtml(typeof node.attrs?.alt === 'string' ? node.attrs.alt : '')
    const width = Math.min(1200, Math.max(1, Number(node.attrs?.width) || 600))
    return `<img alt="${alt}" src="${src}" style="max-width:100%;width:${width}px">`
  }
  return ''
}

function templateHtml(value: string): string {
  return value.split(/(\{page\}|\{pages\})/g).map(part => part === '{page}'
    ? '<span class="jobos-page-number" aria-label="page number"></span>'
    : part === '{pages}'
      ? '<span class="jobos-page-count" aria-label="page count"></span>'
      : escapeDocumentHtml(part)).join('')
}

function pageCss(settings: DocumentSettings): string {
  const size = settings.pageSize === 'a4' ? 'A4' : 'Letter'
  const margin = settings.marginsInches
  const firstPage = settings.header.firstPageDifferent || settings.footer.firstPageDifferent
    ? '@page:first{@top-center{content:none}@bottom-center{content:none}}'
    : ''
  return `@page{size:${size} portrait;margin:${margin.top}in ${margin.right}in ${margin.bottom}in ${margin.left}in;@top-center{content:element(jobosHeader)}@bottom-center{content:element(jobosFooter)}}${firstPage}`
}

export function renderEditableDocumentHtml(
  document: EditableDocument,
  options: { allowUnresolvedSuggestions?: boolean } = {}
): string {
  validateEditableContent(document.content, document.settings, document.comments)
  if (!options.allowUnresolvedSuggestions && unresolvedSuggestionCount(document.content) > 0) {
    throw new Error('Resolve every suggestion before export or publication')
  }
  const body = renderNode(document.content)
  const settings = document.settings
  const header = [settings.header.left, settings.header.center, settings.header.right].map(templateHtml)
  const footer = [settings.footer.left, settings.footer.center, settings.footer.right].map(templateHtml)
  if (settings.showPageNumbers && !settings.footer.center.includes('{page}')) {
    footer[1] = `${footer[1]}${footer[1] ? ' · ' : ''}Page <span class="jobos-page-number" aria-label="page number"></span>`
  }
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none'; frame-src 'none'; connect-src 'none'; media-src 'none'; object-src 'none'"><title>${escapeDocumentHtml(document.documentLabel)}</title><style>${pageCss(settings)}*{box-sizing:border-box}html,body{margin:0;color:#20242a;background:#fff;font-family:${settings.defaultFontFamily},Arial,sans-serif;font-size:${settings.defaultFontSizePt}pt;line-height:1.25}body{print-color-adjust:exact;-webkit-print-color-adjust:exact}p{margin:0 0 .72em}h1{font-size:2em}h2{font-size:1.45em}h3{font-size:1.18em}img{height:auto}table{width:100%;border-collapse:collapse}thead{display:table-header-group}td,th{padding:.35em .45em;border:1px solid #9da3ac;vertical-align:top}.explicit-page-break{break-after:page}.jobos-print-header,.jobos-print-footer{display:grid;width:100%;grid-template-columns:1fr 1fr 1fr;gap:12px;font-size:9pt;color:#5f6670}.jobos-print-header{position:running(jobosHeader)}.jobos-print-footer{position:running(jobosFooter)}.jobos-print-header>span:nth-child(2),.jobos-print-footer>span:nth-child(2){text-align:center}.jobos-print-header>span:last-child,.jobos-print-footer>span:last-child{text-align:right}.jobos-page-number::before{content:counter(page)}.jobos-page-count::before{content:counter(pages)}</style></head><body><header class="jobos-print-header"><span>${header[0]}</span><span>${header[1]}</span><span>${header[2]}</span></header><main>${body}</main><footer class="jobos-print-footer"><span>${footer[0]}</span><span>${footer[1]}</span><span>${footer[2]}</span></footer></body></html>`
}
