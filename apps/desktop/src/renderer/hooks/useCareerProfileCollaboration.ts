import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type {
  CareerProfileBridge,
  CareerProfileChangeHistory,
  CareerProfileChangeProposal,
  CareerProfileChangeRevision
} from '../../shared/contracts'

type CollaborationStatus = 'idle' | 'loading' | 'saving' | 'error'

function requestId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : ''
}

export function useCareerProfileCollaboration(
  bridge: CareerProfileBridge,
  online: boolean,
  onProfileChanged: () => Promise<unknown> | void
) {
  const [proposals, setProposals] = useState<CareerProfileChangeProposal[]>([])
  const [history, setHistory] = useState<CareerProfileChangeHistory | null>(null)
  const [status, setStatus] = useState<CollaborationStatus>('idle')
  const [message, setMessage] = useState('')
  const pendingKeys = useRef(new Map<string, string>())
  const mounted = useRef(true)
  const refreshSequence = useRef(0)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const refresh = useCallback(async (showLoading = true) => {
    if (!online) return
    const sequence = ++refreshSequence.current
    if (showLoading && mounted.current) setStatus('loading')
    try {
      const [nextProposals, nextHistory] = await Promise.all([
        bridge.listCareerProfileProposals(),
        bridge.getCareerProfileChangeHistory()
      ])
      if (!mounted.current || sequence !== refreshSequence.current) return
      setProposals(nextProposals.filter(proposal => proposal.status === 'pending'))
      setHistory(nextHistory)
      setStatus('idle')
    } catch {
      if (!mounted.current || sequence !== refreshSequence.current) return
      setStatus('error')
      setMessage('Agent changes could not load right now. Your Career Profile is still available.')
    }
  }, [bridge, online])

  useEffect(() => {
    if (!online) return undefined
    const refreshQuietly = (): void => { void refresh(false) }
    const refreshWhenVisible = (): void => {
      if (document.visibilityState === 'visible') refreshQuietly()
    }
    const interval = window.setInterval(refreshQuietly, 15_000)
    window.addEventListener('focus', refreshQuietly)
    document.addEventListener('visibilitychange', refreshWhenVisible)
    void refresh()
    return () => {
      window.clearInterval(interval)
      window.removeEventListener('focus', refreshQuietly)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [online, refresh])

  const directRevision = useMemo<CareerProfileChangeRevision | null>(() => (
    history?.revisions.find(revision => (
      revision.actorKind === 'autonomous_agent'
      && revision.proposalId === null
      && revision.undoable
    )) ?? null
  ), [history])

  const decide = useCallback(async (
    proposal: CareerProfileChangeProposal,
    decision: 'accept' | 'reject'
  ) => {
    if (!online || status === 'saving') return false
    const signature = `proposal:${proposal.proposalId}:${decision}:${proposal.proposalSha256}:${proposal.baseProfileRevision}`
    const idempotencyKey = pendingKeys.current.get(signature) ?? requestId('career_proposal')
    pendingKeys.current.set(signature, idempotencyKey)
    setStatus('saving')
    setMessage('')
    try {
      await bridge.decideCareerProfileProposal(proposal.proposalId, {
        decision,
        expectedProfileRevision: proposal.baseProfileRevision,
        idempotencyKey,
        proposalSha256: proposal.proposalSha256
      })
      pendingKeys.current.delete(signature)
      setProposals(current => current.filter(candidate => candidate.proposalId !== proposal.proposalId))
      if (decision === 'accept') await onProfileChanged()
      await refresh()
      setMessage(decision === 'accept' ? 'Exact change accepted.' : 'Change rejected. Nothing was changed.')
      return true
    } catch (error) {
      const stale = /revision|stale|changed|regenerat|payload/i.test(errorText(error))
      setStatus('error')
      setMessage(stale
        ? 'This proposal is out of date and cannot replace newer profile data. Ask the agent to regenerate it.'
        : 'JobOS could not save that decision. Nothing was changed—try again.')
      await refresh()
      return false
    }
  }, [bridge, onProfileChanged, online, refresh, status])

  const undo = useCallback(async (revision: CareerProfileChangeRevision) => {
    if (!history || !online || status === 'saving') return false
    const signature = `undo:${revision.revisionId}:${history.profileRevision}`
    const idempotencyKey = pendingKeys.current.get(signature) ?? requestId('career_agent_undo')
    pendingKeys.current.set(signature, idempotencyKey)
    setStatus('saving')
    setMessage('')
    try {
      await bridge.undoCareerProfileChange(revision.revisionId, {
        expectedProfileRevision: history.profileRevision,
        idempotencyKey
      })
      pendingKeys.current.delete(signature)
      await onProfileChanged()
      await refresh()
      setMessage('Agent change undone as a new revision.')
      return true
    } catch (error) {
      const stale = /revision|stale|changed|regenerat/i.test(errorText(error))
      setStatus('error')
      setMessage(stale
        ? 'Your Career Profile changed after this edit. Reloaded the latest version instead of overwriting it.'
        : 'JobOS could not undo that agent change. Nothing was changed—try again.')
      await refresh()
      return false
    }
  }, [bridge, history, onProfileChanged, online, refresh, status])

  return {
    decide,
    directRevision,
    history,
    message,
    proposals,
    refresh,
    status,
    undo
  }
}
