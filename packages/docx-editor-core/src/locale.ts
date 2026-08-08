export type Lang = 'en' | 'zh' | 'zh-TW' | 'ja' | 'ko' | 'fr' | 'de' | 'es' | 'th' | 'id' | 'ru' | 'ar' | 'pt' | 'it' | 'pl' | 'nl' | 'ms' | 'he' | 'hi'
export type StringKey = string
export type Params = Record<string, string | number>

let activeLanguage: Lang = 'zh'

export const DATE_LOCALES: Record<Lang, string> = {
  en: 'en-US', zh: 'zh-CN', 'zh-TW': 'zh-TW', ja: 'ja-JP', ko: 'ko-KR', fr: 'fr-FR',
  de: 'de-DE', es: 'es-ES', th: 'th-TH', id: 'id-ID', ru: 'ru-RU', ar: 'ar-SA',
  pt: 'pt-BR', it: 'it-IT', pl: 'pl-PL', nl: 'nl-NL', ms: 'ms-MY', he: 'he-IL', hi: 'hi-IN',
}

const chinese: Record<string, string> = {
  editorCoverTitle: '文档标题', editorCoverSubtitle: '副标题', editorCoverAuthor: '作者',
  aiCmdReplacedCount: '共替换 {count} 处', aiCmdNone: '没有匹配的内容',
  aiCmdNoneSkipped: '没有匹配的内容；跳过 {count} 个受保护块', aiCmdSkipped: '；跳过 {count} 个受保护块',
}

const english: Record<string, string> = {
  editorEdit: 'Edit', editorEditFormulaLatex: 'Edit equation (LaTeX)', editorEquation: 'Equation',
  editorPageBreak: 'Page Break', editorProtectedContent: 'Protected content', editorUnknownAuthor: 'Unknown author',
  editorMoveImage: 'Move picture', editorMoveTable: 'Move table', editorMoveChart: 'Move chart',
  editorMoveEquation: 'Move equation', editorMoveTextbox: 'Move text box', editorFootnote: 'Footnote',
  editorEndnote: 'Endnote', editorCrossReference: 'Cross-reference', editorSectionBreak: 'Section break',
  editorContentControl: 'Content control', editorOleExcel: 'Embedded spreadsheet', editorOleWord: 'Embedded document',
  editorOlePpt: 'Embedded presentation', editorOlePdf: 'Embedded PDF', editorOleGeneric: 'Embedded object',
  editorDefaultAuthor: 'JobOS', editorChartSeries: 'Series {index}', editorCoverTitle: 'Document title',
  editorCoverSubtitle: 'Subtitle', editorCoverAuthor: 'Author', aiCmdReplacedCount: 'Replaced {count} occurrence(s)',
  aiCmdNone: 'No matching content', aiCmdNoneSkipped: 'No matching content; {skipped} protected block(s) skipped',
  aiCmdSkipped: '{skipped} protected block(s) skipped',
}

export const getLang = (): Lang => activeLanguage
export const setModuleLang = (lang: Lang): void => { activeLanguage = lang }
export function t(key: StringKey, params: Params = {}): string {
  const dictionary = activeLanguage === 'zh' ? { ...english, ...chinese } : english
  let value = dictionary[key] ?? key.replace(/^(editor|aiCmd)/, '').replace(/([a-z])([A-Z])/g, '$1 $2')
  for (const [name, replacement] of Object.entries(params)) value = value.replaceAll(`{${name}}`, String(replacement))
  return value
}
