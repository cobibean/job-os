import { createHash } from 'node:crypto'
import { createReadStream, createWriteStream, existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { pipeline } from 'node:stream/promises'
import { Readable } from 'node:stream'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const desktopRoot = path.resolve(scriptDirectory, '..')
const repositoryRoot = path.resolve(desktopRoot, '../..')
const receiptPath = path.join(
  repositoryRoot,
  'docs/acceptance/connected-agents/phase-0/codex-redistribution-candidate.json'
)
const receipt = JSON.parse(readFileSync(receiptPath, 'utf8'))
const output = path.join(desktopRoot, 'build/codex-runtime')
const archiveCache = path.join(desktopRoot, 'build/codex-runtime-download.tar.gz')

function sha256(file) {
  const hash = createHash('sha256')
  return new Promise((resolve, reject) => {
    const stream = createReadStream(file)
    stream.on('data', chunk => hash.update(chunk))
    stream.on('end', () => resolve(hash.digest('hex')))
    stream.on('error', reject)
  })
}

async function ensureArchive() {
  if (existsSync(archiveCache) && await sha256(archiveCache) === receipt.package.sha256) return
  rmSync(archiveCache, { force: true })
  const response = await fetch(receipt.package.asset_url, { redirect: 'follow' })
  if (!response.ok || !response.body) {
    throw new Error(`Pinned Codex runtime download failed (${response.status})`)
  }
  mkdirSync(path.dirname(archiveCache), { recursive: true })
  await pipeline(
    Readable.fromWeb(response.body),
    createWriteStream(archiveCache, { mode: 0o600 })
  )
  if (await sha256(archiveCache) !== receipt.package.sha256) {
    rmSync(archiveCache, { force: true })
    throw new Error('Pinned Codex runtime archive failed integrity verification')
  }
}

async function main() {
  if (process.platform !== 'darwin' || process.arch !== 'arm64') {
    console.log('Skipping arm64 macOS Codex runtime preparation')
    return
  }
  await ensureArchive()
  const temporary = mkdtempSync(path.join(tmpdir(), 'jobos-codex-runtime-'))
  try {
    execFileSync('/usr/bin/tar', ['-xzf', archiveCache, '-C', temporary], { stdio: 'inherit' })
    const expectedMembers = new Set(receipt.package.members.map(item => item.path))
    for (const member of receipt.package.members) {
      const target = path.resolve(temporary, member.path)
      if (!target.startsWith(`${path.resolve(temporary)}${path.sep}`)) {
        throw new Error('Pinned Codex runtime archive contains an unsafe path')
      }
      const metadata = statSync(target)
      if (!metadata.isFile() || metadata.size !== member.size) {
        throw new Error(`Pinned Codex runtime member mismatch: ${member.path}`)
      }
      expectedMembers.delete(member.path)
    }
    if (expectedMembers.size !== 0) throw new Error('Pinned Codex runtime receipt is incomplete')
    const binary = path.join(temporary, receipt.package.entrypoint)
    if (await sha256(binary) !== receipt.app_server_binary.sha256) {
      throw new Error('Pinned Codex App Server binary failed integrity verification')
    }
    execFileSync('/usr/bin/codesign', ['--verify', '--strict', '--verbose=2', binary], {
      stdio: 'inherit'
    })
    writeFileSync(
      path.join(temporary, 'JOBOS_CODEX_RUNTIME_RECEIPT.json'),
      `${JSON.stringify(receipt, null, 2)}\n`,
      { mode: 0o644 }
    )
    rmSync(output, { recursive: true, force: true })
    renameSync(temporary, output)
    console.log(`Prepared ${receipt.receipt_id}`)
  } finally {
    rmSync(temporary, { recursive: true, force: true })
  }
}

await main()
