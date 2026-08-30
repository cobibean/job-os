import { expect, test } from 'vitest'

import {
  browserRepairMessage,
  canonicalWorkspace,
  movePanel,
  resetActivePreset,
  resizeAdjacentPanels
} from './workspaceLayout'

test.each([
  [['protected_title'], 'Credential-like title metadata was protected. No browser tabs were lost.'],
  [['dropped_tabs'], 'Browser metadata was repaired: invalid saved tabs were skipped.'],
  [['reselected_active_tab'], 'Browser metadata was repaired: a recoverable active tab was selected.'],
  [['protected_title', 'dropped_tabs', 'reselected_active_tab'], 'Browser metadata was repaired: credential-like title metadata was protected; invalid saved tabs were skipped; a recoverable active tab was selected.']
] as const)('describes browser repair reasons accurately', (reasons, expected) => {
  expect(browserRepairMessage([...reasons], true)).toBe(expected)
})

test('presets encode the locked dominant surfaces', () => {
  const workspace = canonicalWorkspace()

  expect(workspace.layouts.research.widths.center).toBeGreaterThan(700)
  expect(workspace.layouts.review.widths.center).toBeGreaterThan(650)
  expect(workspace.layouts['agent-focus'].widths.agent).toBeGreaterThan(600)
  expect(workspace.layouts['agent-focus'].order).toEqual(['jobs', 'agent', 'center'])
})

test('resizing is continuous data with usable panel minimums', () => {
  const workspace = canonicalWorkspace()
  const resized = resizeAdjacentPanels(workspace, 'jobs', 'center', -500)

  expect(resized.layouts.review.widths.jobs).toBe(220)
  expect(resized.layouts.review.widths.center).toBe(760)
})

test('reorder and reset affect presentation for the active preset only', () => {
  const workspace = movePanel(canonicalWorkspace(), 'agent', 0)
  const researchBefore = workspace.layouts.research
  const reset = resetActivePreset(workspace)

  expect(workspace.layouts.review.order).toEqual(['agent', 'jobs', 'center'])
  expect(reset.selectedPreset).toBe('review')
  expect(reset.layouts.review.order).toEqual(['jobs', 'center', 'agent'])
  expect(reset.layouts.research).toEqual(researchBefore)
})

test('reset while Browse is active preserves Browse and workbench continuity', () => {
  const workspace = canonicalWorkspace()
  workspace.activeTopLevelWorkspace = 'browse'
  workspace.browseMode = 'swipe'
  workspace.browseFocusJobId = 'job-7'
  workspace.browseQuery = 'platform'
  workspace.browseStatusGroup = 'Considering'
  workspace.browseSortMode = 'recent'
  workspace.layouts.review.widths.jobs = 340
  workspace.layouts.research.order = ['agent', 'jobs', 'center']
  const before = JSON.stringify(workspace)

  const reset = resetActivePreset(workspace)

  expect(JSON.stringify(reset)).toBe(before)
  expect(reset).toBe(workspace)
})
