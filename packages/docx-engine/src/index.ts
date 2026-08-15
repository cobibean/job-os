// This file is part of JobOS's modified GenOffice-derived package; see this package's UPSTREAM.md.
export * from './types.js'
export { parseDocx, type ParseExtras } from './parse.js'
export {
  saveDocx,
  findChartWorkbookPath,
  readDocxPartBase64,
  type SaveBlock,
  type SaveOptions,
  type StyleUpsert,
  type ParsedDocFull,
} from './patch.js'
export {
  TABLE_HEADER_FILL,
  applyImageWrap,
  buildShapeParagraphXml,
  buildTextboxParagraphXml,
  buildWordArtParagraphXml,
  generateCaptionXml,
  generateIndexFieldXml,
  generateParagraphXml,
  generateTableModelXml,
  generateTableXml,
  generateTocFieldXml,
  mergePPrFormat,
  setPPrChange,
  stripPPrChange,
  patchFieldParagraphXml,
  patchImageParagraphXml,
  patchMathTokens,
  patchTableCellTexts,
  patchTextboxHeights,
  patchTextboxParas,
  patchTextboxSizes,
  patchShapeStyles,
  type ShapeStylePatch,
  patchDrawingExtent,
  buildLineParagraphXml,
  LINE_KINDS,
  type TextboxSizePatch,
  type CellTextsPatch,
  type FieldTextPatch,
  type GenerateContext,
  type ImagePatch,
  type TextboxParaPatch,
  type TableGenOptions,
  type TocEntry,
} from './generate.js'
export {
  buildChartPartXml,
  buildChartWorkbookXlsxBase64,
  patchChartWorkbookXlsxBase64,
  parseChartPartXml,
  patchChartPartXml,
  CHART_WORKBOOK_REL_TYPE,
  type ChartPatch,
  type ChartSeriesPatch,
} from './chart.js'
export {
  latexToOmml,
  mathParagraphXml,
  mathTokensOf,
  ommlFragmentsOf,
  ommlToLatex,
  ommlToMathML,
} from './math.js'
export { scanBody, type BodyElement, type BodyScan } from './scan.js'
export {
  BLANK_BULLET_NUM_ID,
  BLANK_ORDERED_NUM_ID,
  buildBlankDocx,
  type BlankDocxOptions,
  type CustomNumberingLevel,
} from './blank.js'
export {
  DEFAULT_SECTION,
  applySectionSettings,
  applyPageNumType,
  applySectionStartType,
  readPageColor,
  readSections,
  readSectionSettings,
  sectionSettingsFromXml,
} from './section.js'
export { nextNoteId, parseNotesXml, type NoteKind } from './notes.js'
export { readWatermarkText } from './watermark.js'
export {
  INK_NAME_PREFIX,
  anchoredInkRunXml,
  findInkRuns,
  injectInkRunsIntoParagraph,
  stripInkRuns,
} from './ink.js'
export { bibliographyLine, citationText, parseSourcesXml } from './sources.js'
export { readThemeColors, readThemeFonts } from './theme.js'
export { hashProtectionPassword, verifyProtectionPassword } from './protection.js'
export { decodeSymbolChar, decodeSymbolText, isSymbolFont } from './symbol-fonts.js'
export { computeListMarkers, formatNumber, type ListItemRef } from './list-markers.js'
