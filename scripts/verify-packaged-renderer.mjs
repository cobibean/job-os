import { readFile } from 'node:fs/promises'

const html = await readFile('apps/desktop/dist/renderer/index.html', 'utf8')

if (!html.includes('src="./assets/') || !html.includes('href="./assets/')) {
  throw new Error('Packaged renderer assets must use file-safe relative URLs')
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

for (const directive of requiredCspDirectives) {
  if (!html.includes(directive)) {
    throw new Error(`Packaged renderer CSP is missing: ${directive}`)
  }
}
