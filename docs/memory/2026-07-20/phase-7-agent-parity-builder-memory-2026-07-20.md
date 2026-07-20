# JobOS V1 Phase 7 Agent Parity Builder Memory - 2026-07-20

## Builder status

The CLO-53 implementation slice is complete and left uncommitted for Devonte's
integration, review, native/live proof, commit, and push. No service was
restarted, no credential was read or changed, no Hermes turn was submitted, and
no real job-hunter or user data was contacted or mutated.

## Implemented boundary

- Added the Phase 7 OpenAPI domain for authenticated desktop capability
  presence and bounded correlated browser commands/results.
- Added one in-memory single-device capability broker with a 15-second lease,
  five-second heartbeat, immediate offline failure, command IDs, deadlines,
  correlation, and no offline queue.
- Added a FastAPI capability WebSocket. Authentication is the first bounded
  WebSocket frame, never a query parameter; the renderer receives neither the
  token nor the transport.
- Added idempotent browser mutation auditing and concise MCP-origin activity in
  the existing Phase 6 chronology. Snapshot/tab inspection payloads are not
  persisted; mutation results retain only sanitized browser metadata needed for
  replay.
- Extended `BrowserManager` with tab inspection plus fixed semantic snapshot,
  opaque target click/type, and bounded scroll operations. Remote content still
  has no Node/preload/raw IPC. No caller scripts or selectors are accepted.
- Added an Electron-main capability client with bounded reconnect backoff,
  heartbeat, deadline and command validation, fixed BrowserManager dispatch,
  safe error mapping, and sanitized correlated results.
- Completed the thin MCP adapter for jobs, workspace, documents, browser, and
  activity. The original five job tool names remain present and compatible;
  mutation tools accept or generate idempotency keys.
- Added the explicit mapping in
  `docs/architecture/v1-agent-parity-matrix.md`. Geometry and other
  presentation-only behavior remain local.
- Preserved all six accepted Phase 6 MVP debt items; none of their escalation
  triggers was reproduced by Phase 7 work.

## TDD evidence

- Capability API RED failed collection because `jobos_api.capabilities` did not
  exist. GREEN: five focused broker/WebSocket/auth/validation/idempotency cases.
- MCP RED produced three failures for missing mutation idempotency, parity
  methods, and parity tool registrations. GREEN: all three MCP adapter tests.
- Browser RED failed because `BrowserManager.snapshot` did not exist. GREEN:
  ten browser tests including bounded semantic targets and fixed scripts.
- Capability-client RED failed module resolution because the client did not
  exist. GREEN: two focused dispatch/auth/transport tests.
- Combined focused Phase 7/API regression gate passed 86 Python tests; combined
  focused browser/capability desktop gate passed 12 tests.

## Final local verification

Using Node.js 26.5.0, pnpm 10.33.1, Python 3.11.15, and an isolated uv cache:

- `pnpm check` passed lint, Ruff, contract generation, contract/Desktop
  typechecks, 94 desktop tests across 18 files, 262 Python tests, production
  Electron/Vite build, self-contained preload verification, and packaged
  renderer verification.
- `node apps/desktop/scripts/verify-preload-artifact.mjs` passed independently.
- `node scripts/verify-packaged-renderer.mjs` passed independently.
- Contract generation was byte-stable across consecutive generation runs for
  OpenAPI and all four generated TypeScript entry files.
- `git diff --check` passed.
- `pnpm contracts:check` regenerated successfully and then reported the four
  expected generated files as changed against committed Phase 6 `HEAD`. This is
  an expected limitation of the intentionally uncommitted builder handoff, not
  a claim of a green committed-candidate drift check.

## Architecture decisions

- WebSocket authentication uses a first-message device-auth frame because it is
  supported by Electron's built-in WebSocket and keeps secrets out of URLs.
- Capability presence is memory-only. SQLite stores only durable mutation audit,
  idempotent mutation results, and concise visible chronology.
- `desktop_unavailable` is an immediate HTTP failure with recovery guidance;
  `tab_not_found`, `timeout`, `validation`, and bounded execution failures are
  correlated safe command results.
- Browser reads and semantic snapshots are live and not cached/offline queued.
  Browser mutation replays use the existing parameterized `job_events` ledger.
- UI browser controls and capability commands converge on the same validated
  Electron-main `BrowserManager` operations. The capability route adds remote
  authentication/correlation/audit without creating another browser backend.

## Not verified here

- No live Mini API, real Hermes/MCP call, real browser account/session, or real
  JobHunter facade render was exercised.
- No production-native Electron capability WebSocket was connected to a running
  API. Automated tests use in-process/fake sockets and BrowserManager views.
- No application submission or other consequential external action exists in
  the new command set.
- The committed-candidate generated-contract drift check remains for Devonte
  after review/commit.

## Recommended Devonte proof

1. Start a disposable authenticated API/database and production-built desktop
   with a disposable Electron profile; verify presence lease, reconnect, and
   immediate `desktop_unavailable` after closing the desktop.
2. Through MCP only, inspect/select a disposable fixture job, perform an allowed
   status transition, inspect/create/select/navigate a browser tab, snapshot an
   ordinary fixture page, and click/type/scroll its opaque targets.
3. Exercise a disposable facade-backed resume render, register it, and select it
   through Workspace; do not use real user artifacts unless separately approved.
4. Confirm each MCP action appears once in the existing Agent Panel chronology
   with origin/outcome detail and no token, cookie, input value, or raw frame.
5. Verify tab/session continuity and browser independence in the production
   native app, then run the committed-candidate contract drift/full gate before
   commit and push.
