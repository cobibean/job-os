import { execFileSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const desktopRoot = path.resolve(scriptDirectory, '..')
const source = path.join(desktopRoot, 'native', 'JobOSDocxAtomicReplace.swift')
const outputDirectory = path.join(desktopRoot, 'build')
const output = path.join(outputDirectory, 'jobos-docx-atomic-replace')

if (process.platform !== 'darwin') {
  console.log('Skipping macOS-only DOCX atomic-replace helper build')
  process.exit(0)
}

mkdirSync(outputDirectory, { recursive: true })
execFileSync(
  '/usr/bin/xcrun',
  ['swiftc', source, '-framework', 'CryptoKit', '-o', output],
  { stdio: 'inherit' }
)
