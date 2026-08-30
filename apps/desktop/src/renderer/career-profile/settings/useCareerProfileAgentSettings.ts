import { useCallback, useEffect, useState } from 'react'

import type { CareerProfileBridge, CareerProfileTrustMode, ConnectedCareerProfileAgent } from '../../../shared/contracts'

export function useCareerProfileAgentSettings(bridge: CareerProfileBridge) {
  const [connectedAgents, setConnectedAgents] = useState<ConnectedCareerProfileAgent[]>([])
  const [busyAgentId, setBusyAgentId] = useState<string | null>(null)
  const [disconnectAgentId, setDisconnectAgentId] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [loadFailed, setLoadFailed] = useState(false)

  useEffect(() => {
    let active = true
    setLoadFailed(false)
    void bridge.listConnectedAgents()
      .then(agents => {
        if (active) setConnectedAgents(agents.filter(agent => agent.active))
      })
      .catch(() => {
        if (active) setLoadFailed(true)
      })
    return () => { active = false }
  }, [bridge])

  const changeTrustMode = useCallback(async (agent: ConnectedCareerProfileAgent, trustMode: CareerProfileTrustMode) => {
    if (agent.trustMode === trustMode || busyAgentId) return
    setBusyAgentId(agent.agentId)
    setMessage('')
    try {
      const updated = await bridge.updateConnectedAgentTrustMode(agent.agentId, trustMode)
      setConnectedAgents(current => current.map(candidate => candidate.agentId === updated.agentId ? updated : candidate))
      setMessage(trustMode === 'direct'
        ? `${agent.displayName} can now make ordinary edits directly.`
        : `${agent.displayName} will ask before every Career Profile change.`)
    } catch {
      setMessage(`Could not change ${agent.displayName}’s edit mode. Try again.`)
    } finally {
      setBusyAgentId(null)
    }
  }, [bridge, busyAgentId])

  const disconnectAgent = useCallback(async (agent: ConnectedCareerProfileAgent) => {
    if (busyAgentId) return
    setBusyAgentId(agent.agentId)
    setMessage('')
    try {
      await bridge.disconnectConnectedAgent(agent.agentId)
      setConnectedAgents(current => current.filter(candidate => candidate.agentId !== agent.agentId))
      setDisconnectAgentId(null)
      setMessage(`${agent.displayName} is disconnected. Your Career Profile was not changed.`)
    } catch {
      setMessage(`Could not disconnect ${agent.displayName}. Try again.`)
    } finally {
      setBusyAgentId(null)
    }
  }, [bridge, busyAgentId])

  return {
    busyAgentId,
    changeTrustMode,
    connectedAgents,
    disconnectAgent,
    disconnectAgentId,
    loadFailed,
    message,
    setDisconnectAgentId
  }
}
