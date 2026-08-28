import { access, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const mainOutput = path.join(desktopRoot, 'dist', 'main')
await access(path.join(mainOutput, 'main.js'))

const legacyProductionFiles = [
  'agent.js', 'agentIpc.js', 'apiLifecycle.js', 'browser.js', 'browserIpc.js',
  'browserJobExtraction.js', 'capabilityClient.js', 'careerProfile.js',
  'careerProfileAcceptanceDialogs.js', 'careerProfileArchiveWriter.js',
  'connectedAgents.js', 'connectedAgentsIpc.js', 'connectivity.js',
  'credentialStore.js', 'desktopRuntime.js', 'documents.js', 'DocxWorkerManager.js',
  'docxDocuments.js', 'docxDocumentsIpc.js', 'docxFileStore.js', 'docxFileWatcher.js',
  'editableDocuments.js', 'editableDocumentsIpc.js', 'installationProfiles.js',
  'jobs.js', 'localDocxBindingStore.js', 'mainWindowLifecycle.js', 'mediaCapture.js',
  'mediaCaptureSpec.js', 'profileStorage.js', 'runtimeConfig.js', 'security.js', 'workspace.js'
]
const entries = new Set(await readdir(mainOutput))
const stale = legacyProductionFiles.filter(file => entries.has(file))
if (stale.length) throw new Error(`Stale Electron main output: ${stale.join(', ')}`)
for (const legacyDirectory of ['document-export', 'document-import']) {
  if (entries.has(legacyDirectory)) throw new Error(`Stale Electron main output: ${legacyDirectory}`)
}
