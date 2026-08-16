import { spawnSync } from 'node:child_process'

const generatedPaths = [
  'packages/contracts/openapi.json',
  'packages/contracts/src/generated'
]

function runGit(arguments_) {
  const result = spawnSync('git', arguments_, { encoding: 'utf8' })
  if (result.status !== 0) {
    throw new Error(result.stderr.trim() || 'Unable to inspect generated contract drift')
  }
  return result.stdout.trim()
}

const modified = runGit(['diff', '--name-only', '--', ...generatedPaths])
const untracked = runGit([
  'ls-files', '--others', '--exclude-standard', '--', ...generatedPaths
])
const drift = [modified, untracked].filter(Boolean).join('\n')

if (drift) {
  throw new Error(`Generated contracts are not current:\n${drift}`)
}
