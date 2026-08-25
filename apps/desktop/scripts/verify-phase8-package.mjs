import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const desktopRoot = path.resolve(scriptDirectory, '..')
const repositoryRoot = path.resolve(desktopRoot, '../..')
const releaseRoot = path.resolve(repositoryRoot, 'release/desktop')
const packageJson = JSON.parse(readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'))
const app = path.join(releaseRoot, 'mac-arm64/JobOS.app')
const resources = path.join(app, 'Contents/Resources')
const zip = path.join(releaseRoot, `JobOS-${packageJson.version}-arm64.zip`)
const sourceReceiptPath = path.join(
  repositoryRoot,
  'docs/acceptance/connected-agents/phase-0/codex-redistribution-candidate.json'
)
const packagedReceiptPath = path.join(
  resources,
  'codex-runtime/JOBOS_CODEX_RUNTIME_RECEIPT.json'
)

function sha256(file) {
  return createHash('sha256').update(readFileSync(file)).digest('hex')
}

function requireFile(relativePath) {
  const target = path.join(resources, relativePath)
  if (!existsSync(target) || !statSync(target).isFile()) {
    throw new Error(`Packaged JobOS resource is missing: ${relativePath}`)
  }
  return target
}

function assertNoSensitiveContent(text, label) {
  const forbidden = [
    /\/Users\/[A-Za-z0-9._-]+\//,
    /\/home\/[A-Za-z0-9._-]+\//,
    /["']?(?:access|refresh|device)[_-]?token["']?\s*[=:]\s*["'][^"']+/i,
    /["']?authorization["']?\s*:\s*["']?bearer\s+[^\s"']+/i,
  ]
  if (forbidden.some(pattern => pattern.test(text))) {
    throw new Error(`Sensitive or build-host text found in ${label}`)
  }
}

function assertNoSensitiveText(file) {
  assertNoSensitiveContent(readFileSync(file, 'utf8'), path.basename(file))
}

function scanPackagedApplication(asar) {
  const extractionRoot = mkdtempSync(path.join(tmpdir(), 'jobos-phase8-asar-'))
  const textExtensions = new Set(['.css', '.html', '.js', '.json', '.map', '.mjs', '.txt'])
  try {
    execFileSync('pnpm', ['exec', 'asar', 'extract', asar, extractionRoot], {
      cwd: repositoryRoot,
      stdio: 'ignore',
    })
    const pending = [path.join(extractionRoot, 'dist')]
    const manifest = path.join(extractionRoot, 'package.json')
    if (existsSync(manifest)) pending.push(manifest)
    while (pending.length > 0) {
      const candidate = pending.pop()
      if (!candidate || !existsSync(candidate)) continue
      if (statSync(candidate).isDirectory()) {
        pending.push(...readdirSync(candidate).map(entry => path.join(candidate, entry)))
      } else if (textExtensions.has(path.extname(candidate))) {
        assertNoSensitiveContent(
          readFileSync(candidate, 'utf8'),
          path.relative(extractionRoot, candidate)
        )
      }
    }
  } finally {
    rmSync(extractionRoot, { recursive: true, force: true })
  }
}

function main() {
  if (process.platform !== 'darwin' || process.arch !== 'arm64') {
    throw new Error('Phase 8 package verification requires arm64 macOS')
  }
  if (!existsSync(app) || !existsSync(zip)) {
    throw new Error('Phase 8 package artifacts are unavailable')
  }

  const sourceReceiptBytes = readFileSync(sourceReceiptPath)
  const packagedReceiptBytes = readFileSync(packagedReceiptPath)
  const receipt = JSON.parse(sourceReceiptBytes.toString('utf8'))
  const packagedReceipt = JSON.parse(packagedReceiptBytes.toString('utf8'))
  if (JSON.stringify(receipt) !== JSON.stringify(packagedReceipt)) {
    throw new Error('Packaged Codex runtime receipt does not match the pinned source receipt')
  }
  const appServer = requireFile(receipt.package.entrypoint.replace(/^/, 'codex-runtime/'))
  if (sha256(appServer) !== receipt.app_server_binary.sha256) {
    throw new Error('Packaged Codex App Server hash is incorrect')
  }
  for (const resource of [
    'codex-runtime/JOBOS_CODEX_RUNTIME_RECEIPT.json',
    'licenses/codex/LICENSE',
    'licenses/codex/NOTICE',
    'jobos-keychain',
    'LICENSE',
    'NOTICE',
    'THIRD_PARTY_NOTICES.md',
    'app.asar',
  ]) {
    requireFile(resource)
  }
  if (sha256(requireFile('licenses/codex/LICENSE')) !== receipt.redistribution.license.sha256) {
    throw new Error('Packaged Codex LICENSE hash is incorrect')
  }
  if (sha256(requireFile('licenses/codex/NOTICE')) !== receipt.redistribution.notice.sha256) {
    throw new Error('Packaged Codex NOTICE hash is incorrect')
  }

  execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', '--verbose=2', app], {
    stdio: 'inherit',
  })
  execFileSync('/usr/bin/codesign', ['--verify', '--strict', '--verbose=2', appServer], {
    stdio: 'inherit',
  })
  const appArchitecture = execFileSync('/usr/bin/file', [path.join(app, 'Contents/MacOS/JobOS')], {
    encoding: 'utf8',
  })
  const runtimeArchitecture = execFileSync('/usr/bin/file', [appServer], { encoding: 'utf8' })
  if (!appArchitecture.includes('arm64') || !runtimeArchitecture.includes('arm64')) {
    throw new Error('Packaged JobOS or Codex runtime is not arm64')
  }

  for (const relativePath of [
    'codex-runtime/JOBOS_CODEX_RUNTIME_RECEIPT.json',
    'licenses/codex/LICENSE',
    'licenses/codex/NOTICE',
    'LICENSE',
    'NOTICE',
    'THIRD_PARTY_NOTICES.md',
  ]) {
    assertNoSensitiveText(requireFile(relativePath))
  }
  scanPackagedApplication(requireFile('app.asar'))

  console.log(
    JSON.stringify(
      {
        schemaVersion: 1,
        status: 'passed',
        acceptance: {
          'PKG-01': 'passed',
          'PKG-04': 'passed',
          'PKG-05': 'passed_app_payload_and_exact_binary_hashes',
          'PKG-02': 'approval_gated_installed_run',
          'PKG-03': 'approval_gated_real_data_upgrade',
        },
        app: { architecture: 'arm64', signature: 'valid' },
        codex: {
          receiptId: receipt.receipt_id,
          version: receipt.candidate.version,
          sha256: receipt.app_server_binary.sha256,
          signature: 'valid',
        },
        zip: { sha256: sha256(zip), bytes: statSync(zip).size },
      },
      null,
      2
    )
  )
}

main()
