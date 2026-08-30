import { createHash } from 'node:crypto'
import { resolve } from 'node:path'

import { expect, test } from 'vitest'

import { resolveStylesheetImports } from './styleTestUtils'

const rendererRoot = resolve(process.cwd(), 'src/renderer')

test('resolves the complete owner stylesheet cascade in manifest order', () => {
  const { files, source } = resolveStylesheetImports(resolve(rendererRoot, 'styles.css'))

  expect(files.map(file => file.slice(rendererRoot.length + 1))).toEqual([
    'app/styles/foundation.css',
    'app/styles/app-shell.css',
    'installation-profiles/installation-profiles.css',
    'workspace/workspace.css',
    'jobs/jobs.css',
    'app/styles/shared-empty-states.css',
    'jobs/navigator/navigator-footer.css',
    'workspace/center-workspace.css',
    'browser/browser.css',
    'documents/artifacts/artifacts.css',
    'workspace/workspace-surfaces.css',
    'agents/chat/chat.css',
    'app/styles/status.css',
    'app/styles/settings.css',
    'career-profile/settings/career-profile-settings.css',
    'app/theme/theme-grid.css',
    'agents/avatar/avatar-grid.css',
    'app/theme/theme.css',
    'agents/avatar/avatar-settings.css',
    'career-profile/settings/career-profile-settings-responsive.css',
    'documents/editable/editable.css',
    'documents/previews/previews.css',
    'documents/editable/editor-overlays.css',
    'documents/docx/docx.css',
    'app/onboarding/onboarding.css',
    'app/settings/diagnostics/diagnostics.css',
    'career-profile/career-profile.css',
    'agents/agents.css'
  ])
  expect(new Set(files).size).toBe(files.length)
  expect(source).not.toMatch(/@import\b/)
  expect(createHash('sha256').update(source).digest('hex')).toBe('2dc5e6126fdd9423fb4722f3c5bb473b2cda9ce7e34c73c1721c94dc48ed8ac4')
})
