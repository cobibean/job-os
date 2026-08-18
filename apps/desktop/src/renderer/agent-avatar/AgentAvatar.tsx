import type { AgentAvatarId } from './agentAvatars'
import { getAgentAvatar } from './agentAvatars'

export type AgentAvatarState =
  | 'idle'
  | 'restoring'
  | 'working'
  | 'waiting'
  | 'stopping'
  | 'complete'
  | 'offline'
  | 'error'

export type AgentAvatarSize = 'message' | 'empty' | 'settings'

interface AgentAvatarProps {
  avatarId: AgentAvatarId
  state?: AgentAvatarState
  size: AgentAvatarSize
}

export function AgentAvatar({ avatarId, state = 'idle', size }: AgentAvatarProps) {
  const avatar = getAgentAvatar(avatarId)

  return (
    <span
      aria-hidden="true"
      className={`agent-avatar agent-avatar-${size}`}
      data-agent-avatar-id={avatar.id}
      data-agent-avatar-state={state}
    >
      <span className="agent-avatar-artwork">
        <img alt="" draggable={false} src={avatar.src} />
      </span>
    </span>
  )
}
