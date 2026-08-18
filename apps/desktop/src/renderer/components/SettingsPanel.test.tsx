import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { SettingsPanel } from './SettingsPanel'

afterEach(cleanup)

test('shows the selected agent avatar and reports selection through the settings interface', () => {
  const onSelectAgentAvatar = vi.fn()
  render(
    <SettingsPanel
      activeAgentAvatarId="ninja"
      activeThemeId="graphite"
      mode="dark"
      onClose={() => {}}
      onSelectAgentAvatar={onSelectAgentAvatar}
      onSelectTheme={() => {}}
    />
  )

  const ninja = screen.getByRole('radio', { name: /Ninja/ })
  expect(ninja.getAttribute('aria-checked')).toBe('true')
  expect(ninja.querySelector('[data-agent-avatar-id="ninja"]')).not.toBeNull()

  fireEvent.click(ninja)
  expect(onSelectAgentAvatar).toHaveBeenCalledWith('ninja')
})
