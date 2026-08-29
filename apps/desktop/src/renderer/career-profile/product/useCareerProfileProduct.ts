import { useCallback, useEffect, useRef, useState } from 'react'

import type {
  CareerProfileBridge,
  CareerProfileCurrent,
  CareerProfileEvidenceImportRequest,
  CareerProfileItemSnapshot,
  CareerProfileMutationResult
} from '../../../shared/contracts'

export type CareerProfileProductStatus = 'loading' | 'ready' | 'saving' | 'saved' | 'conflict' | 'error'
export type CareerProfileProductDataSource = 'none' | 'live' | 'cache' | 'stale'

export interface CareerProfileItemConflict {
  canPreserveBoth: boolean
  latestItem: CareerProfileItemSnapshot | null
  originalItemId: string | null
  proposedEvidenceIds: string[]
  proposedValue: Record<string, unknown> & { kind: string }
}

const COMPLETE_PROFILE_CACHE_KEY = 'jobos.careerProfile.complete.v1'
const COMPLETE_PROFILE_CACHE_SCHEMA_VERSION = 1
const MAX_CACHE_PAYLOAD_BYTES = 5 * 1024 * 1024
const MAX_CACHED_ITEMS = 10_000
const MAX_CACHED_EVIDENCE = 10_000

const parallelItemKinds = new Set([
  'education',
  'skill',
  'experience',
  'project',
  'claim',
  'target_roles',
  'location',
  'industries',
  'priority',
  'dealbreaker',
  'custom'
])

interface CompleteProfileCacheEnvelope {
  payload: string
  schemaVersion: typeof COMPLETE_PROFILE_CACHE_SCHEMA_VERSION
  sha256: string
}

function requestId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isBoundedString(value: unknown, max = 10_000): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0
}

function isJsonValue(value: unknown, depth = 0): boolean {
  if (depth > 12) return false
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) return value.length <= 10_000 && value.every(entry => isJsonValue(entry, depth + 1))
  if (!isRecord(value) || Object.keys(value).length > 1_000) return false
  return Object.entries(value).every(([key, entry]) => key.length > 0 && key.length <= 500 && isJsonValue(entry, depth + 1))
}

function isStringArray(value: unknown, limit: number): value is string[] {
  return Array.isArray(value)
    && value.length <= limit
    && value.every(entry => isBoundedString(entry, 500))
    && new Set(value).size === value.length
}

function isCachedItem(value: unknown): value is CareerProfileItemSnapshot {
  if (!isRecord(value)) return false
  if (!isBoundedString(value.actorPrincipal, 500)) return false
  if (!['my_career', 'what_im_looking_for', 'my_evidence'].includes(String(value.area))) return false
  if (!isBoundedString(value.createdAt, 100) || !Number.isFinite(Date.parse(value.createdAt))) return false
  if (!isStringArray(value.evidenceIds, 500)) return false
  if (!isBoundedString(value.itemId, 200) || !isNonNegativeInteger(value.itemRevision)) return false
  if (!isRecord(value.provenance) || !isJsonValue(value.provenance)) return false
  if (!['accepted', 'proposed', 'conflicting'].includes(String(value.reviewStatus))) return false
  if (!isBoundedString(value.updatedAt, 100) || !Number.isFinite(Date.parse(value.updatedAt))) return false
  if (!isRecord(value.value) || !isBoundedString(value.value.kind, 100) || !isJsonValue(value.value)) return false
  return true
}

function isCachedEvidence(value: unknown): boolean {
  if (!isRecord(value) || typeof value.active !== 'boolean') return false
  if (!isNonNegativeInteger(value.byteCount)) return false
  if (value.capturedAt !== null && (!isBoundedString(value.capturedAt, 100) || !Number.isFinite(Date.parse(value.capturedAt)))) return false
  if (!isBoundedString(value.evidenceId, 200)) return false
  if (!isBoundedString(value.importedAt, 100) || !Number.isFinite(Date.parse(value.importedAt))) return false
  if (!isBoundedString(value.mediaType, 200) || !isBoundedString(value.originalFilename, 1_000)) return false
  if (!isRecord(value.provenance)) return false
  if (!['user_import', 'agent_import', 'migration_import'].includes(String(value.provenance.method))) return false
  if (!['resume', 'portfolio', 'supporting_document', 'citation'].includes(String(value.provenance.sourceKind))) return false
  if (!isBoundedString(value.provenance.sourceLabel, 1_000)) return false
  return typeof value.sha256 === 'string' && /^[a-f0-9]{64}$/i.test(value.sha256)
}

function isCareerProfileCurrent(value: unknown): value is CareerProfileCurrent {
  if (!isRecord(value)) return false
  const keys = Object.keys(value).sort()
  if (keys.join('|') !== 'authorityEpoch|items|profileRevision|sourceEvidence') return false
  if (!isNonNegativeInteger(value.authorityEpoch) || !isNonNegativeInteger(value.profileRevision)) return false
  if (!Array.isArray(value.items) || value.items.length > MAX_CACHED_ITEMS || !value.items.every(isCachedItem)) return false
  if (!Array.isArray(value.sourceEvidence) || value.sourceEvidence.length > MAX_CACHED_EVIDENCE || !value.sourceEvidence.every(isCachedEvidence)) return false
  const itemIds = value.items.map(item => item.itemId)
  const evidenceIds = value.sourceEvidence.map(source => source.evidenceId)
  return new Set(itemIds).size === itemIds.length && new Set(evidenceIds).size === evidenceIds.length
}

async function sha256(value: string): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

async function buildCacheEnvelope(profile: CareerProfileCurrent): Promise<CompleteProfileCacheEnvelope | null> {
  if (!isCareerProfileCurrent(profile)) return null
  const payload = JSON.stringify(profile)
  if (new TextEncoder().encode(payload).byteLength > MAX_CACHE_PAYLOAD_BYTES) return null
  const digest = await sha256(payload)
  return digest ? { payload, schemaVersion: COMPLETE_PROFILE_CACHE_SCHEMA_VERSION, sha256: digest } : null
}

async function readCachedProfile(): Promise<CareerProfileCurrent | null> {
  try {
    const raw = globalThis.localStorage?.getItem(COMPLETE_PROFILE_CACHE_KEY)
    if (!raw || raw.length > MAX_CACHE_PAYLOAD_BYTES * 2) return null
    const envelope: unknown = JSON.parse(raw)
    if (!isRecord(envelope)) return null
    if (envelope.schemaVersion !== COMPLETE_PROFILE_CACHE_SCHEMA_VERSION) return null
    if (typeof envelope.payload !== 'string' || typeof envelope.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(envelope.sha256)) return null
    if (new TextEncoder().encode(envelope.payload).byteLength > MAX_CACHE_PAYLOAD_BYTES) return null
    const digest = await sha256(envelope.payload)
    if (!digest || digest !== envelope.sha256) return null
    const profile: unknown = JSON.parse(envelope.payload)
    return isCareerProfileCurrent(profile) ? profile : null
  } catch {
    return null
  }
}

export function useCareerProfileProduct(bridge: CareerProfileBridge) {
  const [current, setCurrent] = useState<CareerProfileCurrent | null>(null)
  const [dataSource, setDataSource] = useState<CareerProfileProductDataSource>('none')
  const [itemConflict, setItemConflict] = useState<CareerProfileItemConflict | null>(null)
  const [status, setStatus] = useState<CareerProfileProductStatus>('loading')
  const [message, setMessage] = useState('')
  const currentRef = useRef<CareerProfileCurrent | null>(null)
  const pendingKeys = useRef(new Map<string, string>())
  const cacheWriteSequence = useRef(0)
  const headCheckRunning = useRef(false)

  useEffect(() => {
    if (status !== 'saved' || !message) return undefined
    const timeout = window.setTimeout(() => {
      setMessage('')
      setStatus(currentStatus => currentStatus === 'saved' ? 'ready' : currentStatus)
    }, 5_000)
    return () => { window.clearTimeout(timeout) }
  }, [message, status])

  const persist = useCallback(async (profile: CareerProfileCurrent) => {
    const sequence = ++cacheWriteSequence.current
    try {
      const envelope = await buildCacheEnvelope(profile)
      if (!envelope || sequence !== cacheWriteSequence.current) return
      globalThis.localStorage?.setItem(COMPLETE_PROFILE_CACHE_KEY, JSON.stringify(envelope))
    } catch {
      // A cache failure must never make live profile data unavailable.
    }
  }, [])

  const invalidatePersistentCache = useCallback(() => {
    cacheWriteSequence.current += 1
    try {
      globalThis.localStorage?.removeItem(COMPLETE_PROFILE_CACHE_KEY)
    } catch {
      // Erasure/reset must still proceed when browser storage is unavailable.
    }
  }, [])

  const useCurrent = useCallback((profile: CareerProfileCurrent, source: CareerProfileProductDataSource, persistLive = true) => {
    currentRef.current = profile
    setCurrent(profile)
    setDataSource(source)
    if (source === 'live' && persistLive) void persist(profile)
  }, [persist])

  const load = useCallback(async (showLoading = true) => {
    if (showLoading) setStatus('loading')
    try {
      const next = await bridge.getCareerProfile()
      useCurrent(next, 'live')
      setItemConflict(null)
      setStatus('ready')
      setMessage('')
      return true
    } catch {
      const existing = currentRef.current
      if (existing) {
        setDataSource('stale')
        setStatus('error')
        setMessage('The complete Career Profile could not refresh. JobOS is showing the last loaded profile and has disabled changes until reconnecting.')
        return false
      }
      const cached = await readCachedProfile()
      if (cached) {
        useCurrent(cached, 'cache')
        setStatus('error')
        setMessage('Offline — showing the last validated cached profile. It is read-only until JobOS reconnects.')
        return false
      }
      setStatus('error')
      setMessage('The complete Career Profile could not load. Your work-arrangement preference is still available.')
      return false
    }
  }, [bridge, useCurrent])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    const syncToCollaborationHead = async () => {
      if (headCheckRunning.current) return
      headCheckRunning.current = true
      try {
        const history = await bridge.getCareerProfileChangeHistory()
        const visible = currentRef.current
        if (visible && history.profileRevision !== visible.profileRevision) await load(false)
      } catch {
        // Collaboration refresh owns its own visible error; keep existing profile content.
      } finally {
        headCheckRunning.current = false
      }
    }
    const syncWhenVisible = () => {
      if (document.visibilityState === 'visible') void syncToCollaborationHead()
    }
    const syncOnFocus = () => { void syncToCollaborationHead() }
    const interval = window.setInterval(syncOnFocus, 15_000)
    window.addEventListener('focus', syncOnFocus)
    document.addEventListener('visibilitychange', syncWhenVisible)
    void syncToCollaborationHead()
    return () => {
      window.clearInterval(interval)
      window.removeEventListener('focus', syncOnFocus)
      document.removeEventListener('visibilitychange', syncWhenVisible)
    }
  }, [bridge, load])

  const applyMutation = useCallback((result: CareerProfileMutationResult, successMessage: string) => {
    useCurrent(result.current, 'live')
    if (result.status === 'conflict') {
      setStatus('conflict')
      setMessage('Your Career Profile changed somewhere else. The latest saved version is shown; review it before trying again.')
      return false
    }
    setStatus('saved')
    setMessage(successMessage)
    return true
  }, [useCurrent])

  const mutationKey = useCallback((signature: string, prefix: string) => {
    const existing = pendingKeys.current.get(signature)
    if (existing) return existing
    const created = requestId(prefix)
    pendingKeys.current.set(signature, created)
    return created
  }, [])

  const saveItem = useCallback(async (
    item: CareerProfileItemSnapshot | null,
    value: Record<string, unknown> & { kind: string },
    evidenceIds: string[],
    expectedProfileRevision: number
  ) => {
    const visible = currentRef.current
    if (!visible || dataSource !== 'live' || status === 'saving') return false
    const signature = JSON.stringify({ operation: item ? 'update' : 'create', itemId: item?.itemId, revision: expectedProfileRevision, value, evidenceIds })
    const idempotencyKey = mutationKey(signature, 'career_item')
    setStatus('saving')
    setMessage('')
    try {
      const request = {
        evidenceIds,
        expectedProfileRevision,
        idempotencyKey,
        value
      }
      const result = item
        ? await bridge.updateCareerProfileItem(item.itemId, request)
        : await bridge.createCareerProfileItem(request)
      pendingKeys.current.delete(signature)
      useCurrent(result.current, 'live')
      if (result.status === 'conflict') {
        const latestItem = item
          ? result.current.items.find(candidate => candidate.itemId === item.itemId) ?? null
          : null
        setItemConflict({
          canPreserveBoth: Boolean(latestItem && parallelItemKinds.has(value.kind)),
          latestItem,
          originalItemId: item?.itemId ?? null,
          proposedEvidenceIds: [...evidenceIds],
          proposedValue: { ...value }
        })
        setStatus('conflict')
        setMessage('Your edit met a newer saved version. Compare both versions and choose what JobOS should keep.')
        return false
      }
      setItemConflict(null)
      setStatus('saved')
      setMessage(item ? 'Career detail updated.' : 'Career detail added.')
      return true
    } catch {
      setStatus('error')
      setMessage('JobOS could not save that detail. Your draft is still here—try again.')
      return false
    }
  }, [bridge, dataSource, mutationKey, status, useCurrent])

  const keepItemConflict = useCallback(() => {
    setItemConflict(null)
    setStatus('ready')
    setMessage('Kept the current saved detail. Your newer profile content was not overwritten.')
  }, [])

  const dismissItemConflict = useCallback(() => {
    setItemConflict(null)
    setStatus('ready')
    setMessage('')
  }, [])

  const reapplyItemConflict = useCallback(async () => {
    const latestRevision = currentRef.current?.profileRevision
    if (!itemConflict || latestRevision === undefined) return false
    return saveItem(itemConflict.latestItem, itemConflict.proposedValue, itemConflict.proposedEvidenceIds, latestRevision)
  }, [itemConflict, saveItem])

  const preserveBothItemConflict = useCallback(async () => {
    const latestRevision = currentRef.current?.profileRevision
    if (!itemConflict?.canPreserveBoth || latestRevision === undefined) return false
    return saveItem(null, itemConflict.proposedValue, itemConflict.proposedEvidenceIds, latestRevision)
  }, [itemConflict, saveItem])

  const removeItem = useCallback(async (item: CareerProfileItemSnapshot) => {
    const visible = currentRef.current
    if (!visible || dataSource !== 'live' || status === 'saving') return false
    const signature = `remove-item:${item.itemId}:${visible.profileRevision}`
    const idempotencyKey = mutationKey(signature, 'career_remove')
    setStatus('saving')
    setMessage('')
    try {
      const result = await bridge.removeCareerProfileItem(item.itemId, {
        expectedProfileRevision: visible.profileRevision,
        idempotencyKey
      })
      pendingKeys.current.delete(signature)
      setItemConflict(null)
      return applyMutation(result, 'Career detail removed. You can restore it from History.')
    } catch {
      setStatus('error')
      setMessage('JobOS could not remove that detail. Nothing was changed—try again.')
      return false
    }
  }, [applyMutation, bridge, dataSource, mutationKey, status])

  const importEvidence = useCallback(async (request: CareerProfileEvidenceImportRequest) => {
    if (!currentRef.current || dataSource !== 'live' || status === 'saving') return 'error' as const
    setStatus('saving')
    setMessage('')
    try {
      const result = await bridge.importCareerProfileEvidence(request)
      applyMutation(result, `${request.originalFilename} added to My Evidence.`)
      return result.status
    } catch {
      setStatus('error')
      setMessage(`${request.originalFilename} could not be imported. Other sources are unchanged.`)
      return 'error' as const
    }
  }, [applyMutation, bridge, dataSource, status])

  const removeEvidence = useCallback(async (evidenceId: string) => {
    const visible = currentRef.current
    if (!visible || dataSource !== 'live' || status === 'saving') return false
    const signature = `remove-evidence:${evidenceId}:${visible.profileRevision}`
    const idempotencyKey = mutationKey(signature, 'career_evidence_remove')
    setStatus('saving')
    setMessage('')
    try {
      const result = await bridge.removeCareerProfileEvidence(evidenceId, {
        expectedProfileRevision: visible.profileRevision,
        idempotencyKey
      })
      pendingKeys.current.delete(signature)
      invalidatePersistentCache()
      useCurrent(result.current, 'live', false)
      if (result.status === 'conflict') {
        setStatus('conflict')
        setMessage('Your Career Profile changed somewhere else. The latest saved version is shown; review it before trying again.')
        return false
      }
      setStatus('saved')
      setMessage('Evidence removed from active Career Profile use. Its history remains available.')
      return true
    } catch {
      setStatus('error')
      setMessage('JobOS could not remove that Evidence source. Nothing was changed—try again.')
      return false
    }
  }, [bridge, dataSource, invalidatePersistentCache, mutationKey, status, useCurrent])

  const confirmBaselineRestored = useCallback((unavailableEvidenceCount: number) => {
    setItemConflict(null)
    setStatus('saved')
    setMessage(unavailableEvidenceCount > 0
      ? `Baseline restored. ${unavailableEvidenceCount} omitted Evidence source${unavailableEvidenceCount === 1 ? ' is' : 's are'} marked unavailable.`
      : 'Baseline restored. This archive is now the start of a new Career Profile timeline.')
  }, [])

  return {
    confirmBaselineRestored,
    current,
    dataSource,
    dismissItemConflict,
    importEvidence,
    invalidatePersistentCache,
    itemConflict,
    keepItemConflict,
    load,
    message,
    preserveBothItemConflict,
    readOnly: dataSource !== 'live',
    reapplyItemConflict,
    removeEvidence,
    removeItem,
    saveItem,
    status
  }
}

export type CareerProfileProductController = ReturnType<typeof useCareerProfileProduct>
