import sanitizeHtml from 'sanitize-html'

const ALLOWED_TAGS = [
  'p', 'h1', 'h2', 'h3', 'strong', 'em', 'u', 's', 'a', 'ul', 'ol', 'li', 'blockquote',
  'br', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img', 'span'
]

const SAFE_IMAGE = /^data:image\/(?:png|jpeg|gif);base64,[A-Za-z0-9+/]*={0,2}$/

/** Sanitize Mammoth output before it is exposed to any DOM parser. */
export function sanitizeImportedHtml(html: string): string {
  return sanitizeHtml(html, {
    allowedTags: ALLOWED_TAGS,
    allowedAttributes: {
      a: ['href'],
      img: ['src', 'alt', 'width', 'height'],
      th: ['colspan', 'rowspan'],
      td: ['colspan', 'rowspan'],
      p: ['style'],
      h1: ['style'], h2: ['style'], h3: ['style'], span: ['style']
    },
    allowedSchemes: ['http', 'https', 'mailto'],
    allowedSchemesByTag: { img: ['data'] },
    allowedSchemesAppliedToAttributes: ['href', 'src'],
    allowProtocolRelative: false,
    allowedStyles: {
      '*': {
        'text-align': [/^(?:left|center|right|justify)$/],
        'font-family': [/^(?:Arial|Calibri|Times New Roman|Georgia|Garamond)$/],
        'font-size': [/^(?:[89]|[1-6][0-9]|7[0-2])(?:\.\d+)?pt$/],
        'line-height': [/^(?:0\.[89]|[12](?:\.\d+)?|3(?:\.0+)?)$/],
        color: [/^#[0-9a-f]{6}$/i],
        'background-color': [/^#[0-9a-f]{6}$/i]
      }
    },
    exclusiveFilter(frame) {
      if (frame.tag === 'img') return typeof frame.attribs.src !== 'string' || !SAFE_IMAGE.test(frame.attribs.src)
      const colspan = frame.attribs.colspan
      const rowspan = frame.attribs.rowspan
      return [colspan, rowspan].some(value => value !== undefined && (!/^\d{1,2}$/.test(value) || Number(value) < 1 || Number(value) > 20))
    },
    disallowedTagsMode: 'discard',
    enforceHtmlBoundary: true
  })
}
