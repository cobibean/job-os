import { expect, test } from 'vitest'

import {
  canonicalWorkspace,
  movePanel,
  resetActivePreset,
  resizeAdjacentPanels
} from './workspaceLayout'

test('presets encode the locked dominant surfaces', () => {
  const workspace = canonicalWorkspace()

  expect(workspace.layouts.research.widths.center).toBeGreaterThan(700)
  expect(workspace.layouts.review.widths.center).toBeGreaterThan(650)
  expect(workspace.layouts['agent-focus'].widths.agent).toBeGreaterThan(600)
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
