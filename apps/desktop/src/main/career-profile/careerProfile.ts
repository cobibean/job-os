import { jobOsAuthenticatedHeaders } from '@jobos/contracts'
import type {
  CareerProfileChangeProposal as ApiCareerProfileChangeProposal,
  CareerProfileCompleteCurrent as ApiCareerProfileCompleteCurrent,
  CareerProfileContextPreview as ApiCareerProfileContextPreview,
  CareerProfileContextScope as ApiCareerProfileContextScope,
  CareerProfileExportResult as ApiCareerProfileExportResult,
  CareerProfileProposalList as ApiCareerProfileProposalList,
  CareerProfileRestoreResult as ApiCareerProfileRestoreResult,
  ConnectedAgent as ApiConnectedAgent,
  ConnectedAgentList as ApiConnectedAgentList,
  ProfileHistory as ApiProfileHistory,
  ProfileHistoryRevision as ApiProfileHistoryRevision,
  ProfileItemMutation as ApiProfileItemMutation,
  ProfileItemRecord as ApiProfileItemRecord,
  ProposalDecisionResult as ApiProposalDecisionResult,
  SourceEvidenceRecord as ApiSourceEvidenceRecord,
  WorkArrangementCurrent as ApiWorkArrangementCurrent,
  WorkArrangementHistory as ApiWorkArrangementHistory,
  WorkArrangementRecord as ApiWorkArrangementRecord,
  WorkArrangementRevision as ApiWorkArrangementRevision
} from '@jobos/contracts'
import { createHash, createHmac, randomUUID, timingSafeEqual } from 'node:crypto'
import { constants, type BigIntStats } from 'node:fs'
import { lstat, open, rename, unlink, type FileHandle } from 'node:fs/promises'
import path from 'node:path'
import { CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT, careerProfileAdditionalContextLength } from '../../shared/contracts.js'
import { writeCareerProfileArchiveNative } from './careerProfileArchiveWriter.js'

import type {
  CareerProfileChangeHistory,
  CareerProfileChangeProposal,
  CareerProfileChangeRevision,
  CareerProfileContextPreview,
  CareerProfileContextScope,
  CareerProfileContextUpdateRequest,
  CareerProfileCurrent,
  CareerProfileEvidence,
  CareerProfileEvidenceImportRequest,
  CareerProfileEvidenceMode,
  CareerProfileExportRequest,
  CareerProfileExportResult,
  CareerProfileItemMutationRequest,
  CareerProfileItemSnapshot,
  CareerProfileMutationResult,
  CareerProfileProposalDecisionRequest,
  CareerProfileProposalDecisionResult,
  CareerProfileRemovalRequest,
  CareerProfileRestoreRequest,
  CareerProfileRestoreResult,
  CareerProfileTrustMode,
  CareerProfileUndoRequest,
  ConnectedCareerProfileAgent,
  WorkArrangementCurrent,
  WorkArrangementHistory,
  WorkArrangementMutationRequest,
  WorkArrangementMutationResult,
  WorkArrangementRecord,
  WorkArrangementRestoreRequest,
  WorkArrangementRevision,
  WorkArrangementValue
} from '../../shared/contracts.js'
import type { DesktopApiConfig } from '../app/runtime/desktopApiConfig.js'

const ROUTE = '/v1/career-profile/work-arrangement'
const COLLABORATION_ROUTE = '/v1/career-profile'
const MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
const MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
const ARCHIVE_SELECTION_TTL_MS = 30 * 60 * 1000
const ARCHIVE_IO_CHUNK_BYTES = 1024 * 1024
const O_NOFOLLOW_FLAG = typeof constants.O_NOFOLLOW === 'number' ? constants.O_NOFOLLOW : 0
const O_DIRECTORY_FLAG = typeof constants.O_DIRECTORY === 'number' ? constants.O_DIRECTORY : 0
const REGULAR_ARCHIVE_ERROR = 'Choose a regular JobOS Career Profile ZIP smaller than 100 MiB'
const CHANGED_ARCHIVE_ERROR = 'The Career Profile archive changed while it was being read'
const modes = new Set(['remote', 'hybrid', 'onsite', 'flexible'])
const strengths = new Set(['requirement', 'strong_preference', 'preference', 'dealbreaker'])
const trustModes = new Set<CareerProfileTrustMode>(['review', 'direct'])
const contextModes = new Set(['none', 'selected', 'broader'])
const careerProfileAreas = new Set(['my_career', 'what_im_looking_for', 'my_evidence'])
const evidenceModes = new Set<CareerProfileEvidenceMode>(['profile_only', 'selected', 'all'])
const evidenceKinds = new Set(['resume', 'portfolio', 'supporting_document', 'citation'])
const profileValueKinds = new Set([
  'identity', 'education', 'skill', 'positioning', 'experience', 'project', 'claim',
  'target_roles', 'compensation', 'location', 'work_arrangement', 'industries',
  'priority', 'dealbreaker', 'custom'
])

export interface CareerProfileNative {
  chooseArchivePath: () => Promise<string | null>
  chooseExportPath: (filename: string) => Promise<string | null>
}

export interface CareerProfileArchiveFileSystem {
  lstat: (filePath: string) => Promise<BigIntStats>
  open: (filePath: string, flags: number, mode?: number) => Promise<FileHandle>
  rename: (sourcePath: string, destinationPath: string) => Promise<void>
  unlink: (filePath: string) => Promise<void>
}

export interface CareerProfileClientOptions {
  archiveFileSystem?: Partial<CareerProfileArchiveFileSystem>
  archiveWriter?: (target: string, bytes: Buffer, expectedSha256: string) => Promise<void>
}

const defaultArchiveFileSystem: CareerProfileArchiveFileSystem = {
  lstat: filePath => lstat(filePath, { bigint: true }),
  open: (filePath, flags, mode) => open(filePath, flags, mode),
  rename: (sourcePath, destinationPath) => rename(sourcePath, destinationPath),
  unlink: filePath => unlink(filePath)
}

function errnoCode(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null || !('code' in error)) return undefined
  const code = (error as { code?: unknown }).code
  return typeof code === 'string' ? code : undefined
}

function sameFileIdentity(left: BigIntStats, right: BigIntStats): boolean {
  return left.dev === right.dev && left.ino === right.ino
}

function sameFileVersion(left: BigIntStats, right: BigIntStats): boolean {
  return sameFileIdentity(left, right)
    && left.mode === right.mode
    && left.size === right.size
    && left.mtimeNs === right.mtimeNs
    && left.ctimeNs === right.ctimeNs
}

function assertReadableArchive(metadata: BigIntStats): number {
  if (!metadata.isFile()
    || metadata.isSymbolicLink()
    || metadata.size < 1n
    || metadata.size > BigInt(MAX_ARCHIVE_BYTES)) {
    throw new Error(REGULAR_ARCHIVE_ERROR)
  }
  return Number(metadata.size)
}

async function readDescriptorExactly(
  handle: FileHandle,
  expectedSize: number,
  changedMessage: string
): Promise<Buffer> {
  const bytes = Buffer.allocUnsafe(expectedSize)
  let position = 0
  while (position < expectedSize) {
    const length = Math.min(ARCHIVE_IO_CHUNK_BYTES, expectedSize - position)
    const result = await handle.read(bytes, position, length, position)
    if (result.bytesRead < 1 || result.bytesRead > length) throw new Error(changedMessage)
    position += result.bytesRead
  }
  const probe = Buffer.allocUnsafe(1)
  if ((await handle.read(probe, 0, 1, expectedSize)).bytesRead !== 0) throw new Error(changedMessage)
  return bytes
}

async function hashDescriptorExactly(
  handle: FileHandle,
  expectedSize: number,
  integrityMessage: string
): Promise<string> {
  const digest = createHash('sha256')
  const chunk = Buffer.allocUnsafe(Math.min(ARCHIVE_IO_CHUNK_BYTES, expectedSize))
  let position = 0
  while (position < expectedSize) {
    const length = Math.min(chunk.length, expectedSize - position)
    const result = await handle.read(chunk, 0, length, position)
    if (result.bytesRead < 1 || result.bytesRead > length) throw new Error(integrityMessage)
    digest.update(chunk.subarray(0, result.bytesRead))
    position += result.bytesRead
  }
  const probe = Buffer.allocUnsafe(1)
  if ((await handle.read(probe, 0, 1, expectedSize)).bytesRead !== 0) throw new Error(integrityMessage)
  return digest.digest('hex')
}

async function openTemporaryArchive(
  fileSystem: CareerProfileArchiveFileSystem,
  destinationDirectory: string
): Promise<{ handle: FileHandle; temporaryPath: string }> {
  const flags = constants.O_RDWR | constants.O_CREAT | constants.O_EXCL | O_NOFOLLOW_FLAG
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const temporaryPath = path.join(destinationDirectory, `.jobos-career-profile-${randomUUID()}.tmp`)
    try {
      const handle = await fileSystem.open(temporaryPath, flags, 0o600)
      return { handle, temporaryPath }
    } catch (error) {
      if (errnoCode(error) !== 'EEXIST') throw error
    }
  }
  throw new Error('Unable to create a private temporary Career Profile export')
}

async function openDirectoryForSync(
  fileSystem: CareerProfileArchiveFileSystem,
  destinationDirectory: string
): Promise<FileHandle | null> {
  // Node cannot fsync directory handles on Windows. POSIX exports fail before
  // touching the target unless the destination directory can be opened safely.
  if (process.platform === 'win32') return null
  const flags = constants.O_RDONLY | O_DIRECTORY_FLAG | O_NOFOLLOW_FLAG
  const handle = await fileSystem.open(destinationDirectory, flags)
  try {
    if (!(await handle.stat({ bigint: true })).isDirectory()) {
      throw new Error('Career Profile export destination is not a directory')
    }
    return handle
  } catch (error) {
    await handle.close().catch(() => undefined)
    throw error
  }
}

async function writeArchiveAtomically(
  fileSystem: CareerProfileArchiveFileSystem,
  target: string,
  bytes: Buffer,
  expectedSha256: string
): Promise<void> {
  const destinationDirectory = path.dirname(target)
  const destinationName = path.basename(target)
  if (!destinationName || destinationName === '.' || destinationName === '..') {
    throw new Error('Choose a valid Career Profile export filename')
  }

  const directoryHandle = await openDirectoryForSync(fileSystem, destinationDirectory)
  let temporaryPath: string | null = null
  let operationError: unknown
  try {
    const temporary = await openTemporaryArchive(fileSystem, destinationDirectory)
    temporaryPath = temporary.temporaryPath
    let verifiedMetadata: BigIntStats
    try {
      await temporary.handle.chmod(0o600)
      const createdMetadata = await temporary.handle.stat({ bigint: true })
      if (!createdMetadata.isFile() || createdMetadata.isSymbolicLink() || createdMetadata.size !== 0n) {
        throw new Error('Career Profile export failed its integrity check')
      }
      await temporary.handle.writeFile(bytes)
      await temporary.handle.sync()
      const flushedMetadata = await temporary.handle.stat({ bigint: true })
      if (!flushedMetadata.isFile()
        || flushedMetadata.isSymbolicLink()
        || flushedMetadata.size !== BigInt(bytes.length)
        || (process.platform !== 'win32' && (flushedMetadata.mode & 0o777n) !== 0o600n)) {
        throw new Error('Career Profile export failed its integrity check')
      }
      const persistedSha256 = await hashDescriptorExactly(
        temporary.handle,
        bytes.length,
        'Career Profile export failed its integrity check'
      )
      verifiedMetadata = await temporary.handle.stat({ bigint: true })
      if (!sameFileVersion(flushedMetadata, verifiedMetadata)
        || !timingSafeEqual(Buffer.from(persistedSha256, 'hex'), Buffer.from(expectedSha256, 'hex'))) {
        throw new Error('Career Profile export failed its integrity check')
      }
    } finally {
      await temporary.handle.close()
    }

    const pathMetadata = await fileSystem.lstat(temporaryPath)
    if (pathMetadata.isSymbolicLink()
      || !pathMetadata.isFile()
      || !sameFileVersion(verifiedMetadata, pathMetadata)) {
      throw new Error('Career Profile export failed its integrity check')
    }
    await fileSystem.rename(temporaryPath, target)
    temporaryPath = null
    await directoryHandle?.sync()
  } catch (error) {
    operationError = error
  }

  let cleanupError: unknown
  if (temporaryPath) {
    try {
      await fileSystem.unlink(temporaryPath)
    } catch (error) {
      if (errnoCode(error) !== 'ENOENT') cleanupError = error
    }
  }
  if (directoryHandle) {
    try {
      await directoryHandle.close()
    } catch (error) {
      cleanupError ??= error
    }
  }
  if (operationError && cleanupError) {
    throw new AggregateError([operationError, cleanupError], 'Career Profile export failed and cleanup did not complete')
  }
  if (operationError) throw operationError
  if (cleanupError) throw cleanupError
}

async function readArchiveSafely(
  fileSystem: CareerProfileArchiveFileSystem,
  selectedPath: string
): Promise<Buffer> {
  let fallbackPathMetadata: BigIntStats | null = null
  if (O_NOFOLLOW_FLAG === 0) {
    try {
      fallbackPathMetadata = await fileSystem.lstat(selectedPath)
    } catch {
      throw new Error(REGULAR_ARCHIVE_ERROR)
    }
    assertReadableArchive(fallbackPathMetadata)
  }

  let handle: FileHandle
  try {
    handle = await fileSystem.open(selectedPath, constants.O_RDONLY | O_NOFOLLOW_FLAG)
  } catch {
    throw new Error(REGULAR_ARCHIVE_ERROR)
  }

  try {
    const beforeRead = await handle.stat({ bigint: true })
    const expectedSize = assertReadableArchive(beforeRead)
    if (fallbackPathMetadata) {
      let openedPathMetadata: BigIntStats
      try {
        openedPathMetadata = await fileSystem.lstat(selectedPath)
      } catch {
        throw new Error(CHANGED_ARCHIVE_ERROR)
      }
      if (openedPathMetadata.isSymbolicLink()
        || !openedPathMetadata.isFile()
        || !sameFileVersion(fallbackPathMetadata, beforeRead)
        || !sameFileVersion(openedPathMetadata, beforeRead)) {
        throw new Error(CHANGED_ARCHIVE_ERROR)
      }
    }

    const bytes = await readDescriptorExactly(handle, expectedSize, CHANGED_ARCHIVE_ERROR)
    const afterRead = await handle.stat({ bigint: true })
    if (!afterRead.isFile()
      || afterRead.isSymbolicLink()
      || !sameFileVersion(beforeRead, afterRead)) {
      throw new Error(CHANGED_ARCHIVE_ERROR)
    }
    if (fallbackPathMetadata) {
      let finalPathMetadata: BigIntStats
      try {
        finalPathMetadata = await fileSystem.lstat(selectedPath)
      } catch {
        throw new Error(CHANGED_ARCHIVE_ERROR)
      }
      if (finalPathMetadata.isSymbolicLink()
        || !finalPathMetadata.isFile()
        || !sameFileVersion(afterRead, finalPathMetadata)) {
        throw new Error(CHANGED_ARCHIVE_ERROR)
      }
    }
    return bytes
  } finally {
    await handle.close()
  }
}

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

function mapConnectedAgent(agent: ApiConnectedAgent): ConnectedCareerProfileAgent {
  return {
    active: agent.active,
    agentId: agent.agent_id,
    connectedAt: agent.connected_at,
    disconnectedAt: agent.disconnected_at ?? null,
    displayName: agent.display_name,
    principal: agent.principal,
    trustMode: agent.trust_mode,
    updatedAt: agent.updated_at
  }
}

function mapItemSnapshot(item: ApiProfileItemRecord): CareerProfileItemSnapshot {
  return {
    actorPrincipal: item.actor_principal,
    area: item.area,
    createdAt: item.created_at,
    evidenceIds: item.evidence_ids ?? [],
    itemId: item.item_id,
    itemRevision: item.item_revision,
    provenance: { ...item.provenance },
    reviewStatus: item.review_status,
    updatedAt: item.updated_at,
    value: { ...item.value }
  }
}

function mapEvidence(evidence: ApiSourceEvidenceRecord): CareerProfileEvidence {
  return {
    active: evidence.active,
    byteCount: evidence.byte_count,
    capturedAt: evidence.captured_at ?? null,
    evidenceId: evidence.evidence_id,
    importedAt: evidence.imported_at,
    mediaType: evidence.media_type,
    originalFilename: evidence.original_filename,
    provenance: {
      method: evidence.provenance.method,
      sourceKind: evidence.provenance.source_kind,
      sourceLabel: evidence.provenance.source_label
    },
    sha256: evidence.sha256
  }
}

function mapCompleteProfile(profile: ApiCareerProfileCompleteCurrent): CareerProfileCurrent {
  return {
    authorityEpoch: profile.authority_epoch,
    items: profile.items.map(mapItemSnapshot),
    profileRevision: profile.profile_revision,
    sourceEvidence: profile.source_evidence.map(mapEvidence)
  }
}

function mapContextScope(scope: ApiCareerProfileContextScope): CareerProfileContextScope {
  return {
    agentId: scope.agent_id,
    mode: scope.mode,
    selectedAreas: scope.selected_areas,
    selectedItemIds: scope.selected_item_ids,
    updatedAt: scope.updated_at
  }
}

function mapContextPreview(preview: ApiCareerProfileContextPreview): CareerProfileContextPreview {
  return {
    authorityEpoch: preview.authority_epoch,
    contentHash: preview.content_hash,
    createdAt: preview.created_at,
    profile: mapCompleteProfile(preview.projection),
    profileRevision: preview.profile_revision
  }
}

function mapProposal(proposal: ApiCareerProfileChangeProposal): CareerProfileChangeProposal {
  return {
    after: proposal.after ? mapItemSnapshot(proposal.after) : null,
    agentDisplayName: proposal.agent_display_name,
    agentId: proposal.agent_id,
    baseProfileRevision: proposal.base_profile_revision,
    before: proposal.before ? mapItemSnapshot(proposal.before) : null,
    createdAt: proposal.created_at,
    evidenceIds: proposal.evidence_ids,
    operation: proposal.operation,
    proposalId: proposal.proposal_id,
    proposalSha256: proposal.proposal_sha256,
    reason: proposal.reason,
    reviewReason: proposal.review_reason,
    status: proposal.status,
    targetId: proposal.target_id
  }
}

function mapChangeRevision(revision: ApiProfileHistoryRevision): CareerProfileChangeRevision {
  return {
    actorKind: revision.actor_kind,
    actorPrincipal: revision.actor_principal,
    affectedFields: revision.affected_fields,
    after: revision.after,
    baseProfileRevision: revision.base_profile_revision,
    before: revision.before,
    createdAt: revision.created_at,
    evidenceId: revision.evidence_id,
    itemId: revision.item_id,
    operation: revision.operation,
    profileRevision: revision.profile_revision,
    proposalId: revision.proposal_id,
    reason: revision.reason,
    revisionId: revision.revision_id,
    undoOfRevisionId: revision.undo_of_revision_id,
    undoable: revision.undoable
  }
}

function mapChangeHistory(history: ApiProfileHistory): CareerProfileChangeHistory {
  return {
    profileRevision: history.profile_revision,
    revisions: history.revisions.map(mapChangeRevision)
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

function validateAgentId(value: string): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(value)) throw new Error('Invalid connected agent')
  return value
}

function validateProposalId(value: string): string {
  if (!/^cpp_[A-Za-z0-9_-]{16,64}$/.test(value)) throw new Error('Invalid Career Profile proposal')
  return value
}

function validateChangeRevisionId(value: string): string {
  if (!/^cpv_[A-Za-z0-9_-]{16,64}$/.test(value)) throw new Error('Invalid Career Profile revision')
  return value
}

function validateTrustMode(value: CareerProfileTrustMode): CareerProfileTrustMode {
  if (!trustModes.has(value)) throw new Error('Invalid agent edit mode')
  return value
}

function validateProposalDigest(value: string): string {
  if (!/^[a-f0-9]{64}$/.test(value)) throw new Error('Invalid Career Profile proposal')
  return value
}

function validateValue(value: WorkArrangementValue): WorkArrangementValue {
  if (!modes.has(value.mode) || !strengths.has(value.strength)) throw new Error('Invalid work arrangement')
  const note = value.note === '' ? null : value.note
  if (note && careerProfileAdditionalContextLength(note) > CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT) throw new Error('Work arrangement note is too long')
  return { mode: value.mode, strength: value.strength, note }
}

function validateProfileItemId(value: string): string {
  if (!/^cpi_[A-Za-z0-9_-]{16,64}$/.test(value)) throw new Error('Invalid Career Profile item')
  return value
}

function validateEvidenceId(value: string): string {
  if (!/^cpe_[A-Za-z0-9_-]{16,64}$/.test(value)) throw new Error('Invalid Career Profile Evidence')
  return value
}

function validateEvidenceIds(values: string[], maximum = 100): string[] {
  if (!Array.isArray(values) || values.length > maximum || new Set(values).size !== values.length) {
    throw new Error('Invalid Career Profile Evidence selection')
  }
  return values.map(validateEvidenceId)
}

function validateProfileValue(value: CareerProfileItemMutationRequest['value']): ApiProfileItemMutation['value'] {
  if (!isRecord(value) || typeof value.kind !== 'string' || !profileValueKinds.has(value.kind)) {
    throw new Error('Invalid Career Profile item')
  }
  const serialized = JSON.stringify(value)
  if (Buffer.byteLength(serialized) > 64 * 1024) throw new Error('Career Profile item is too large')
  return JSON.parse(serialized) as ApiProfileItemMutation['value']
}

function validateEvidenceImport(requestBody: CareerProfileEvidenceImportRequest) {
  if (typeof requestBody.originalFilename !== 'string'
    || requestBody.originalFilename.length < 1
    || requestBody.originalFilename.length > 255
    || path.basename(requestBody.originalFilename) !== requestBody.originalFilename) {
    throw new Error('Invalid Evidence filename')
  }
  if (!/^[\w!#$&^.+-]+\/[\w!#$&^.+-]+$/i.test(requestBody.mediaType)) throw new Error('Invalid Evidence file type')
  if (!evidenceKinds.has(requestBody.sourceKind)) throw new Error('Invalid Evidence source type')
  if (typeof requestBody.sourceLabel !== 'string' || requestBody.sourceLabel.trim().length < 1 || requestBody.sourceLabel.length > 500) {
    throw new Error('Invalid Evidence source label')
  }
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(requestBody.contentBase64)) {
    throw new Error('Invalid Evidence content')
  }
  const bytes = Buffer.from(requestBody.contentBase64, 'base64')
  if (bytes.length < 1 || bytes.length > MAX_EVIDENCE_BYTES || bytes.toString('base64') !== requestBody.contentBase64) {
    throw new Error('Evidence files must be between 1 byte and 10 MiB')
  }
  return {
    captured_at: requestBody.capturedAt,
    content_base64: requestBody.contentBase64,
    expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
    extractions: [],
    idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey),
    media_type: requestBody.mediaType,
    original_filename: requestBody.originalFilename,
    provenance: {
      method: 'user_import' as const,
      source_kind: requestBody.sourceKind,
      source_label: requestBody.sourceLabel.trim()
    }
  }
}

function validateArchiveToken(value: string): string {
  if (!/^cpa_[a-f0-9]{32}$/.test(value)) throw new Error('Choose the Career Profile archive again')
  return value
}

async function request(config: DesktopApiConfig, route: string, init?: RequestInit): Promise<Response> {
  return fetch(new URL(route, config.baseUrl), {
    ...init,
    headers: {
      ...jobOsAuthenticatedHeaders(config.deviceToken, config.installationProfileId),
      ...(init?.body ? { 'Content-Type': 'application/json' } : {})
    },
    redirect: 'error'
  })
}

async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({})) as { detail?: string; message?: string }
  return body.detail ?? body.message ?? `Career Profile request failed (${response.status})`
}

export function createMainCareerProfileClient(
  config: DesktopApiConfig,
  native?: CareerProfileNative,
  options: CareerProfileClientOptions = {}
) {
  const configuredArchiveFileSystem = options.archiveFileSystem
  const archiveWriter = options.archiveWriter ?? (
    !configuredArchiveFileSystem && process.platform === 'darwin' ? writeCareerProfileArchiveNative : null
  )
  const archiveFileSystem: CareerProfileArchiveFileSystem = {
    lstat: configuredArchiveFileSystem?.lstat ?? defaultArchiveFileSystem.lstat,
    open: configuredArchiveFileSystem?.open ?? defaultArchiveFileSystem.open,
    rename: configuredArchiveFileSystem?.rename ?? defaultArchiveFileSystem.rename,
    unlink: configuredArchiveFileSystem?.unlink ?? defaultArchiveFileSystem.unlink
  }
  const pendingArchives = new Map<string, {
    bytes: Buffer
    createdAt: number
    filename: string
    timeout: ReturnType<typeof setTimeout>
  }>()
  const discardPendingArchive = (archiveToken: string): void => {
    const archive = pendingArchives.get(archiveToken)
    if (!archive) return
    clearTimeout(archive.timeout)
    pendingArchives.delete(archiveToken)
  }
  const discardAllPendingArchives = (): void => {
    for (const archiveToken of pendingArchives.keys()) discardPendingArchive(archiveToken)
  }
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

  const getCareerProfile = async (): Promise<CareerProfileCurrent> => {
    const response = await request(config, COLLABORATION_ROUTE)
    if (!response.ok) throw new Error(await errorMessage(response))
    return mapCompleteProfile(await response.json() as ApiCareerProfileCompleteCurrent)
  }

  const completeMutationResult = async (response: Response): Promise<CareerProfileMutationResult> => {
    if (response.status === 409) return { status: 'conflict', current: await getCareerProfile() }
    if (!response.ok) throw new Error(await errorMessage(response))
    return { status: 'saved', current: mapCompleteProfile(await response.json() as ApiCareerProfileCompleteCurrent) }
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
    getCareerProfile,
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
    },
    async listConnectedAgents(): Promise<ConnectedCareerProfileAgent[]> {
      const response = await request(config, `${COLLABORATION_ROUTE}/agents`)
      if (!response.ok) throw new Error(await errorMessage(response))
      return (await response.json() as ApiConnectedAgentList).agents.map(mapConnectedAgent)
    },
    async updateConnectedAgentTrustMode(
      agentId: string,
      trustMode: CareerProfileTrustMode
    ): Promise<ConnectedCareerProfileAgent> {
      const response = await request(
        config,
        `${COLLABORATION_ROUTE}/agents/${encodeURIComponent(validateAgentId(agentId))}`,
        { method: 'PATCH', body: JSON.stringify({ trust_mode: validateTrustMode(trustMode) }) }
      )
      if (!response.ok) throw new Error(await errorMessage(response))
      return mapConnectedAgent(await response.json() as ApiConnectedAgent)
    },
    async disconnectConnectedAgent(agentId: string): Promise<ConnectedCareerProfileAgent> {
      const response = await request(
        config,
        `${COLLABORATION_ROUTE}/agents/${encodeURIComponent(validateAgentId(agentId))}`,
        { method: 'DELETE' }
      )
      if (!response.ok) throw new Error(await errorMessage(response))
      return mapConnectedAgent(await response.json() as ApiConnectedAgent)
    },
    async listCareerProfileProposals(): Promise<CareerProfileChangeProposal[]> {
      const response = await request(config, `${COLLABORATION_ROUTE}/proposals`)
      if (!response.ok) throw new Error(await errorMessage(response))
      return (await response.json() as ApiCareerProfileProposalList).proposals.map(mapProposal)
    },
    async decideCareerProfileProposal(
      proposalId: string,
      requestBody: CareerProfileProposalDecisionRequest
    ): Promise<CareerProfileProposalDecisionResult> {
      const body = {
        decision: requestBody.decision,
        expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey),
        proposal_sha256: validateProposalDigest(requestBody.proposalSha256)
      }
      const response = await request(
        config,
        `${COLLABORATION_ROUTE}/proposals/${encodeURIComponent(validateProposalId(proposalId))}/decision`,
        { method: 'POST', body: JSON.stringify(body) }
      )
      if (!response.ok) throw new Error(await errorMessage(response))
      const result = await response.json() as ApiProposalDecisionResult
      return { profileRevision: result.profile.profile_revision, proposal: mapProposal(result.proposal) }
    },
    async getCareerProfileChangeHistory(): Promise<CareerProfileChangeHistory> {
      const response = await request(config, `${COLLABORATION_ROUTE}/history`)
      if (!response.ok) throw new Error(await errorMessage(response))
      return mapChangeHistory(await response.json() as ApiProfileHistory)
    },
    async undoCareerProfileChange(
      revisionId: string,
      requestBody: CareerProfileUndoRequest
    ): Promise<{ profileRevision: number }> {
      const body = {
        expected_profile_revision: validateExistingRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey)
      }
      const response = await request(
        config,
        `${COLLABORATION_ROUTE}/history/${encodeURIComponent(validateChangeRevisionId(revisionId))}/undo`,
        { method: 'POST', body: JSON.stringify(body) }
      )
      if (!response.ok) throw new Error(await errorMessage(response))
      return { profileRevision: (await response.json() as ApiCareerProfileCompleteCurrent).profile_revision }
    },
    async createCareerProfileItem(requestBody: CareerProfileItemMutationRequest): Promise<CareerProfileMutationResult> {
      const body = {
        evidence_ids: validateEvidenceIds(requestBody.evidenceIds),
        expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey),
        value: validateProfileValue(requestBody.value)
      }
      return completeMutationResult(await request(config, `${COLLABORATION_ROUTE}/items`, {
        method: 'POST', body: JSON.stringify(body)
      }))
    },
    async updateCareerProfileItem(
      itemId: string,
      requestBody: CareerProfileItemMutationRequest
    ): Promise<CareerProfileMutationResult> {
      const body = {
        evidence_ids: validateEvidenceIds(requestBody.evidenceIds),
        expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey),
        value: validateProfileValue(requestBody.value)
      }
      return completeMutationResult(await request(
        config,
        `${COLLABORATION_ROUTE}/items/${encodeURIComponent(validateProfileItemId(itemId))}`,
        { method: 'PUT', body: JSON.stringify(body) }
      ))
    },
    async removeCareerProfileItem(
      itemId: string,
      requestBody: CareerProfileRemovalRequest
    ): Promise<CareerProfileMutationResult> {
      const body = {
        expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey)
      }
      return completeMutationResult(await request(
        config,
        `${COLLABORATION_ROUTE}/items/${encodeURIComponent(validateProfileItemId(itemId))}`,
        { method: 'DELETE', body: JSON.stringify(body) }
      ))
    },
    async importCareerProfileEvidence(
      requestBody: CareerProfileEvidenceImportRequest
    ): Promise<CareerProfileMutationResult> {
      return completeMutationResult(await request(config, `${COLLABORATION_ROUTE}/evidence`, {
        method: 'POST', body: JSON.stringify(validateEvidenceImport(requestBody))
      }))
    },
    async removeCareerProfileEvidence(
      evidenceId: string,
      requestBody: CareerProfileRemovalRequest
    ): Promise<CareerProfileMutationResult> {
      const body = {
        expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey)
      }
      return completeMutationResult(await request(
        config,
        `${COLLABORATION_ROUTE}/evidence/${encodeURIComponent(validateEvidenceId(evidenceId))}`,
        { method: 'DELETE', body: JSON.stringify(body) }
      ))
    },
    async getCareerProfileContext(agentId: string): Promise<CareerProfileContextScope> {
      const response = await request(
        config,
        `${COLLABORATION_ROUTE}/agents/${encodeURIComponent(validateAgentId(agentId))}/context`
      )
      if (!response.ok) throw new Error(await errorMessage(response))
      return mapContextScope(await response.json() as ApiCareerProfileContextScope)
    },
    async updateCareerProfileContext(
      agentId: string,
      requestBody: CareerProfileContextUpdateRequest
    ): Promise<CareerProfileContextScope> {
      if (!contextModes.has(requestBody.mode)) throw new Error('Invalid Career Profile access choice')
      if (!Array.isArray(requestBody.selectedAreas)
        || new Set(requestBody.selectedAreas).size !== requestBody.selectedAreas.length
        || requestBody.selectedAreas.some(area => !careerProfileAreas.has(area))) {
        throw new Error('Invalid Career Profile area selection')
      }
      const selectedItemIds = requestBody.selectedItemIds.map(validateProfileItemId)
      if (selectedItemIds.length > 200 || new Set(selectedItemIds).size !== selectedItemIds.length) {
        throw new Error('Invalid Career Profile item selection')
      }
      const body = {
        expected_authority_epoch: validateRevision(requestBody.expectedAuthorityEpoch),
        expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
        idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey),
        mode: requestBody.mode,
        selected_areas: requestBody.selectedAreas,
        selected_item_ids: selectedItemIds
      }
      const response = await request(
        config,
        `${COLLABORATION_ROUTE}/agents/${encodeURIComponent(validateAgentId(agentId))}/context`,
        { method: 'PUT', body: JSON.stringify(body) }
      )
      if (!response.ok) throw new Error(await errorMessage(response))
      return mapContextScope(await response.json() as ApiCareerProfileContextScope)
    },
    async previewCareerProfileContext(agentId: string): Promise<CareerProfileContextPreview> {
      const response = await request(
        config,
        `${COLLABORATION_ROUTE}/agents/${encodeURIComponent(validateAgentId(agentId))}/context/preview`,
        { method: 'POST' }
      )
      if (!response.ok) throw new Error(await errorMessage(response))
      return mapContextPreview(await response.json() as ApiCareerProfileContextPreview)
    },
    async exportCareerProfile(requestBody: CareerProfileExportRequest): Promise<CareerProfileExportResult> {
      if (!native || !evidenceModes.has(requestBody.evidenceMode)) throw new Error('Career Profile export is unavailable')
      const selectedEvidenceIds = validateEvidenceIds(requestBody.selectedEvidenceIds, 1_000)
      if (requestBody.evidenceMode !== 'selected' && selectedEvidenceIds.length > 0) {
        throw new Error('Selected Evidence is only available in the selected export option')
      }
      const response = await request(config, `${COLLABORATION_ROUTE}/export`, {
        method: 'POST',
        body: JSON.stringify({
          evidence_mode: requestBody.evidenceMode,
          expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
          selected_evidence_ids: selectedEvidenceIds
        })
      })
      if (!response.ok) throw new Error(await errorMessage(response))
      const result = await response.json() as ApiCareerProfileExportResult
      if (typeof result.content_base64 !== 'string'
        || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(result.content_base64)
        || !Number.isSafeInteger(result.byte_count)
        || result.byte_count < 1
        || result.byte_count > MAX_ARCHIVE_BYTES
        || typeof result.sha256 !== 'string'
        || !/^[a-f0-9]{64}$/.test(result.sha256)
        || typeof result.filename !== 'string'
        || result.filename.length < 1
        || result.filename.length > 255
        || path.basename(result.filename) !== result.filename
        || !result.filename.toLowerCase().endsWith('.zip')) {
        throw new Error('Career Profile export failed its integrity check')
      }
      const includedEvidenceIds = validateEvidenceIds(result.included_evidence_ids, 1_000)
      const omittedEvidenceIds = validateEvidenceIds(result.omitted_evidence_ids, 1_000)
      const bytes = Buffer.from(result.content_base64, 'base64')
      const digest = createHash('sha256').update(bytes).digest('hex')
      if (bytes.toString('base64') !== result.content_base64
        || bytes.length !== result.byte_count
        || !timingSafeEqual(Buffer.from(digest, 'hex'), Buffer.from(result.sha256, 'hex'))) {
        throw new Error('Career Profile export failed its integrity check')
      }
      const target = await native.chooseExportPath(result.filename)
      if (!target) return { status: 'cancelled' }
      if (archiveWriter) await archiveWriter(target, bytes, result.sha256)
      else await writeArchiveAtomically(archiveFileSystem, target, bytes, result.sha256)
      return {
        status: 'saved',
        byteCount: result.byte_count,
        filename: path.basename(target),
        includedEvidenceIds,
        omittedEvidenceIds,
        sha256: result.sha256
      }
    },
    async chooseCareerProfileArchive() {
      if (!native) throw new Error('Career Profile restore is unavailable')
      const selectedPath = await native.chooseArchivePath()
      if (!selectedPath) return null
      const bytes = await readArchiveSafely(archiveFileSystem, selectedPath)
      const now = Date.now()
      discardAllPendingArchives()
      const archiveToken = `cpa_${randomUUID().replaceAll('-', '')}`
      const timeout = setTimeout(() => pendingArchives.delete(archiveToken), ARCHIVE_SELECTION_TTL_MS)
      timeout.unref?.()
      pendingArchives.set(archiveToken, { bytes, createdAt: now, filename: path.basename(selectedPath), timeout })
      return { archiveToken, byteCount: bytes.length, filename: path.basename(selectedPath) }
    },
    async restoreCareerProfile(requestBody: CareerProfileRestoreRequest): Promise<CareerProfileRestoreResult> {
      const archiveToken = validateArchiveToken(requestBody.archiveToken)
      const archive = pendingArchives.get(archiveToken)
      if (!archive || Date.now() - archive.createdAt > ARCHIVE_SELECTION_TTL_MS) {
        discardPendingArchive(archiveToken)
        throw new Error('That archive selection expired. Choose the Career Profile archive again.')
      }
      if (requestBody.confirmation !== 'RESTORE_CAREER_PROFILE_BASELINE') throw new Error('Confirm the baseline restore exactly')
      const response = await request(config, `${COLLABORATION_ROUTE}/restore`, {
        method: 'POST',
        body: JSON.stringify({
          archive_base64: archive.bytes.toString('base64'),
          confirmation: requestBody.confirmation,
          expected_profile_revision: validateRevision(requestBody.expectedProfileRevision),
          idempotency_key: validateIdempotencyKey(requestBody.idempotencyKey)
        })
      })
      if (!response.ok) throw new Error(await errorMessage(response))
      const result = await response.json() as ApiCareerProfileRestoreResult
      if (result.baseline_created !== true) throw new Error('Career Profile restore did not create a baseline')
      discardPendingArchive(archiveToken)
      return {
        archiveSha256: result.archive_sha256,
        baselineCreated: true,
        profile: mapCompleteProfile(result.profile),
        restoredEvidenceIds: result.restored_evidence_ids,
        unavailableEvidenceIds: result.unavailable_evidence_ids
      }
    }
  }
}
