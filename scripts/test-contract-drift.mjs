import { chmod, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import path from 'node:path'

const generatedFile = 'packages/contracts/openapi.json'
const generatedPaths = [generatedFile, 'packages/contracts/src/generated']
const originalBytes = await readFile(generatedFile)
const originalMode = (await stat(generatedFile)).mode
const temporaryDirectory = await mkdtemp(path.join(tmpdir(), 'jobos-contract-drift-'))
const isolatedEnvironment = {
  ...process.env,
  GIT_DIR: path.join(temporaryDirectory, '.git'),
  GIT_WORK_TREE: process.cwd()
}
let result

try {
  const initialized = spawnSync('git', ['init', '--quiet', temporaryDirectory], {
    encoding: 'utf8'
  })
  if (initialized.status !== 0) {
    throw new Error(initialized.stderr.trim() || 'Unable to initialize isolated drift baseline')
  }
  const baseline = spawnSync('git', ['add', '--', ...generatedPaths], {
    encoding: 'utf8',
    env: isolatedEnvironment
  })
  if (baseline.status !== 0) {
    throw new Error(baseline.stderr.trim() || 'Unable to prepare isolated drift baseline')
  }
  await writeFile(generatedFile, Buffer.concat([originalBytes, Buffer.from('stale generated output\n')]))
  result = spawnSync(process.execPath, ['scripts/verify-contract-drift.mjs'], {
    encoding: 'utf8',
    env: isolatedEnvironment
  })
} finally {
  await writeFile(generatedFile, originalBytes)
  await chmod(generatedFile, originalMode)
  await rm(temporaryDirectory, { recursive: true })
}

const restoredBytes = await readFile(generatedFile)
const restoredMode = (await stat(generatedFile)).mode
if (!restoredBytes.equals(originalBytes) || restoredMode !== originalMode) {
  throw new Error('Contract drift negative test did not restore the generated file exactly')
}
if (result.status === 0) {
  throw new Error('Contract drift verifier accepted stale tracked generated output')
}
const output = `${result.stdout}\n${result.stderr}`
if (!output.includes(generatedFile)) {
  throw new Error('Contract drift verifier failed without identifying the stale tracked output')
}
