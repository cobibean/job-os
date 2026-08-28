import { randomUUID } from 'node:crypto'

import {
  conversationSaveDocumentViewV1ConversationsConversationIdWorkspaceDocumentPut,
  createJobOsApiClient,
  workspaceGetV1WorkspaceGet,
  workspacePutV1WorkspacePut
} from '@jobos/contracts'
import type { WorkspaceSnapshotResponse } from '@jobos/contracts'

import type { AgentSessionJobContext, BrowserTabMetadata, LayoutPreset, PanelId, WorkspaceSnapshot } from '../../shared/contracts.js'
import type { DesktopApiConfig } from '../app/runtime/desktopApiConfig.js'

interface ApiResult<T> { data?: T; error?: unknown; response?: Response }

function unwrap<T>(result: ApiResult<T>, fallback: string): T {
  if (result.response?.status === 200 && result.data !== undefined) return result.data
  const detail = typeof result.error === 'object' && result.error && 'detail' in result.error
    ? String(result.error.detail)
    : fallback
  throw new Error(detail)
}

function fromApi(value: WorkspaceSnapshotResponse): WorkspaceSnapshot {
  const presets: LayoutPreset[] = ['research', 'review', 'agent-focus']
  const layouts = Object.fromEntries(presets.map(preset => {
    const layout = value.layouts[preset]!
    return [preset, {
      order: layout.order as PanelId[],
      widths: layout.widths as Record<PanelId, number>,
      collapsed: layout.collapsed as PanelId[]
    }]
  })) as WorkspaceSnapshot['layouts']
  return {
    revision: value.revision,
    selectedPreset: value.selected_preset,
    layouts,
    selectedJobId: value.selected_job_id,
    activeCenterSurface: value.active_center_surface,
    repairedPresets: value.repaired_presets ?? [],
    browserTabs: (value.browser_tabs ?? []).map(tab => ({
      tabId: tab.tab_id,
      url: tab.url,
      title: tab.title ?? 'New tab',
      faviconUrl: tab.favicon_url ?? null,
      associatedJobId: tab.associated_job_id ?? null
    })) satisfies BrowserTabMetadata[],
    activeBrowserTabId: value.active_browser_tab_id ?? null,
    repairedBrowser: value.repaired_browser ?? false,
    browserRepairReasons: value.browser_repair_reasons ?? [],
    activeArtifactId: value.active_artifact_id ?? null,
    activeArtifactPage: value.active_artifact_page ?? 1,
    activeArtifactZoom: value.active_artifact_zoom ?? 1,
    activeTopLevelWorkspace: value.active_top_level_workspace ?? value.selected_preset,
    browseMode: value.browse_mode ?? 'list',
    browseFocusJobId: value.browse_focus_job_id ?? null,
    browseQuery: value.browse_query ?? '',
    browseStatusGroup: value.browse_status_group ?? '',
    browseSortMode: value.browse_sort_mode ?? 'manual',
    browseRailWidth: value.browse_rail_width ?? 292
  }
}

function toApi(snapshot: WorkspaceSnapshot, idempotencyKey: string) {
  return {
    revision: snapshot.revision,
    origin: 'user' as const,
    idempotency_key: idempotencyKey,
    selected_preset: snapshot.selectedPreset,
    layouts: snapshot.layouts,
    selected_job_id: snapshot.selectedJobId,
    active_center_surface: snapshot.activeCenterSurface,
    browser_tabs: (snapshot.browserTabs ?? []).map(tab => ({
      tab_id: tab.tabId,
      url: tab.url,
      title: tab.title,
      favicon_url: tab.faviconUrl,
      associated_job_id: tab.associatedJobId
    })),
    active_browser_tab_id: snapshot.activeBrowserTabId ?? null,
    active_artifact_id: snapshot.activeArtifactId ?? null,
    active_artifact_page: snapshot.activeArtifactPage ?? 1,
    active_artifact_zoom: snapshot.activeArtifactZoom ?? 1,
    active_top_level_workspace: snapshot.activeTopLevelWorkspace ?? snapshot.selectedPreset,
    browse_mode: snapshot.browseMode ?? 'list',
    browse_focus_job_id: snapshot.browseFocusJobId ?? null,
    browse_query: snapshot.browseQuery ?? '',
    browse_status_group: snapshot.browseStatusGroup ?? '',
    browse_sort_mode: snapshot.browseSortMode ?? 'manual',
    browse_rail_width: snapshot.browseRailWidth ?? 292
  }
}

export function createMainWorkspaceClient(config: DesktopApiConfig) {
  const client = createJobOsApiClient(
    config.baseUrl,
    config.deviceToken,
    config.installationProfileId
  )
  return {
    async get(): Promise<WorkspaceSnapshot> {
      return fromApi(unwrap(await workspaceGetV1WorkspaceGet({ client }), 'Workspace unavailable'))
    },
    async save(snapshot: WorkspaceSnapshot): Promise<WorkspaceSnapshot> {
      const body = toApi(snapshot, randomUUID())
      let result
      let retried = false
      try {
        result = await workspacePutV1WorkspacePut({ client, body })
      } catch {
        retried = true
        result = await workspacePutV1WorkspacePut({ client, body })
      }
      if (!retried && result.response === undefined && result.error !== undefined) {
        result = await workspacePutV1WorkspacePut({ client, body })
      }
      if (
        body.active_artifact_id !== null
        && result.response?.status === 409
        && typeof result.error === 'object'
        && result.error !== null
        && 'detail' in result.error
        && result.error.detail === 'Active artifact does not belong to the selected job'
      ) {
        result = await workspacePutV1WorkspacePut({
          client,
          body: {
            ...body,
            idempotency_key: randomUUID(),
            active_artifact_id: null,
            active_artifact_page: 1,
            active_artifact_zoom: 1
          }
        })
      }
      return fromApi(unwrap(result, 'Workspace save failed'))
    },
    async saveDocumentView(conversationId: string, artifactId: string | null, page: number, zoom: number): Promise<AgentSessionJobContext> {
      const result = unwrap(await conversationSaveDocumentViewV1ConversationsConversationIdWorkspaceDocumentPut({
        client,
        path: { conversation_id: conversationId },
        body: { active_artifact_id: artifactId, active_artifact_page: page, active_artifact_zoom: zoom }
      }), 'Document view save failed')
      return {
        selectedJobId: result.job_context.selected_job_id ?? null,
        activeArtifactId: result.job_context.active_artifact_id ?? null,
        activeArtifactPage: result.job_context.active_artifact_page ?? 1,
        activeArtifactZoom: result.job_context.active_artifact_zoom ?? 1
      }
    }
  }
}
