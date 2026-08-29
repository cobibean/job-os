import { useCallback, useState } from 'react'

import {
  DEFAULT_AGENT_AVATAR_ID,
  isAgentAvatarId,
  type AgentAvatarId
} from './agentAvatars'

const AGENT_AVATAR_STORAGE_KEY = 'jobos.agentAvatar'

function readStoredAgentAvatar(): AgentAvatarId {
  try {
    const stored = window.localStorage.getItem(AGENT_AVATAR_STORAGE_KEY)
    if (isAgentAvatarId(stored)) return stored
  } catch {
    // Storage is optional; use the bundled default when unavailable.
  }
  return DEFAULT_AGENT_AVATAR_ID
}

export interface AgentAvatarPreference {
  avatarId: AgentAvatarId
  selectAvatar: (avatarId: AgentAvatarId) => void
}

export function useAgentAvatarPreference(): AgentAvatarPreference {
  const [avatarId, setAvatarId] = useState(readStoredAgentAvatar)

  const selectAvatar = useCallback((nextAvatarId: AgentAvatarId) => {
    setAvatarId(nextAvatarId)
    try {
      window.localStorage.setItem(AGENT_AVATAR_STORAGE_KEY, nextAvatarId)
    } catch {
      // The current selection still applies when storage is unavailable.
    }
  }, [])

  return { avatarId, selectAvatar }
}
