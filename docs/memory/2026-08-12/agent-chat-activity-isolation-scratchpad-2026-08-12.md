# Agent chat activity isolation scratchpad — 2026-08-12

## Goal

Prevent tool/activity events from unrelated Hermes sessions, Discord work, background processes, or ownerless MCP calls from appearing in JobOS Agent Chat.

JobOS is a one-person app. Prefer a minimal fail-closed ownership boundary over enterprise-grade identity machinery.

## Confirmed live evidence

While Devonte used tools from Discord, the live JobOS database recorded the exact labels below against an active JobOS turn belonging to a MacBook resume/cover-letter request:

- `Using skill view`
- `Using search files`
- `Reading file`
- `Using session search`

Recent ownerless MCP conversation events also matched the loose rows shown in Cobi's screenshots:

- `Inspected job`
- `Inspected workspace`

This proves two separate ingress paths.

## Root causes

### 1. Raw Hermes frames bypass session isolation

`services/api/jobos_api/hermes_adapter.py::normalize_frame`

JSON-RPC `method=event` envelopes verify `params.session_id` against `_live_session_id`. Legacy/raw top-level frames do not. If `_active_turn_id` exists, supported raw message/tool/activity frames are normalized and stamped with that active JobOS turn ID.

Required fix: transcript-affecting Hermes events must be valid event envelopes and must carry the exact live JobOS session ID. Raw/unscoped frames fail closed. Preserve request responses and `gateway.ready` handling in `_reader`; preserve verified `session.info` attachment/reconciliation.

### 2. MCP HTTP calls were injecting ownerless chat events

`services/api/jobos_api/app.py`

- `record_agent_read()` appended `activity` with `turn_id=None`.
- `record_mutation()` ensured MCP mutation activity with `turn_id=None`.
- Browser, workspace, and explicit activity routes also appended ownerless activity.

The original repair option was to thread conversation and turn IDs through every MCP call. The cleaner implementation is smaller: verified Hermes tool events are already the transcript projection for JobOS-owned MCP calls, so direct MCP-to-chat mirroring is duplicate plumbing.

Implemented behavior:

- The verified, session-scoped Hermes stream is the single authority for Agent Chat tool activity.
- MCP reads, mutations, browser commands, workspace updates, and explicit activity reports remain in the existing `job_events` audit chronology but do not write `conversation_events` directly.
- This avoids dozens of optional ownership parameters and makes external/background MCP calls incapable of appearing in Agent Chat.

### 3. Renderer promotes invalid ownerless activity

`apps/desktop/src/renderer/hooks/useAgentConversation.ts::projectConversation`

Ownerless `activity` rows are intentionally deduplicated and emitted as top-level `ActivityItem`s. `AgentPanel` then renders them loose.

Required fix: drop ownerless normal activity from Agent Chat. Continue rendering ownerless actionable connection/error/waiting terminal notices where already supported.

## Regression contract

1. Raw top-level Hermes `tool.*`, `message.*`, and activity/status frames are rejected even while a JobOS turn is active.
2. Enveloped events with the exact live session are accepted and assigned to the active JobOS turn.
3. Enveloped foreign-session events are rejected.
4. MCP read/mutation/browser/workspace/activity calls do not append chat events; their normal `job_events` audit record remains.
5. Matching JobOS MCP tool activity reaches chat only through an exact-session Hermes event and is grouped under that turn.
6. External or background MCP calls cannot enter Agent Chat.
7. Renderer drops ownerless activity instead of emitting a loose row.
8. Installed acceptance: while JobOS chat is blank or active, use tools through Discord and confirm no rows appear; then execute a JobOS-owned tool call and confirm it remains grouped under the correct turn.

## Relevant prior debt

Phase 6 explicitly deferred strongest raw Hermes frame/session scoping and stronger activity/turn namespacing. See:

- `docs/memory/2026-07-20/phase-6-mvp-review-disposition-memory-2026-07-20.md`
- Devonte memory wiki: `Session Logs/2026-07-20-jobos-phase-6-shipped.md`, heading `MVP risk decision`

## Safety / current runtime note

At investigation time, the installed API had an active MacBook-origin JobOS turn. Do not restart or interrupt the service until current state is checked and it is safe to do so.
