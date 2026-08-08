import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src')
const forbidden = ['/ee/', '@genoffice/', 'agent-core', 'ai-provider', 'ai-search', 'project-store', 'electron-updater', 'genspark']

async function files(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const found = []
  for (const entry of entries) {
    const target = path.join(directory, entry.name)
    if (entry.isDirectory()) found.push(...await files(target))
    else if (/\.(?:ts|tsx|js|mjs)$/.test(entry.name)) found.push(target)
  }
  return found
}

const violations = []
for (const file of await files(sourceRoot)) {
  const relative = path.relative(sourceRoot, file).split(path.sep).join('/')
  const text = await readFile(file, 'utf8')
  for (const token of forbidden) if (text.toLowerCase().includes(token)) violations.push(`${relative}: ${token}`)
}
if (violations.length) {
  console.error(violations.join('\n'))
  process.exit(1)
}
console.log('docx editor source boundary verified')
