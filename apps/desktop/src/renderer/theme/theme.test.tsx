import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test } from 'vitest'

import { DEFAULT_THEME_ID, getTheme, THEMES } from './themes'
import { applyTheme, useTheme } from './useTheme'
import { SettingsPanel } from '../components/SettingsPanel'

function ThemeHarness() {
  const theme = useTheme()
  return (
    <div>
      <span data-testid="active-theme">{theme.themeId}</span>
      <span data-testid="active-mode">{theme.mode}</span>
      <button onClick={theme.toggleMode} type="button">toggle mode</button>
      <SettingsPanel
        activeAgentAvatarId="ninja"
        activeThemeId={theme.themeId}
        mode={theme.mode}
        onClose={() => {}}
        onSelectAgentAvatar={() => {}}
        onSelectTheme={theme.selectTheme}
      />
    </div>
  )
}

beforeEach(() => {
  const backing = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => backing.get(key) ?? null,
      setItem: (key: string, value: string) => { backing.set(key, String(value)) },
      removeItem: (key: string) => { backing.delete(key) },
      clear: () => { backing.clear() }
    }
  })
})

afterEach(() => {
  cleanup()
  document.documentElement.removeAttribute('style')
  delete document.documentElement.dataset.theme
  delete document.documentElement.dataset.themeMode
})

test('every theme defines the full token set in both modes', () => {
  const reference = Object.keys(getTheme(DEFAULT_THEME_ID).modes.dark).sort()
  expect(reference.length).toBeGreaterThan(30)
  for (const theme of THEMES) {
    for (const mode of ['light', 'dark'] as const) {
      const tokens = theme.modes[mode]
      expect(Object.keys(tokens).sort(), `${theme.id}/${mode}`).toEqual(reference)
      for (const [name, value] of Object.entries(tokens)) {
        expect(value, `${theme.id}/${mode}/${name}`).toMatch(/^#[0-9a-f]{6}([0-9a-f]{2})?$/)
      }
    }
  }
})

test('applyTheme writes tokens onto the document root', () => {
  applyTheme('midnight', 'dark')
  const root = document.documentElement
  expect(root.dataset.theme).toBe('midnight')
  expect(root.dataset.themeMode).toBe('dark')
  expect(root.style.getPropertyValue('--bg')).toBe(getTheme('midnight').modes.dark.bg)
  expect(root.style.colorScheme).toBe('dark')
})

test('selecting a theme and toggling mode both persist for the next launch', () => {
  render(<ThemeHarness />)

  expect(screen.getByTestId('active-theme').textContent).toBe(DEFAULT_THEME_ID)
  expect(screen.getByTestId('active-mode').textContent).toBe('dark')

  fireEvent.click(screen.getByRole('radio', { name: /Forest/ }))
  fireEvent.click(screen.getByRole('button', { name: 'toggle mode' }))

  expect(screen.getByTestId('active-theme').textContent).toBe('forest')
  expect(screen.getByTestId('active-mode').textContent).toBe('light')
  expect(window.localStorage.getItem('jobos.theme')).toBe('forest')
  expect(window.localStorage.getItem('jobos.themeMode')).toBe('light')
  expect(document.documentElement.style.getPropertyValue('--bg')).toBe(getTheme('forest').modes.light.bg)
})

test('an unknown stored theme falls back to the default', () => {
  window.localStorage.setItem('jobos.theme', 'does-not-exist')
  render(<ThemeHarness />)
  expect(screen.getByTestId('active-theme').textContent).toBe(DEFAULT_THEME_ID)
})
