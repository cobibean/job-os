import { readFile } from 'node:fs/promises'

const [html, docxWorkerHtml] = await Promise.all([
  readFile('apps/desktop/dist/renderer/index.html', 'utf8'),
  readFile('apps/desktop/dist/renderer/docx-worker.html', 'utf8')
])

if (!html.includes('src="./assets/') || !html.includes('href="./assets/')) {
  throw new Error('Packaged renderer assets must use file-safe relative URLs')
}
if (!docxWorkerHtml.includes('src="./assets/')) {
  throw new Error('Packaged DOCX worker asset must use a file-safe relative URL')
}

const requiredCspDirectives = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'"
]

for (const [name, rendererHtml] of [
  ['renderer', html],
  ['DOCX worker', docxWorkerHtml]
]) {
  for (const directive of requiredCspDirectives) {
    if (!rendererHtml.includes(directive)) {
      throw new Error(`Packaged ${name} CSP is missing: ${directive}`)
    }
  }
}
