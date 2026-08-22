import path from 'node:path'

const ACCEPTANCE_MODE = 'career-profile-native-flow-v1'
const ACCEPTANCE_DIRECTORY = 'jobos-career-profile-native'

export interface CareerProfileAcceptanceDialogPaths {
  chooseArchivePath: () => Promise<string | null>
  chooseExportPath: () => Promise<string | null>
}

export function careerProfileAcceptanceDialogPaths(
  environment: NodeJS.ProcessEnv = process.env
): CareerProfileAcceptanceDialogPaths | null {
  if (environment.JOBOS_CAREER_PROFILE_ACCEPTANCE_MODE === undefined) return null
  if (environment.JOBOS_CAREER_PROFILE_ACCEPTANCE_MODE !== ACCEPTANCE_MODE) {
    throw new Error('Invalid Career Profile acceptance dialog mode')
  }
  const temporaryRoot = environment.TMPDIR
  const configuredRoot = environment.JOBOS_CAREER_PROFILE_ACCEPTANCE_ROOT
  const restorePath = environment.JOBOS_CAREER_PROFILE_ACCEPTANCE_RESTORE_PATH
  const exportJson = environment.JOBOS_CAREER_PROFILE_ACCEPTANCE_EXPORT_PATHS
  if (!temporaryRoot || !configuredRoot || !restorePath || !exportJson) {
    throw new Error('Incomplete Career Profile acceptance dialog configuration')
  }
  const expectedRoot = path.resolve(temporaryRoot, ACCEPTANCE_DIRECTORY)
  if (!path.isAbsolute(configuredRoot) || path.resolve(configuredRoot) !== expectedRoot) {
    throw new Error('Career Profile acceptance paths must use the disposable TMPDIR root')
  }
  let exports: unknown
  try { exports = JSON.parse(exportJson) } catch { throw new Error('Invalid Career Profile acceptance export paths') }
  if (!Array.isArray(exports) || exports.length !== 4 || exports.some(value => typeof value !== 'string')) {
    throw new Error('Career Profile acceptance requires exactly four export paths')
  }
  const validate = (candidate: string): string => {
    const resolved = path.resolve(candidate)
    if (path.dirname(resolved) !== expectedRoot || path.extname(resolved).toLowerCase() !== '.zip') {
      throw new Error('Career Profile acceptance archive path escaped the disposable root')
    }
    return resolved
  }
  const exportQueue = exports.map(validate)
  const selectedRestorePath = validate(restorePath)
  return {
    chooseArchivePath: async () => selectedRestorePath,
    chooseExportPath: async () => exportQueue.shift() ?? (() => { throw new Error('Career Profile acceptance export path queue exhausted') })()
  }
}
