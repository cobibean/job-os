// This file is part of JobOS's modified GenOffice-derived package; see this package's UPSTREAM.md.
import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const forbidden = [
  '/ee/',
  '@genoffice/ai-',
  '@genoffice/agent-core',
  '@genoffice/project-store',
  'electron-updater',
  'genspark',
]

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const absolute = path.join(directory, entry.name)
    if (entry.isDirectory()) files.push(...await sourceFiles(absolute))
    else if (/\.(?:ts|tsx|js|mjs|md|json)$/u.test(entry.name)) files.push(absolute)
  }
  return files
}

const scanRoots = [path.join(root, 'src')]
for (const directory of scanRoots) {
  for (const file of await sourceFiles(directory)) {
    const relative = path.relative(root, file).split(path.sep).join('/')
    const text = await readFile(file, 'utf8')
    for (const token of forbidden) {
      if (`/${relative}`.toLowerCase().includes(token) || text.toLowerCase().includes(token)) {
        throw new Error(`Forbidden GenOffice boundary token ${token} in ${relative}`)
      }
    }
  }
}

console.log('DOCX engine source boundary verified')
