import { spawnSync } from 'node:child_process'

const generatedPaths = [
  'packages/contracts/openapi.json',
  'packages/contracts/src/generated'
]
const result = spawnSync(
  'git',
  ['status', '--porcelain', '--untracked-files=all', '--', ...generatedPaths],
  { encoding: 'utf8' }
)

if (result.status !== 0) {
  throw new Error(result.stderr.trim() || 'Unable to inspect generated contract drift')
}

if (result.stdout.trim()) {
  throw new Error(`Generated contracts are not current:\n${result.stdout.trim()}`)
}
