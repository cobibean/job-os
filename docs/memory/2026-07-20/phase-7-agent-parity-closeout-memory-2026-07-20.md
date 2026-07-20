# Phase 7 Agent Parity Closeout Memory - 2026-07-20

## Phase status

Phase 7 is closed as a verified single-user, local-first MVP. JobHunter can operate the same persistent Electron browser and JobOS workbench surfaces used by Cobi through authenticated API-only MCP tools. The locked three-pane product shape remains unchanged:

```text
Job Navigation | Dominant Browser or Document Workspace | Continuous Agent Chat
```

The final candidate passed automated, independent-review, and native runtime gates. Remaining limitations are explicitly bounded in `phase-7-mvp-review-disposition-memory-2026-07-20.md`.

## What shipped

- One authenticated desktop capability WebSocket with a 15-second lease, five-second heartbeat, bounded reconnect backoff, immediate offline failure, and no silent offline queue.
- Typed, correlated browser commands with command IDs, idempotency keys, deadlines, structured safe results, and explicit failures.
- Agent and user share the existing persistent Electron-owned `WebContents`, tabs, cookies, sessions, navigation policy, and visible state. No Playwright instance or hidden browser was added.
- Bounded tab inspect/create/select/close/navigate/back/forward/reload/stop operations.
- Bounded semantic snapshots containing visible page text and up to 100 links, buttons, and inputs.
- Opaque click/type targets stored only in Electron main as candidate index plus role/name/type/disabled fingerprint. No target identity, raw selector, caller script, cookie, credential, or unrestricted Electron object crosses the boundary.
- Bounded scrolling and safe browser result redaction while preserving ordinary URLs.
- API-only MCP parity for jobs, workspace selection, job status, document render/register/select, browser operations, and explicit activity reporting.
- Shared Phase 6 chronology with deterministic activity identities, exact-one replay behavior, and repair of a missing activity row from the durable mutation ledger.
- In-process per-device/idempotency-key serialization for Phase 7 mutations, suitable for the single-process local MVP.
- Server-side artifact existence and selected-job ownership validation, with the MCP check retained as defense in depth.
- Generated OpenAPI and TypeScript contracts plus `docs/architecture/v1-agent-parity-matrix.md`.

## Review-driven hardening

Independent reviews found and drove fixes for:

- unbound `BrowserManager` methods that failed in the real Electron runtime despite mocked tests;
- valid semantic controls being rejected by incorrect visibility handling;
- mutable page-owned target IDs and stale target reuse;
- tab-create and startup-restore load races;
- stale WebSocket callbacks acting on replacement connections;
- concurrent duplicate requests executing a side effect twice;
- mutation replay that could leave visible chronology missing;
- document selection copying response-only fields or accepting a cross-job artifact;
- activity source IDs that could collide when distinct commands reused one idempotency key;
- scroll defaults and safe ordinary URL preservation.

The final scoped review found no remaining implementation security or logic blocker. Its sole final finding was three stale test fixtures for the old activity ID format; those fixtures were updated and the complete suite passed afterward.

## Native runtime acceptance

A disposable proof used a clean API database, local fixture page, production-built Electron main/preload/renderer, disposable Electron profile, and MCP client. It exercised:

```text
MCP -> authenticated JobOS API -> capability broker -> Electron main
    -> persistent browser WebContents -> correlated result -> MCP
```

Observed evidence:

- desktop capability became available with a live lease;
- one disposable job was listed, selected, and status-updated;
- a real browser tab was created on an ordinary local HTTP fixture;
- semantic snapshot preserved its safe URL and exposed bounded controls;
- MCP found the text input, typed into it, clicked the fixture button, and the subsequent snapshot contained the changed page text;
- bounded scroll completed;
- a disposable PDF artifact was rendered/registered and selected in the document workspace;
- the active center surface became `document`;
- chronology contained exactly one browser-click row, one document-selection row, and one explicit activity row;
- no proof credential, cookie, input value, or raw browser frame was persisted in the visible activity evidence.

The disposable fixture, API, Electron profile, and proof processes were stopped after verification. Native screenshot capture was unavailable in the proof environment, so runtime evidence is structured command output rather than a screenshot. No visual redesign was part of Phase 7.

## Final automated verification

Final post-fix execution evidence:

- Desktop: **101 passed** across 18 files.
- Python: **273 passed**.
- Contract and desktop lint: passed with zero warnings/errors.
- Ruff: passed.
- TypeScript contract and desktop typechecks: passed.
- OpenAPI and generated TypeScript contracts: generated successfully.
- Electron/preload build and self-contained preload verification: passed.
- Vite production renderer build: passed; 1,794 modules transformed.
- Packaged-renderer verification: passed.
- `git diff --cached --check`: passed.
- Focused chronology replay and distinct-command/shared-key regressions: passed.

## Primary implementation areas

- `services/api/jobos_api/capabilities.py`
- `services/api/jobos_api/app.py`
- `services/api/jobos_api/state_store.py`
- `services/api/jobos_api/activity.py`
- `services/api/jobos_api/documents.py`
- `services/api/jobos_api/jobs.py`
- `apps/desktop/src/main/capabilityClient.ts`
- `apps/desktop/src/main/browser.ts`
- `apps/desktop/src/main/browserIpc.ts`
- `apps/desktop/src/main/main.ts`
- `apps/desktop/src/shared/contracts.ts`
- `services/mcp/jobos_mcp/jobs.py`
- `services/mcp/jobos_mcp/server.py`
- generated contracts and focused API/MCP/Electron tests.

## Next phase boundary

Do not turn Phase 7 into a generalized browser-automation or device-fleet platform. Reopen its hardening only when one of the documented escalation triggers occurs. The next product phase should build on the verified parity boundary rather than replacing the persistent center browser or reopening the locked workbench layout.