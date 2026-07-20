import { readFile } from 'node:fs/promises'

const html = await readFile('apps/desktop/dist/renderer/index.html', 'utf8')

if (!html.includes('src="./assets/') || !html.includes('href="./assets/')) {
  throw new Error('Packaged renderer assets must use file-safe relative URLs')
}
