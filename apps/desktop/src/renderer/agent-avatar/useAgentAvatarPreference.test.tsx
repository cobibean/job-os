import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, test } from 'vitest'

import { DEFAULT_AGENT_AVATAR_ID } from './agentAvatars'
import { useAgentAvatarPreference } from './useAgentAvatarPreference'

beforeEach(() => window.localStorage.clear())
afterEach(cleanup)

test('falls back to the default for an unknown stored avatar', () => {
  window.localStorage.setItem('jobos.agentAvatar', 'missing-avatar')

  const { result } = renderHook(() => useAgentAvatarPreference())

  expect(result.current.avatarId).toBe(DEFAULT_AGENT_AVATAR_ID)
})

test('persists a valid avatar selection', () => {
  const { result } = renderHook(() => useAgentAvatarPreference())

  act(() => result.current.selectAvatar('ninja'))

  expect(result.current.avatarId).toBe('ninja')
  expect(window.localStorage.getItem('jobos.agentAvatar')).toBe('ninja')
})
