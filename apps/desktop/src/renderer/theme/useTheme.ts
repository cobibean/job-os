import { useCallback, useEffect, useState } from 'react'

import {
  DEFAULT_THEME_ID,
  DEFAULT_THEME_MODE,
  getTheme,
  THEMES,
  type ThemeMode
} from './themes'

const THEME_STORAGE_KEY = 'jobos.theme'
const MODE_STORAGE_KEY = 'jobos.themeMode'

export function applyTheme(themeId: string, mode: ThemeMode): void {
  const tokens = getTheme(themeId).modes[mode]
  const root = document.documentElement
  for (const [name, value] of Object.entries(tokens)) {
    root.style.setProperty(`--${name}`, value)
  }
  root.dataset.theme = themeId
  root.dataset.themeMode = mode
  root.style.colorScheme = mode
}

function readStoredTheme(): string {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (stored && THEMES.some(theme => theme.id === stored)) return stored
  } catch {
    // storage unavailable; fall through to default
  }
  return DEFAULT_THEME_ID
}

function readStoredMode(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(MODE_STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // storage unavailable; fall through to default
  }
  return DEFAULT_THEME_MODE
}

function store(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // storage unavailable; the selection still applies for this session
  }
}

export interface ThemeState {
  themeId: string
  mode: ThemeMode
  selectTheme: (themeId: string) => void
  toggleMode: () => void
}

export function useTheme(): ThemeState {
  const [themeId, setThemeId] = useState(readStoredTheme)
  const [mode, setMode] = useState(readStoredMode)

  useEffect(() => {
    applyTheme(themeId, mode)
  }, [themeId, mode])

  const selectTheme = useCallback((nextId: string) => {
    setThemeId(nextId)
    store(THEME_STORAGE_KEY, nextId)
  }, [])

  const toggleMode = useCallback(() => {
    setMode(current => {
      const next: ThemeMode = current === 'dark' ? 'light' : 'dark'
      store(MODE_STORAGE_KEY, next)
      return next
    })
  }, [])

  return { themeId, mode, selectTheme, toggleMode }
}
