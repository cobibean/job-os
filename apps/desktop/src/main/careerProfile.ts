import type {
  WorkArrangementCurrent as ApiWorkArrangementCurrent,
  WorkArrangementHistory as ApiWorkArrangementHistory,
  WorkArrangementRecord as ApiWorkArrangementRecord,
  WorkArrangementRevision as ApiWorkArrangementRevision
} from '@jobos/contracts'
import { createHmac, timingSafeEqual } from 'node:crypto'
import { CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT, careerProfileAdditionalContextLength } from '../shared/contracts.js'

import type {
  WorkArrangementCurrent,
  WorkArrangementHistory,
  WorkArrangementMutationRequest,
  WorkArrangementMutationResult,
  WorkArrangementRecord,
  WorkArrangementRestoreRequest,
  WorkArrangementRevision,
  WorkArrangementValue
} from '../shared/contracts.js'
import type { JobsConfig } from './jobs.js'

const ROUTE = '/v1/career-profile/work-arrangement'
const modes = new Set(['remote', 'hybrid', 'onsite', 'flexible'])
const strengths = new Set(['requirement', 'strong_preference', 'preference', 'dealbreaker'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function mapValue(value: ApiWorkArrangementRecord['value']): WorkArrangementValue {
  return { mode: value.mode, strength: value.strength, note: value.note ?? null }
}

function mapRecord(record: ApiWorkArrangementRecord): WorkArrangementRecord {
  return {
    actorPrincipal: record.actor_principal,
    itemRevision: record.item_revision,
    profileRevision: record.profile_revision,
    recordId: record.record_id,
    updatedAt: record.updated_at,
    value: mapValue(record.value)
  }
}

function mapCurrent(current: ApiWorkArrangementCurrent): WorkArrangementCurrent {
  return {
    profileRevision: current.profile_revision,
    record: current.record ? mapRecord(current.record) : null
  }
}

function mapRevision(revision: ApiWorkArrangementRevision): WorkArrangementRevision {
  return {
    actorPrincipal: revision.actor_principal,
    baseProfileRevision: revision.base_profile_revision,
    changedFields: revision.changed_fields,
    createdAt: revision.created_at,
    itemRevision: revision.item_revision,
    operation: revision.operation,
    profileRevision: revision.profile_revision,
    recordId: revision.record_id,
    restoredFromProfileRevision: revision.restored_from_profile_revision ?? null,
    revisionId: revision.revision_id,
    value: mapValue(revision.value)
  }
}

function mapHistory(history: ApiWorkArrangementHistory): WorkArrangementHistory {
  return {
    profileRevision: history.profile_revision,
    revisions: history.revisions.map(mapRevision)
  }
}

function validateIdempotencyKey(value: string): string {
  if (!/^[A-Za-z0-9_-]{8,128}$/.test(value)) throw new Error('Invalid Career Profile request')
  return value
}

function validateRevision(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) throw new Error('Invalid Career Profile revision')
  return value
}

function validateExistingRevision(value: number): number {
  if (!Number.isSafeInteger(value) || value < 1) throw new Error('Invalid Career Profile revision')
  return value
}

function validateValue(value: WorkArrangementValue): WorkArrangementValue {
  if (!modes.has(value.mode) || !strengths.has(value.strength)) throw new Error('Invalid work arrangement')
  const note = value.note === '' ? null : value.note
  if (note && careerProfileAdditionalContextLength(note) > CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT) throw new Error('Work arrangement note is too long')
  return { mode: value.mode, strength: value.strength, note }
}

async function request(config: JobsConfig, route: string, init?: RequestInit): Promise<Response> {
  return fetch(new URL(route, config.baseUrl), {
    ...init,
    headers: {
      Authorization: `Bearer ${config.deviceToken}`,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {})
    },
    redirect: 'error'
  })
}

async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({})) as { detail?: string; message?: string }
  return body.detail ?? body.message ?? `Career Profile request failed (${response.status})`
}

export function createMainCareerProfileClient(config: JobsConfig) {
  const protectCurrent = (current: WorkArrangementCurrent): WorkArrangementCurrent => {
    const unsigned = { profileRevision: current.profileRevision, record: current.record }
    const cacheProof = createHmac('sha256', config.deviceToken)
      .update(JSON.stringify({ baseUrl: config.baseUrl, ...unsigned }))
      .digest('hex')
    return { cacheProof, ...unsigned }
  }

  const validateCachedWorkArrangement = (candidate: unknown): WorkArrangementCurrent | null => {
    if (!isRecord(candidate) || typeof candidate.cacheProof !== 'string' || !/^[a-f0-9]{64}$/.test(candidate.cacheProof)) return null
    if (!Number.isSafeInteger(candidate.profileRevision) || (candidate.profileRevision as number) < 0) return null
    if (candidate.record !== null) {
      if (!isRecord(candidate.record) || !isRecord(candidate.record.value)) return null
      const record = candidate.record
      const value = record.value as Record<string, unknown>
      if (!Number.isSafeInteger(record.itemRevision) || (record.itemRevision as number) < 1) return null
      if (!Number.isSafeInteger(record.profileRevision) || (record.profileRevision as number) < 1) return null
      if ((record.profileRevision as number) > (candidate.profileRevision as number)) return null
      if (typeof record.actorPrincipal !== 'string' || typeof record.recordId !== 'string' || typeof record.updatedAt !== 'string') return null
      if (typeof value.mode !== 'string' || !modes.has(value.mode) || typeof value.strength !== 'string' || !strengths.has(value.strength)) return null
      if (value.note !== null && (typeof value.note !== 'string' || careerProfileAdditionalContextLength(value.note) > CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT)) return null
    }
    const unsigned = {
      profileRevision: candidate.profileRevision as number,
      record: candidate.record as WorkArrangementRecord | null
    }
    const expected = protectCurrent(unsigned).cacheProof!
    const actualBuffer = Buffer.from(candidate.cacheProof, 'hex')
    const expectedBuffer = Buffer.from(expected, 'hex')
    return timingSafeEqual(actualBuffer, expectedBuffer) ? { cacheProof: candidate.cacheProof, ...unsigned } : null
  }

  const getWorkArrangement = async (): Promise<WorkArrangementCurrent> => {
    const response = await request(config, ROUTE)
    if (!response.ok) throw new Error(await errorMessage(response))
    return protectCurrent(mapCurrent(await response.json() as ApiWorkArrangementCurrent))
  }

  const mutationResult = async (response: Response): Promise<WorkArrangementMutationResult> => {
    if (response.status === 409) {
      return { status: 'conflict', current: await getWorkArrangement() }
    }
    if (!response.ok) throw new Error(await errorMessage(response))
    return { status: 'saved', current: protectCurrent(mapCurrent(await response.json() as ApiWorkArrangementCurrent)) }
  }

  return {
    async availability(): Promise<{ enabled: boolean }> {
      const response = await request(config, ROUTE)
      if (response.status === 404) return { enabled: false }
      if (!response.ok) throw new Error(await errorMessage(response))
      return { enabled: true }
    },
    validateCachedWorkArrangement,
    getWorkArrangement,
    async saveWorkArrangement(requestBody: WorkArrangementMutationRequest): Promise<WorkArrangementMutationResult> {
      const body = {
        expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey),
        value: validateValue(requestBody.value)
      }
      return mutationResult(await request(config, ROUTE, { method: 'PUT', body: JSON.stringify(body) }))
    },
    async getWorkArrangementHistory(): Promise<WorkArrangementHistory> {
      const response = await request(config, `${ROUTE}/history`)
      if (!response.ok) throw new Error(await errorMessage(response))
      return mapHistory(await response.json() as ApiWorkArrangementHistory)
    },
    async restoreWorkArrangement(requestBody: WorkArrangementRestoreRequest): Promise<WorkArrangementMutationResult> {
      const body = {
        expected_profile_revision: validateExistingRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey),
        target_profile_revision: validateExistingRevision(requestBody.targetProfileRevision)
      }
      return mutationResult(await request(config, `${ROUTE}/restore`, { method: 'POST', body: JSON.stringify(body) }))
    }
  }
}
