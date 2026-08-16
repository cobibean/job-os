import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const desktopRoot = path.resolve(scriptDirectory, '..')
const source = path.join('native', 'JobOSDocxAtomicReplace.swift')
const outputDirectory = path.join(desktopRoot, 'build')
const output = path.join(outputDirectory, 'jobos-docx-atomic-replace')

if (process.platform !== 'darwin') {
  console.log('Skipping macOS-only DOCX atomic-replace helper build')
  process.exit(0)
}

mkdirSync(outputDirectory, { recursive: true })
execFileSync(
  '/usr/bin/xcrun',
  [
    'swiftc',
    source,
    '-framework',
    'CryptoKit',
    '-debug-prefix-map',
    `${desktopRoot}=/jobos/apps/desktop`,
    '-file-prefix-map',
    `${desktopRoot}=/jobos/apps/desktop`,
    '-file-compilation-dir',
    '/jobos',
    '-o',
    output
  ],
  { cwd: desktopRoot, stdio: 'inherit' }
)

const helperBytes = readFileSync(output)
for (const privatePath of [desktopRoot, homedir()]) {
  if (privatePath && helperBytes.includes(Buffer.from(privatePath))) {
    throw new Error(`DOCX atomic-replace helper contains a private build path`)
  }
}
