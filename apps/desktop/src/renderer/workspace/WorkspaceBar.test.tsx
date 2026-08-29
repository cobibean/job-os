import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { WorkspaceBar } from './WorkspaceBar'

afterEach(cleanup)

function renderBar(careerProfileEnabled: boolean) {
  const onWorkspaceChange = vi.fn()
  render(
    <WorkspaceBar
      activeWorkspace="research"
      careerProfileEnabled={careerProfileEnabled}
      onReset={vi.fn()}
      onToggleMode={vi.fn()}
      onWorkspaceChange={onWorkspaceChange}
      themeMode="dark"
    />
  )
  return onWorkspaceChange
}

test('keeps Career Profile out of live navigation while the API slice is dormant', () => {
  renderBar(false)
  expect(screen.queryByRole('button', { name: 'Career Profile' })).toBeNull()
})

test('reveals and opens Career Profile only when staging availability is enabled', () => {
  const onWorkspaceChange = renderBar(true)
  fireEvent.click(screen.getByRole('button', { name: 'Career Profile' }))
  expect(onWorkspaceChange).toHaveBeenCalledWith('career-profile')
})
