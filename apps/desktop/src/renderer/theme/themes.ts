/**
 * JobOS theme registry.
 *
 * Each theme authors a small "core" palette per mode (light/dark); the rest of
 * the design-system tokens (tints, borders, hover shades for accent/green/
 * danger/warn) are derived with color math so every theme stays internally
 * consistent. Adding a new theme = adding one entry to THEMES below.
 *
 * Token names must stay in sync with the `:root` block in styles.css.
 */

export type ThemeMode = 'light' | 'dark'

export interface ThemeCore {
  bg: string
  surface: string
  surfaceInset: string
  surfaceRaised: string
  surfaceHover: string
  surfaceSelected: string
  border: string
  borderSoft: string
  borderStrong: string
  text: string
  textSoft: string
  muted: string
  quiet: string
  accent: string
  onAccent: string
  green: string
  danger: string
  warn: string
  pageBackdrop: string
}

export type ThemeTokens = Record<string, string>

export interface ThemeDefinition {
  id: string
  label: string
  description: string
  modes: Record<ThemeMode, ThemeTokens>
}

function clamp(value: number): number {
  return Math.min(255, Math.max(0, Math.round(value)))
}

function mix(colorA: string, colorB: string, weightOfB: number): string {
  const a = colorA.replace('#', '')
  const b = colorB.replace('#', '')
  const channels = [0, 2, 4].map(index => {
    const channelA = parseInt(a.slice(index, index + 2), 16)
    const channelB = parseInt(b.slice(index, index + 2), 16)
    return clamp(channelA * (1 - weightOfB) + channelB * weightOfB)
  })
  return `#${channels.map(channel => channel.toString(16).padStart(2, '0')).join('')}`
}

function statusTokens(name: string, color: string, core: ThemeCore, mode: ThemeMode): ThemeTokens {
  const dark = mode === 'dark'
  return {
    [name]: color,
    [`${name}-text`]: mix(color, core.text, dark ? 0.42 : 0.35),
    [`${name}-border`]: mix(core.surface, color, dark ? 0.42 : 0.5),
    [`${name}-tint`]: mix(core.surface, color, dark ? 0.1 : 0.09),
    [`${name}-tint-strong`]: mix(core.surface, color, dark ? 0.24 : 0.18)
  }
}

export function buildTokens(core: ThemeCore, mode: ThemeMode, overrides: ThemeTokens = {}): ThemeTokens {
  const dark = mode === 'dark'
  const black = '#000000'
  const white = '#ffffff'
  return {
    bg: core.bg,
    surface: core.surface,
    'surface-inset': core.surfaceInset,
    'surface-raised': core.surfaceRaised,
    'surface-hover': core.surfaceHover,
    'surface-selected': core.surfaceSelected,
    border: core.border,
    'border-soft': core.borderSoft,
    'border-strong': core.borderStrong,
    text: core.text,
    'text-soft': core.textSoft,
    muted: core.muted,
    quiet: core.quiet,
    accent: core.accent,
    'on-accent': core.onAccent,
    'accent-text': mix(core.accent, core.text, dark ? 0.55 : 0.3),
    'accent-border': mix(core.surface, core.accent, dark ? 0.38 : 0.45),
    'accent-border-strong': mix(core.surface, core.accent, dark ? 0.62 : 0.72),
    'accent-tint': mix(core.surface, core.accent, dark ? 0.08 : 0.08),
    'accent-tint-strong': mix(core.surface, core.accent, dark ? 0.2 : 0.16),
    'accent-solid': dark ? mix(core.accent, black, 0.28) : core.accent,
    'accent-solid-deep': dark ? mix(core.accent, black, 0.46) : mix(core.accent, black, 0.14),
    'accent-solid-hover': dark ? mix(core.accent, black, 0.16) : mix(core.accent, white, 0.1),
    'accent-solid-deep-hover': dark ? mix(core.accent, black, 0.38) : mix(core.accent, black, 0.06),
    ...statusTokens('green', core.green, core, mode),
    ...statusTokens('danger', core.danger, core, mode),
    ...statusTokens('warn', core.warn, core, mode),
    'shadow-color': dark ? '#00000088' : '#00000026',
    'shadow-color-soft': dark ? '#00000055' : '#00000015',
    'highlight-inset': dark ? '#ffffff16' : '#ffffff80',
    'page-backdrop': core.pageBackdrop,
    ...overrides
  }
}

/** Exact hexes JobOS shipped with, so the default dark look stays pixel-identical. */
const JOBOS_DARK_OVERRIDES: ThemeTokens = {
  'accent-text': '#cfc5e3',
  'accent-border': '#51436f',
  'accent-border-strong': '#7058a6',
  'accent-tint': '#191721',
  'accent-tint-strong': '#2c2340',
  'accent-solid': '#654a9d',
  'accent-solid-deep': '#4e387d',
  'accent-solid-hover': '#7458ad',
  'accent-solid-deep-hover': '#59408b',
  'green-text': '#a9dfbd',
  'green-border': '#4f8768',
  'green-tint': '#14231b',
  'green-tint-strong': '#234332',
  'danger-text': '#e3b8b8',
  'danger-border': '#674447',
  'danger-tint': '#251719',
  'danger-tint-strong': '#492d30',
  'warn-text': '#c0a982',
  'warn-border': '#514630',
  'warn-tint': '#1a1814'
}

const jobosDark: ThemeCore = {
  bg: '#0f1114',
  surface: '#12151a',
  surfaceInset: '#101318',
  surfaceRaised: '#171a20',
  surfaceHover: '#1b1e24',
  surfaceSelected: '#242731',
  border: '#2b2e34',
  borderSoft: '#22252a',
  borderStrong: '#3a3d45',
  text: '#f1efe9',
  textSoft: '#d5d3ce',
  muted: '#a0a4ad',
  quiet: '#747985',
  accent: '#9b72ff',
  onAccent: '#f4efff',
  green: '#79c99b',
  danger: '#d38b8b',
  warn: '#c9aa72',
  pageBackdrop: '#f6f6f6'
}

const jobosLight: ThemeCore = {
  bg: '#f2f0eb',
  surface: '#faf9f6',
  surfaceInset: '#efede7',
  surfaceRaised: '#ffffff',
  surfaceHover: '#eceae4',
  surfaceSelected: '#e6e2f5',
  border: '#d8d5cd',
  borderSoft: '#e2dfd8',
  borderStrong: '#c4c1b8',
  text: '#22212a',
  textSoft: '#3d3b47',
  muted: '#6d6a78',
  quiet: '#94919e',
  accent: '#6d47d9',
  onAccent: '#ffffff',
  green: '#2e7d54',
  danger: '#b04a4a',
  warn: '#96702c',
  pageBackdrop: '#f6f6f6'
}

const midnightDark: ThemeCore = {
  bg: '#0a0f1e',
  surface: '#0e1426',
  surfaceInset: '#0b1122',
  surfaceRaised: '#131a30',
  surfaceHover: '#182038',
  surfaceSelected: '#1e2946',
  border: '#242e4a',
  borderSoft: '#1c2540',
  borderStrong: '#324066',
  text: '#e8ecf6',
  textSoft: '#c9d1e4',
  muted: '#93a0c0',
  quiet: '#68749a',
  accent: '#5b8cff',
  onAccent: '#f0f5ff',
  green: '#5fc4a2',
  danger: '#e08a94',
  warn: '#d0ab66',
  pageBackdrop: '#f4f6fb'
}

const midnightLight: ThemeCore = {
  bg: '#eef1f8',
  surface: '#f9fafd',
  surfaceInset: '#e9edf6',
  surfaceRaised: '#ffffff',
  surfaceHover: '#e4e9f4',
  surfaceSelected: '#dde5f8',
  border: '#ccd4e6',
  borderSoft: '#dde2ef',
  borderStrong: '#b3bed8',
  text: '#1a2236',
  textSoft: '#33405c',
  muted: '#5f6b8a',
  quiet: '#8b94ad',
  accent: '#2f5fd0',
  onAccent: '#ffffff',
  green: '#1f7a5c',
  danger: '#b04553',
  warn: '#8f6c22',
  pageBackdrop: '#f4f6fb'
}

const paperDark: ThemeCore = {
  bg: '#191613',
  surface: '#1e1b17',
  surfaceInset: '#1b1814',
  surfaceRaised: '#25211c',
  surfaceHover: '#2a251f',
  surfaceSelected: '#332d25',
  border: '#38322a',
  borderSoft: '#2d2822',
  borderStrong: '#4a4238',
  text: '#efe8dc',
  textSoft: '#d9d1c2',
  muted: '#a89e8d',
  quiet: '#7d7466',
  accent: '#d8965a',
  onAccent: '#241a10',
  green: '#95bd8a',
  danger: '#d09083',
  warn: '#c9aa72',
  pageBackdrop: '#f8f4ec'
}

const paperLight: ThemeCore = {
  bg: '#f5efe3',
  surface: '#fbf7ee',
  surfaceInset: '#f1eadb',
  surfaceRaised: '#fffdf8',
  surfaceHover: '#efe7d6',
  surfaceSelected: '#eadfc8',
  border: '#ddd2bc',
  borderSoft: '#e7ddca',
  borderStrong: '#c9bda3',
  text: '#2b241a',
  textSoft: '#463d31',
  muted: '#77694f',
  quiet: '#9c8f76',
  accent: '#a05e21',
  onAccent: '#fffaf2',
  green: '#4b7a3f',
  danger: '#a8503f',
  warn: '#8f6c22',
  pageBackdrop: '#f8f4ec'
}

const forestDark: ThemeCore = {
  bg: '#0d1410',
  surface: '#111a15',
  surfaceInset: '#0f1712',
  surfaceRaised: '#16211a',
  surfaceHover: '#1b2820',
  surfaceSelected: '#22332a',
  border: '#28382f',
  borderSoft: '#1f2d25',
  borderStrong: '#35493d',
  text: '#e9f0e9',
  textSoft: '#ccd8cc',
  muted: '#94a898',
  quiet: '#68796c',
  accent: '#7fbf8e',
  onAccent: '#0c1a10',
  green: '#8ecf9c',
  danger: '#d68f85',
  warn: '#c9ac6b',
  pageBackdrop: '#f2f6f2'
}

const forestLight: ThemeCore = {
  bg: '#eef2ec',
  surface: '#f8faf6',
  surfaceInset: '#e8eee6',
  surfaceRaised: '#ffffff',
  surfaceHover: '#e3ebe1',
  surfaceSelected: '#dce8da',
  border: '#ccd8ca',
  borderSoft: '#dbe4d9',
  borderStrong: '#b2c2b0',
  text: '#1d271f',
  textSoft: '#364439',
  muted: '#5f7163',
  quiet: '#8a998d',
  accent: '#2f7d4a',
  onAccent: '#ffffff',
  green: '#2c7a45',
  danger: '#ac4d44',
  warn: '#8c6c26',
  pageBackdrop: '#f2f6f2'
}

const monoDark: ThemeCore = {
  bg: '#0e0e0f',
  surface: '#131314',
  surfaceInset: '#101011',
  surfaceRaised: '#19191b',
  surfaceHover: '#1e1e20',
  surfaceSelected: '#28282b',
  border: '#2c2c2f',
  borderSoft: '#232326',
  borderStrong: '#3c3c40',
  text: '#f2f2f0',
  textSoft: '#d6d6d3',
  muted: '#a3a3a0',
  quiet: '#757572',
  accent: '#e6e6e2',
  onAccent: '#111112',
  green: '#a9c4ab',
  danger: '#c9a0a0',
  warn: '#c2b28e',
  pageBackdrop: '#f5f5f4'
}

const monoLight: ThemeCore = {
  bg: '#f1f1ef',
  surface: '#fafaf8',
  surfaceInset: '#ebebe8',
  surfaceRaised: '#ffffff',
  surfaceHover: '#e6e6e3',
  surfaceSelected: '#dededa',
  border: '#d3d3cf',
  borderSoft: '#e0e0dc',
  borderStrong: '#bcbcb7',
  text: '#1c1c1a',
  textSoft: '#383835',
  muted: '#6b6b67',
  quiet: '#939390',
  accent: '#2a2a28',
  onAccent: '#fafaf8',
  green: '#3d6b47',
  danger: '#9c4a44',
  warn: '#82683a',
  pageBackdrop: '#f5f5f4'
}

export const THEMES: ThemeDefinition[] = [
  {
    id: 'jobos',
    label: 'JobOS',
    description: 'The original graphite look with a violet accent.',
    modes: {
      dark: buildTokens(jobosDark, 'dark', JOBOS_DARK_OVERRIDES),
      light: buildTokens(jobosLight, 'light')
    }
  },
  {
    id: 'midnight',
    label: 'Midnight',
    description: 'Deep navy surfaces with a cobalt accent.',
    modes: {
      dark: buildTokens(midnightDark, 'dark'),
      light: buildTokens(midnightLight, 'light')
    }
  },
  {
    id: 'paper',
    label: 'Paper',
    description: 'Warm parchment tones with an amber accent.',
    modes: {
      dark: buildTokens(paperDark, 'dark'),
      light: buildTokens(paperLight, 'light')
    }
  },
  {
    id: 'forest',
    label: 'Forest',
    description: 'Evergreen surfaces with a sage accent.',
    modes: {
      dark: buildTokens(forestDark, 'dark'),
      light: buildTokens(forestLight, 'light')
    }
  },
  {
    id: 'mono',
    label: 'Mono',
    description: 'Minimal grayscale with almost no color.',
    modes: {
      dark: buildTokens(monoDark, 'dark'),
      light: buildTokens(monoLight, 'light')
    }
  }
]

export const DEFAULT_THEME_ID = 'jobos'
export const DEFAULT_THEME_MODE: ThemeMode = 'dark'

export function getTheme(themeId: string): ThemeDefinition {
  return THEMES.find(theme => theme.id === themeId) ?? THEMES[0]!
}
