// @vitest-environment node

import { beforeEach, expect, test, vi } from 'vitest'

import type { WorkspaceSnapshot } from '../../shared/contracts.js'

const { workspacePut } = vi.hoisted(() => ({ workspacePut: vi.fn() }))

vi.mock('@jobos/contracts', () => ({
  createJobOsApiClient: vi.fn(() => ({})),
  workspaceGetV1WorkspaceGet: vi.fn(),
  workspacePutV1WorkspacePut: workspacePut
}))

import { createMainWorkspaceClient } from './workspace.js'

const snapshot: WorkspaceSnapshot = {
  revision: 0,
  selectedPreset: 'research' as const,
  layouts: {
    research: { order: ['jobs', 'center', 'agent'], widths: { jobs: 260, center: 760, agent: 350 }, collapsed: [] },
    review: { order: ['jobs', 'center', 'agent'], widths: { jobs: 280, center: 700, agent: 380 }, collapsed: [] },
    'agent-focus': { order: ['jobs', 'center', 'agent'], widths: { jobs: 220, center: 420, agent: 650 }, collapsed: [] }
  },
  selectedJobId: 'job-1',
  activeCenterSurface: 'browser' as const,
  repairedPresets: []
}

beforeEach(() => workspacePut.mockReset())

test('desktop retries an ambiguous workspace save with the same idempotency command', async () => {
  workspacePut
    .mockResolvedValueOnce({ error: new TypeError('fetch failed'), response: undefined })
    .mockResolvedValueOnce({
      response: { status: 200 },
      data: {
        revision: 1,
        selected_preset: snapshot.selectedPreset,
        layouts: snapshot.layouts,
        selected_job_id: snapshot.selectedJobId,
        active_center_surface: snapshot.activeCenterSurface,
        repaired_presets: []
      }
    })

  const saved = await createMainWorkspaceClient({
    baseUrl: 'http://127.0.0.1:8765',
    deviceToken: 'test-token'
  }).save(snapshot)

  expect(saved.revision).toBe(1)
  expect(workspacePut).toHaveBeenCalledTimes(2)
  const firstBody = workspacePut.mock.calls[0]?.[0].body
  const retryBody = workspacePut.mock.calls[1]?.[0].body
  expect(retryBody).toEqual(firstBody)
  expect(firstBody).toMatchObject({ origin: 'user', revision: 0 })
  expect(firstBody.idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
  expect(firstBody).not.toHaveProperty('repaired_presets')
})

test('desktop does not retry a workspace save that received an HTTP response', async () => {
  workspacePut.mockResolvedValueOnce({
    response: { status: 409 },
    error: { detail: 'Workspace revision conflict; current revision is 4' }
  })

  await expect(createMainWorkspaceClient({
    baseUrl: 'http://127.0.0.1:8765',
    deviceToken: 'test-token'
  }).save(snapshot)).rejects.toThrow('Workspace revision conflict; current revision is 4')

  expect(workspacePut).toHaveBeenCalledTimes(1)
})

test('desktop repairs only a known cross-job active artifact conflict', async () => {
  workspacePut
    .mockResolvedValueOnce({
      response: { status: 409 },
      error: { detail: 'Active artifact does not belong to the selected job' }
    })
    .mockResolvedValueOnce({
      response: { status: 200 },
      data: {
        revision: 1,
        selected_preset: snapshot.selectedPreset,
        layouts: snapshot.layouts,
        selected_job_id: snapshot.selectedJobId,
        active_center_surface: snapshot.activeCenterSurface,
        active_artifact_id: null,
        active_artifact_page: 1,
        active_artifact_zoom: 1,
        repaired_presets: []
      }
    })

  const saved = await createMainWorkspaceClient({
    baseUrl: 'http://127.0.0.1:8765',
    deviceToken: 'test-token'
  }).save({
    ...snapshot,
    activeArtifactId: 'art_abcdefghijklmnop',
    activeArtifactPage: 4,
    activeArtifactZoom: 1.5
  })

  expect(saved.activeArtifactId).toBeNull()
  expect(workspacePut).toHaveBeenCalledTimes(2)
  expect(workspacePut.mock.calls[1]?.[0].body).toMatchObject({
    active_artifact_id: null,
    active_artifact_page: 1,
    active_artifact_zoom: 1
  })
  expect(workspacePut.mock.calls[1]?.[0].body.idempotency_key)
    .not.toBe(workspacePut.mock.calls[0]?.[0].body.idempotency_key)
})
