# JobOS V1 Phase 6 Backend Major Slice — In-Progress Memory — 2026-07-20

## Checkpoint status

- **IN PROGRESS:** backend Tasks 1–5 from the reviewed Phase 6 plan are implemented and locally verified.
- This is not Phase 6 acceptance or closeout. Electron main/preload, renderer UI, native proof, and the harmless live Hermes proof remain deliberately unimplemented/unrun.
- No live Hermes prompt was submitted. No job-hunter database, workspace, artifact root, credential, dashboard configuration, or user session was read or changed by this backend slice.
- No commit or push was made.

## Implemented contracts

- One authenticated current conversation: `GET /v1/conversations/current`.
- Bounded, idempotent message submission: `POST /v1/conversations/current/messages`.
- Serialized turns with durable user message + turn persistence before gateway dispatch.
- Historical per-turn snapshots of authoritative selected job and bounded per-device workspace metadata. Job changes do not create or switch conversations.
- Idempotent cooperative cancellation and append-only retry linkage for failed/interrupted turns.
- Durable normalized event IDs and resumable SSE through `Last-Event-ID` or `after`, including reconnect hints, heartbeat comments, and SQLite gap fill.
- API health remains ready while Hermes is offline and exposes agent connectivity separately.
- Contract identifier is `jobos-v1-phase6-backend`; JobOS state schema is version 7.

## Persistence and trust boundary

- Migration 7 adds the singleton durable conversation/Hermes stored-session identity, turns, and append-only normalized conversation events.
- Message/retry idempotency uses the existing `job_events` mutation/audit ledger, including request hash and original result, rather than an independent ledger.
- Turn context stores only selected job identity and bounded workspace surface/tab/artifact metadata.
- Raw Hermes frames, credentials, cookies, headers, environment values, full stdout/stderr, and dashboard tokens are excluded from SQLite/API contracts.
- Structured detail is depth/item/string bounded and recursively redacts sensitive keys, bearer/token-shaped values, credential paths, and secret-bearing error output while retaining a truthful redaction marker.

## AgentGateway and Hermes adapter

- `AgentGateway` owns start/close, create-or-resume, submit, event stream, interrupt, and connection-state responsibilities.
- `HermesWebSocketGateway` speaks authenticated JSON-RPC over WebSocket and scopes create/resume to profile `job-hunter`, source `jobos`, the configured job-hunter cwd, and `close_on_disconnect: false`.
- The adapter persists `stored_session_id` through JobOS and uses `session.resume` to acquire a fresh live session identity after restart or transport loss.
- Submission acknowledgement is distinct from event-driven completion.
- Normalization covers message start/delta/complete, tool start/progress/complete/output-risk, status, approval/clarification/waiting, file/render activity, top-level error, complete/interrupted/error states, duplicate IDs, malformed frames, and out-of-order sequences.
- Tests inject a fully in-process fake WebSocket transport. The managed sandbox forbids loopback socket binding, so no port or real dashboard was opened.

## Exact implementation files

- Created: `services/api/jobos_api/agent_gateway.py`
- Created: `services/api/jobos_api/conversations.py`
- Created: `services/api/jobos_api/hermes_adapter.py`
- Created: `services/api/jobos_api/activity.py`
- Created: `services/api/jobos_api/redaction.py`
- Modified: `services/api/jobos_api/state_store.py`
- Modified: `services/api/jobos_api/app.py`
- Modified: `services/api/jobos_api/main.py`
- Modified: `services/api/jobos_api/settings.py`
- Modified: `services/api/jobos_api/responses.py`
- Modified: `services/api/pyproject.toml`
- Modified: `uv.lock`
- Created: `services/api/tests/test_agent_contract.py`
- Created: `services/api/tests/test_hermes_adapter.py`
- Created: `services/api/tests/test_activity.py`
- Modified: `services/api/tests/test_state_store.py`
- Modified: `services/api/tests/test_health_contract.py`
- Regenerated: `packages/contracts/openapi.json`
- Regenerated: `packages/contracts/src/generated/index.ts`
- Regenerated: `packages/contracts/src/generated/sdk.gen.ts`
- Regenerated: `packages/contracts/src/generated/types.gen.ts`

## TDD and verification evidence

- RED API/activity collection failed as expected because `agent_gateway.py` and `activity.py` did not exist.
- RED persistence tests failed as expected at schema 6 and missing conversation methods.
- RED adapter collection failed as expected because `hermes_adapter.py` did not exist.
- RED health/OpenAPI tests failed as expected on missing agent connectivity and the Phase 5 contract identifier.
- Focused backend tests pass, including empty/restored conversations, validation, idempotency conflicts/replay, selection/workspace snapshots, one-active-turn serialization, cancellation, retry linkage, offline durability, safe errors, ordered resume, migration/atomicity, fifteen distinct activity actions, normalization/redaction, acknowledgement/completion, reconnect/resume, malformed/duplicate/out-of-order frames, timeout, profile scoping, interruption, and credential non-disclosure.
- Final focused backend gate passes 102 collected tests across state, API contract, activity/redaction, adapter, and health/OpenAPI suites.
- Final full Python gate passes all 179 collected API and MCP tests.
- `ruff check services/api scripts` passes.
- Contracts package lint, build, and TypeScript typecheck pass.
- `uv lock --check --offline` passes with 50 resolved packages.
- `pnpm contracts:generate` passes and regenerates the OpenAPI/TypeScript outputs above.
- `git diff --check` passes.
- Source-tree `pnpm contracts:check` was run and correctly reported those generated files as changed relative to committed Phase 5 bytes. This repository was explicitly required to remain uncommitted; final handoff must report this expected drift-check limitation rather than claim a false clean result.

## Protocol correction — grounded against Hermes 0.18.2

This correction supersedes the adapter-protocol details and verification counts above; the broader backend checkpoint remains in progress.

- Grounding sources read directly from the installed Hermes 0.18.2 tree:
  - `tui_gateway/server.py`
  - `hermes_cli/web_server.py`
  - `apps/shared/src/json-rpc-gateway.ts`
- WebSocket authentication now appends/replaces the URL-encoded `token` query parameter on `/api/ws`, preserves unrelated query parameters, sends no `Authorization` header, omits query data from adapter representation, and raises a generic connection error without retaining a credential-bearing exception cause or context.
- `session.resume` now sends `{session_id: stored_id, profile}` and accepts durable identity from `session_key`, `resumed`, or the create-compatible `stored_session_id`; `session_id` remains the live routing identity. Dedupe/order state resets when a session attaches.
- `prompt.submit` now sends `{session_id, text}`.
- JSON-RPC `event` notifications now unwrap `params.payload`, retain `type` and `session_id` in normalized detail, ignore `gateway.ready` as metadata, and reject events routed to a different live Hermes session.
- Real payload names are supported: tools use `name` and `tool_id`; message/status/error values are read inside payload; blocking states include exact Hermes names `clarify.request`, `approval.request`, `sudo.request`, and `secret.request`.
- Events without synthetic `event_id` or `sequence` remain valid. Dedupe and ordering apply only when those optional fields are present.
- Fifteen tool calls retain fifteen stable activity identities across start/progress/complete updates; an activity projection keyed by `activity_id` yields fifteen completed rows rather than collapsing calls or creating lifecycle-specific identities.
- A new `message.complete` retains the active JobOS turn ID on its normalized completion event, then clears the adapter association. Duplicate durable completion events do not repeat the state transition, and completed tool activities do not complete the enclosing turn. A terminal Hermes `error` follows the same once-only persistence/transition rule for gateway paths that do not emit `message.complete`.
- No Electron/renderer UI was added, no live Hermes prompt was submitted, no credential was read or printed, and no commit or push was made.

### Protocol-correction TDD evidence

- RED `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -q services/api/tests/test_hermes_adapter.py services/api/tests/test_activity.py` failed on the pre-correction production code for query-token auth, resume/submit request shapes, real envelope/payload handling, exact waiting event names, completion association, and real tool names.
- RED `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -q services/api/tests/test_agent_contract.py::test_only_new_terminal_message_event_transitions_turn_status_once` proved completed tool activity and duplicate completion could transition the turn more than once.
- RED credential-context and terminal-error tests separately proved that a suppressed connector exception still retained the authenticated URL and that Hermes error events did not clear/fail the active turn.
- GREEN focused adapter/activity/API/state/health gate: `115 passed in 0.48s`.

### Exact protocol-correction verification

- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest` → `192 passed in 1.56s`.
- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff check services scripts` → `All checks passed!`.
- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv lock --check --offline` → `Resolved 50 packages in 1ms`.
- `PATH=/opt/homebrew/opt/node/bin:$PATH UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache pnpm contracts:generate` → passed; OpenAPI export and four generated contract files completed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts lint` → passed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts build` → passed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts typecheck` → passed.
- `git diff --check` → passed after the checkpoint update.
- The first `uv`-backed focused/contract commands attempted the default user cache and were blocked by the managed sandbox before test collection/generation; reruns used the task-specific writable cache above. No product failure is hidden by those environment-only retries.

## Backend conversation/SSE lifecycle hardening

This hardening pass remains within the Phase 6 backend boundary. It did not add Electron or renderer work, contact live Hermes or other live services, read credentials or JobHunter data, or create a commit/push.

- Public `sanitize_text()` now applies the shared bounded redaction rules to user-facing text and recognizes high-confidence standalone token shapes. Message text is sanitized before the idempotency hash, SQLite turn/event writes, snapshot/API display, retry reuse, and fake/real gateway submission. Normal prose is unchanged. Event summaries also pass through the same boundary.
- `ConversationService.start()` now starts its event consumer even when the initial gateway start fails. A later send can reconnect Hermes, submit, consume the completion, and settle the durable turn.
- Conversation SSE now polls durable local events every 100 ms and maintains a separate 15-second heartbeat clock. Ordered SQLite cursor replay and `Last-Event-ID`/`after` behavior remain unchanged; the minimum sleep is bounded away from zero to avoid a busy loop.
- Cancellation now settles the local turn and appends one actionable interrupted event whether or not the remote interrupt is confirmed. Transport exceptions are suppressed at the trust boundary, never returned, and late completion/waiting frames cannot overwrite or reopen the terminal local interruption. Sequential repeated cancellation remains idempotent.
- A waiting turn moves back to running on a new normalized working status. Waiting/working status events cannot revive a terminal turn; only new terminal assistant/error events settle an active turn.
- API startup explicitly and atomically recovers stale queued/running/waiting turns as interrupted, appending one truthful actionable retry event per recovered turn while preserving every existing transcript/activity row and the singleton conversation identity. Repeated recovery is idempotent and retry creates the existing append-only linked turn.
- Safety tradeoff: after an API process restart, JobOS cannot prove whether a formerly active remote Hermes operation is still executing or safely reattach its exact local turn lifecycle. It therefore favors clearing the permanent local serialization block and truthfully requiring an explicit retry over presenting stale work as still active. This is deliberately not Phase 7/general Hermes session administration.

### Lifecycle-hardening TDD evidence

- Initial RED focused collection failed on the intentionally absent public `sanitize_text` and `conversation_event_source` boundaries.
- Separate RED tests then failed on a standalone `sk-proj-...` credential remaining visible, a late completion changing an interrupted turn to completed, and a late waiting status reopening it. Each received a focused minimal fix before the broader GREEN runs.
- Credential tests prove the raw values are absent from SQLite database bytes, serialized snapshot JSON, and fake gateway submissions while ordinary surrounding prose remains present.
- Offline-start/reconnect, sub-500 ms local SSE delivery with independently timed heartbeat, failed-transport cancellation/idempotence, waiting-to-running, all three stale active statuses, repeated recovery, linked retry, one conversation across relaunch/job context changes, ordered cursor resume, and existing Hermes normalization behavior are covered without a live prompt.

### Exact lifecycle-hardening verification

- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -o addopts='' -q services/api/tests/test_state_store.py services/api/tests/test_agent_contract.py services/api/tests/test_activity.py services/api/tests/test_hermes_adapter.py services/api/tests/test_health_contract.py` → `129 passed in 0.79s`.
- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest` → `206 passed in 1.89s`.
- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff check services/api scripts` → `All checks passed!`.
- `PATH=/opt/homebrew/opt/node/bin:$PATH UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache pnpm contracts:generate` → passed; OpenAPI export and four generated contract files completed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts lint` → passed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts build` → passed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts typecheck` → passed.
- `git diff --check` → passed after the lifecycle-hardening memory update.

## Final Hermes protocol-review corrections

This subsection supersedes the earlier Hermes protocol notes where they differ. It is the final backend-only correction pass grounded in the complete verified review and the installed Hermes gateway source. No desktop UI was edited, no live prompt was submitted, no credential or protected JobHunter state was read, no service was restarted, and no commit or push was made.

- `session.resume` now sends the durable `session_id`, `profile: job-hunter`, `source: jobos`, and `close_on_disconnect: false`. Reconnect tests cover both reuse of the prior live ID and assignment of a new live ID.
- Create/resume response validation fails closed with generic errors when a returned `info.profile_name` is not `job-hunter` or a returned resolved `info.cwd` does not equal the approved resolved JobHunter cwd. Silent Hermes profile/cwd fallback and unsafe-value exclusion are covered.
- A new durable identity is persisted only after `prompt.submit` acknowledgement. The post-ack write is compare-and-set against the pre-attachment identity so a concurrent compression rotation cannot be overwritten. A failed acknowledgement leaves a newly created, potentially unresumable identity out of JobOS persistence.
- Resume RPC code `4007` creates one replacement session. Other RPC codes do not replace the session. RPC error code handling retains no server message, credential, authenticated URL, or raw response in the raised error.
- `session.info` is a non-transcript reconciliation event. Only bounded `running` and `stored_session_id` fields cross the adapter boundary; rotated IDs are persisted, while raw profile/runtime/tool metadata is neither stored nor exposed through the conversation API/renderer contract.
- Transport startup waits for the sessionless JSON-RPC `gateway.ready` event before marking the adapter online or sending session RPCs. The wait uses the configured bounded timeout and fails offline with a credential-safe error.
- Runtime `error` remains terminal for an active local turn. Successful interrupt acknowledgement clears the adapter turn association, while the service continues to settle cancellation locally without requiring `message.complete`.
- Hermes `event_id`, `sequence`, replay, and literal `tool.progress` are not required. Optional `tool.progress` normalization remains compatibility-only. JobOS continues to assign monotonic SQLite event IDs and project tool lifecycle updates by stable `tool_id`/`activity_id`.
- Stable token-query authentication now rejects non-loopback dashboard hosts before connection. Query credentials are removed from adapter representation and connector failures remain cause/context-free at the public boundary.
- Wrong-live-session events are rejected, `gateway.ready` and `session.info` never become transcript rows, and credential/raw-payload exclusion is covered across adapter errors, reconciliation objects, SQLite-backed snapshots, and API serialization.

### Final protocol-review TDD evidence

- RED focused gate produced 15 failures on the pre-correction implementation for resume parameters, response fallback validation, 4007 replacement, readiness timeout, reconciliation handling, durable persistence ordering, non-loopback rejection, and removal of Hermes ID/sequence assumptions.
- A separate RED regression proved that a rotated durable ID arriving during prompt acknowledgement could be overwritten by the older resume result. The compare-and-set persistence fix made that case GREEN without exposing the durable ID in transcript/API data.
- GREEN focused adapter/service/state/API/activity/health gate: `142 passed in 0.80s`.

### Exact final protocol-review verification

- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest services/api/tests/test_hermes_adapter.py services/api/tests/test_agent_contract.py services/api/tests/test_activity.py services/api/tests/test_state_store.py services/api/tests/test_health_contract.py` → `142 passed in 0.80s`.
- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest` → `219 passed in 1.95s`.
- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff check services/api scripts` → `All checks passed!`.
- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv lock --check --offline` → `Resolved 50 packages in 0.92ms`.
- `PATH=/opt/homebrew/opt/node/bin:$PATH UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache pnpm contracts:generate` → passed; OpenAPI export and four generated contract files completed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts lint` → passed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts build` → passed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH pnpm --filter @jobos/contracts typecheck` → passed.
- `PATH=/opt/homebrew/opt/node/bin:$PATH UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache pnpm contracts:check` regenerated successfully, then exited 1 only because the required uncommitted Phase 6 OpenAPI/generated files differ from committed Phase 5 bytes. This is the pre-existing, documented no-commit drift limitation; generation and all package checks are green.
- `git diff --check` → passed before this subsection and was rerun after it.

## Remaining Phase 6 work

1. Task 6 Electron main-process client, strict preload bridge, resumable stream/reconnect/dedupe behavior, and renderer-isolated IPC validation.
2. Task 7 continuous Agent Panel, activity rows, composer, Stop/Retry/offline/waiting states, accessibility, and 1440×1024 renderer screenshots.
3. Task 8 harmless disposable live Mini Hermes prompt proof, including sanitized submit/stream/complete/cancel/resume evidence and before/after protected-state fingerprints. This backend checkpoint submitted no prompt.
4. Task 9 native integrated acceptance: job-context changes in the same conversation, relaunch, fifteen actions through all layers, disconnect recovery, secret review, and visual/native proof.
5. Final full `pnpm check`, committed-candidate contract drift, clean-room/frozen verification, gitleaks, project-memory closeout, and PM review remain future work. No hosted CI result is claimed.

## Handoff constraints

- Keep Hermes/dashboard credentials and raw frames out of renderer IPC, OpenAPI, logs, SQLite, screenshots, and project files.
- Use the protected runtime token only by environment name; never read or print its value.
- Continue using fake gateways/transports for automated tests. Do not convert unit tests into live Hermes turns.
- Preserve the user-owned untracked `.hermes/` plan and `phase-6-readiness-memory-2026-07-20.md` files.
- Do not touch the live job-hunter data/workspaces or expand into Phase 7 browser-command/MCP parity.

## Live-discovered Hermes lazy-create isolation correction

This addendum supersedes the immediate create-response profile-validation statement above only for a lazy `session.create` response. Live authenticated read-only evidence from installed Hermes 0.18.2 established that `session.create(profile="job-hunter", source="jobos", cwd=<approved>, close_on_disconnect=false)` immediately returns `info.lazy: true` with the approved cwd but transient `info.profile_name: "default"`, because that lightweight response reflects the launch context. The matching deferred `session.info` event arrives immediately afterward with `payload.profile_name: "job-hunter"` and the approved cwd; installed source confirms the per-session `profile_home` is assigned before deferred construction.

- A lazy create response still validates its returned cwd immediately, but its transient profile field neither proves nor disproves isolation. The attached live session remains unverified.
- Before `prompt.submit`, the adapter requires a matching-live-session `session.info` event that contains both `profile_name: "job-hunter"` and a resolved cwd equal to the approved resolved cwd. It waits only for the existing bounded request timeout. A missing, incomplete, wrong-profile, or wrong-cwd event fails with the generic `Hermes session isolation could not be verified` error and sends no prompt.
- A deferred event can race immediately behind the create response, before the request coroutine records the returned live ID. The adapter therefore stages at most 16 candidate session entries containing only a boolean verification verdict and the already-allowlisted reconciliation event. It discards all nonmatching candidates as soon as the returned live ID is known; raw profile, cwd, tool, token, and runtime metadata never enter that staging state.
- Verification failure is sticky for the current live session. A later correct event cannot silently rebind a session after a matching wrong or incomplete event. Wrong-session events are ignored. New attachment and transport reconnect paths clear the live ID, active turn association, pending candidates, and verification state.
- Resume and non-lazy responses retain strict immediate validation for every profile/cwd field they return. When both fields are present and correct, the session is immediately verified; otherwise a matching complete `session.info` must verify it before submission. A resume response cannot opt into the lazy-create exception merely by returning `info.lazy: true`.
- `session.info` remains reconciliation-only. Only bounded `running` and `stored_session_id` values can cross the adapter boundary, and the conversation service continues to consume reconciliation without creating a transcript row. Profile, cwd, credentials, and raw metadata are not persisted or rendered.
- This correction touched only the backend adapter, its tests, and this backend memory. It did not touch desktop files, submit a live prompt, restart a service, read or print a credential, commit, or push.

### Lazy-create correction TDD evidence and exact verification

- RED: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest services/api/tests/test_hermes_adapter.py -q` produced 8 failures on the pre-correction adapter. The failures proved the transient lazy profile was rejected, wrong/missing deferred identity did not gate submission, and a wrong deferred event did not invalidate an immediately verified resume.
- GREEN focused adapter/service gate: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest services/api/tests/test_hermes_adapter.py services/api/tests/test_agent_contract.py` → `58 passed in 0.68s`.
- Full Python suite: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest` → `230 passed in 1.99s`.
- Ruff lint: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff check services/api scripts` → `All checks passed!`.
- Repository-wide format check: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff format --check services/api scripts` → exited 1 with seven pre-existing out-of-scope files reported as needing reformatting (`activity.py`, `app.py`, `jobs.py`, `redaction.py`, `workspace.py`, `test_activity.py`, and `test_jobs_contract.py`); `17 files already formatted`. Those user-owned files were not changed.
- Changed-file format check: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff format --check services/api/jobos_api/hermes_adapter.py services/api/tests/test_hermes_adapter.py` → `2 files already formatted`.
- `git diff --check` → passed with no output.

## Live-discovered continuous-session attachment correction

Live JobOS evidence exposed a regression only on the second serialized turn. The first real prompt completed with one terminal tool row and the exact expected response. The second never reached `prompt.submit`: `ConversationService._dispatch()` passed the persisted durable ID back into `create_or_resume_conversation()`, which discarded the already verified live attachment, sent `session.resume`, and then treated Hermes's launch-context `info.profile_name` as a fresh isolation response. That unnecessary reattachment failed before submission.

- When the transport is online, the adapter has a live session whose isolation state is exactly `verified`, and the caller supplies the same non-empty durable ID currently held by the adapter, `create_or_resume_conversation()` now returns that existing `(stored, live)` pair immediately. The check occurs before attachment reset, so it sends no resume RPC and does not clear the live ID, active turn association, verification event/state, or staged pending state.
- The identity comparison uses the adapter's current `_stored_session_id`. Because accepted `session.info` reconciliation updates that field immediately, a rotated durable ID is eligible for reuse while an obsolete pre-rotation ID is not.
- Missing or different caller IDs, a stopped transport, a new adapter process, and `unverified` or sticky `failed` isolation state bypass the fast path. They retain the existing create/resume behavior, reset semantics, deferred isolation proof, and fail-closed submission gate.
- The service-level regression test serializes two completed turns through the real adapter with a fake authenticated transport. It proves the exact RPC sequence is one `session.create` followed by two `prompt.submit` calls, with no `session.resume`; both exact assistant responses persist. Focused tests separately cover the rotated-ID comparison without state reset, missing/different IDs, unverified/failed isolation, reconnect, and a new adapter instance.
- Runtime token/tool metadata injected into the fake `session.info` event remains absent from the SQLite-backed snapshot and adapter representation. No credential or raw live payload was read or written.
- This correction touched only the backend Hermes adapter, adapter/service tests, and this backend memory. It did not touch desktop files, submit a prompt, restart a service, commit, or push.

### Continuous-session correction TDD evidence and exact verification

- RED: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -o addopts='' -q services/api/tests/test_hermes_adapter.py -k 'two_serialized or fast_path or isolation_cannot or missing_or_different'` → `2 failed, 4 passed, 38 deselected`. The failures showed the second turn issued `session.resume` instead of `prompt.submit` and rotated identity reuse fell back to the stale resume response.
- GREEN focused regression gate: the same command → `6 passed, 38 deselected in 0.07s`.
- GREEN focused adapter/service gate: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -o addopts='' -q services/api/tests/test_hermes_adapter.py services/api/tests/test_agent_contract.py` → `64 passed in 0.70s` on the final formatted files.
- Full Python suite: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest` → `236 passed in 1.96s`.
- Ruff lint: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff check services/api scripts` → `All checks passed!`.
- Changed-file Ruff formatting normalized the edited adapter test; the adapter was already formatted. The final changed-file format check passed for both files.
- `git diff --check` → passed with no output.

## Final live-discovered Hermes profile-reporting correction

Authenticated raw protocol evidence for one JobHunter session established this exact sequence: the lazy `session.create` response reported `profile_name: "default"` with the approved cwd; the matching deferred pre-turn `session.info` reported `profile_name: "job-hunter"`, the approved cwd, and `running: false`; the first prompt completed successfully under the JobHunter profile; then post-turn reconciliation for the same live session reported `profile_name: "default"`, the same approved cwd, and `running: false`.

Installed Hermes source explains the apparent profile reversal. The live session's `profile_home` is immutable, and deferred construction emits from inside the profile override. Generic post-turn `_session_info`, however, evaluates `_current_profile_name()` in the launch context, so its `default` value does not describe a profile rebind and cannot invalidate the earlier deferred isolation proof.

- Isolation verification is now sticky for the lifetime of an attached live session. Once a matching `session.info` verifies both `job-hunter` and the approved resolved cwd, later matching-session `session.info` events cannot revoke or rebind that proof from profile/cwd metadata. They can still contribute only allowlisted `running` and bounded `stored_session_id` reconciliation, including a rotated durable ID.
- The sticky transition is enforced both after attachment and when frames race with response handling. A verified create/resume result cannot be downgraded by a staged launch-context reconciliation frame.
- A new attachment, reconnect, or live routing identity still resets isolation to `unverified` and requires strict proof again. While unverified, missing or incorrect profile/cwd metadata fails closed and prevents the first `prompt.submit`; wrong-live-session events remain ignored. A failed proof remains sticky until a new attachment.
- The service regression now reproduces the raw order: lazy default create response, verified deferred JobHunter event, first completion, same-live-ID post-turn default reconciliation, then a successful second prompt with no `session.resume`. Focused tests retain wrong-initial-deferred rejection and reconnect revalidation, and separately prove rotated-ID reconciliation without profile/cwd disclosure.
- Profile and cwd remain adapter-only verification inputs. They are not added to reconciliation detail, SQLite state, API contracts, or renderer data.
- This correction changed only the backend Hermes adapter, backend adapter/service tests, and this backend memory. It made no desktop changes, submitted no prompt, restarted no service, and created no commit or push.

### Final profile-reporting correction TDD evidence

- RED focused gate: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -o addopts='' -q services/api/tests/test_hermes_adapter.py -k 'two_serialized_service_prompts or launch_context_session_info or lazy_create_missing_or_wrong or reconnect_resets'` → `2 failed, 6 passed, 36 deselected`. The exact service regression attempted `session.resume` for the second turn, and the direct same-live-session case rejected submission after post-turn `profile_name: "default"`.
- GREEN focused regression gate: the same command → `8 passed, 36 deselected in 0.10s`.
- GREEN focused adapter/service gate: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -o addopts='' -q services/api/tests/test_hermes_adapter.py services/api/tests/test_agent_contract.py` → `64 passed in 0.69s`.
- Full Python suite: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest` → `236 passed in 1.99s`.
- Ruff lint: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff check services/api scripts` → `All checks passed!`.
- Changed-file Ruff format: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff format services/api/jobos_api/hermes_adapter.py services/api/tests/test_hermes_adapter.py` → `2 files left unchanged`; the subsequent `ruff format --check` reported `2 files already formatted`.
- `git diff --check` → passed with no output.

## Final API-relaunch Hermes resume correction

Live authenticated resume evidence for a persisted JobHunter session established the
same launch-context behavior already observed for lazy create. An exact
`session.resume(stored_id, profile="job-hunter", source="jobos",
close_on_disconnect=false)` returned the approved resolved cwd but immediate
`info.profile_name: "default"`. The same live session ID then emitted deferred
`session.info` with `profile_name: "job-hunter"`, the approved cwd, and
`running: false`. The immediate profile is therefore launch context, not proof that
the resumed session was rebound to the default profile. This section supersedes the
earlier statement that resume retains strict immediate profile rejection.

- Immediate attachment validation is now uniform for create and resume. Any returned
  cwd is resolved and must equal the approved resolved cwd; a present wrong or
  unresolvable cwd still throws the generic unsafe-session error immediately.
- An immediate `profile_name: "job-hunter"` plus the approved cwd verifies the live
  attachment directly. Any other immediate profile, or incomplete immediate
  identity metadata, leaves the new attachment unverified instead of throwing.
- Before the first `prompt.submit`, an unverified attachment must receive a bounded,
  matching-live-ID `session.info` containing both `profile_name: "job-hunter"` and
  the approved cwd. Wrong, incomplete, or missing deferred metadata fails closed and
  sends no prompt. Events for another live ID do not affect the candidate session.
- Isolation remains sticky only after direct or deferred verification for that live
  ID. Every new attachment and transport reconnect still resets the live routing ID,
  active turn association, pending candidates, verification event, and isolation
  state.
- The exact service regression starts with a durable stored session in SQLite,
  receives immediate default/correct-cwd resume metadata followed by deferred
  JobHunter/correct-cwd metadata, submits one prompt through the fake transport,
  consumes `message.complete`, and proves the turn and exact assistant response are
  persisted as completed. It also proves the resume parameters and fresh live ID
  routing.
- Resume controls prove a wrong immediate cwd throws; wrong, incomplete, or missing
  deferred info blocks submission; and a wrong-session event is ignored before the
  matching verification event. Existing coverage continues to prove 4007
  session-not-found replacement, rotated durable IDs, reconnect reset, direct
  verification, sticky post-verification reconciliation, safe errors, and exclusion
  of raw profile/cwd/runtime metadata from persistence and rendering.
- This final correction changed only the backend Hermes adapter, backend adapter and
  service tests, and this backend memory. It did not touch desktop files, submit a
  live prompt, restart a service, read or print a credential, commit, or push.

### Final resume correction TDD and verification evidence

- RED focused resume gate:
  `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -o addopts='' -q services/api/tests/test_hermes_adapter.py -k 'persisted_resume_waits or resume_still_rejects or resume_wrong_or_missing or resume_ignores_wrong_session'`
  → `6 failed, 1 passed, 44 deselected`. All six failures stopped at the old
  immediate resume profile rejection; the wrong-immediate-cwd control passed.
- GREEN focused resume gate: the same command →
  `7 passed, 44 deselected in 0.09s`.
- GREEN focused adapter/service gate after formatting:
  `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest -o addopts='' -q services/api/tests/test_hermes_adapter.py services/api/tests/test_agent_contract.py`
  → `71 passed in 0.76s`.
- Full Python suite: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run pytest` →
  `243 passed in 2.05s`.
- Ruff lint: `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache uv run ruff check services/api scripts`
  → `All checks passed!`.
- Changed-file Ruff formatting reformatted the adapter test only; the adapter was
  unchanged. The final changed-file format check reported `2 files already formatted`.
- `git diff --check` → passed with no output after this memory update.

## Independent-review blocking-fix checkpoint (not closeout)

The Phase 6 independent review identified credential-string, terminal-race,
connectivity, transport-loss, and restart-recovery blockers. This checkpoint fixes
only those blockers and remains an in-progress memory rather than final closeout.
No live prompt was submitted, no external service was restarted, and no commit or
push was created.

- Conservative text sanitization now removes ordinary-string `Cookie:`,
  `Set-Cookie:`, `Authorization:`, and `Proxy-Authorization:` header values,
  including Basic credentials, before gateway submission, SQLite persistence, API
  serialization, or renderer delivery.
- Terminal turn settlement is one SQLite transaction: a conditional update from an
  active state and the matching terminal event insert commit together. Cancel,
  completion, dispatch failure, and transport failure therefore have one winner and
  one durable terminal event.
- Hermes emits connection transitions through its normalized event boundary. The
  service persists safe connection markers into the ordered conversation cursor,
  while snapshots continue to report the live gateway state independently of API
  transport health.
- A mid-turn socket loss emits a bounded failed terminal event, clears SQLite active
  state, and atomically records a durable recovery quarantine. Send or Retry must
  reconnect, resume the stored session, and confirm remote interruption before a new
  turn can be created; failure remains safely blocked without raw transport detail.
- Startup recovery no longer terminalizes a persisted active turn before remote
  cleanup. With an active turn and stored session it reattaches and interrupts first.
  If confirmation fails, the turn remains active/waiting with one retryable recovery
  marker, new work remains unavailable, and Stop safely retries the fixed recovery
  operation. Active turns without a stored session retain the prior local restart
  recovery because no durable remote identity exists.

### Review-fix TDD and verification evidence

- Initial backend RED gate: the combined focused command reported `7 failed, 71
  deselected`; one failure was a test-only JSON serialization mistake corrected
  before production changes. The substantive failures proved header leakage, absent
  remote restart interruption/quarantine, cancel/completion overwrite, dispatch
  overwrite, absent connectivity/transport terminal events, and no recovery API.
- Additional transport-quarantine RED: focused Python reported `1 failed, 25
  deselected` because Retry did not confirm remote interruption.
- Final focused backend GREEN:
  `uv run pytest -o addopts='' -q services/api/tests/test_agent_contract.py
  services/api/tests/test_hermes_adapter.py services/api/tests/test_state_store.py` →
  `163 passed in 0.92s`.
- Full Python suite: `UV_CACHE_DIR=/tmp/jobos-phase6-review-uv uv run pytest -q` →
  all `251` collected tests passed.
- Ruff: `UV_CACHE_DIR=/tmp/jobos-phase6-review-uv uv run ruff check services
  scripts` → `All checks passed!`.
- Root lint, typecheck, and production build all passed with the same isolated uv
  cache. Contract generation performed during typecheck/build was byte-stable: the
  before/after SHA manifests for OpenAPI and all generated TypeScript files were
  identical.
- `git diff --check` passed with no output.

## Final bounded independent-review fix checkpoint (not closeout)

This checkpoint fixes only the remaining Phase 6 review blockers. It did not submit
a live prompt, restart an external service, commit, push, or begin Phase 7.

- Standalone `Basic <base64>` credentials now use the same conservative redaction
  boundary as header credentials and token shapes before user-message persistence,
  gateway submission, event persistence, or API delivery.
- Redaction has explicit purpose bounds: accepted user text remains bounded at
  12,000 characters, event summaries at 500, and generic structured-detail strings
  at 1,000. A valid near-12,000-character prompt is submitted and persisted at its
  full sanitized length instead of being truncated to the detail bound.
- Renderer-event detail recursively removes live/durable session IDs, profile, cwd,
  URL, and raw transport fields. `session.info` durable-ID reconciliation remains a
  private gateway-to-service control event and creates no transcript/API row; only
  the private conversation metadata column retains the durable recovery identity.
- Dispatch now has explicit attachment and submission phases. After attachment, one
  immediate SQLite transaction verifies the same turn is still running with no
  cancellation request and persists/reconciles the attached durable ID. Only then
  can submission begin.
- Cancellation records intent durably before waiting for the in-process submission
  gate. Cancellation during attachment prevents submission; cancellation after a
  submission may have begun waits for the acknowledgement boundary and then safely
  interrupts. Barrier tests use `asyncio.Event` only and retain one terminal winner.
- Attachment failure occurs before any prompt can start and terminalizes without a
  recovery quarantine. Once submission is entered, any ambiguous failure has a
  previously persisted durable ID and atomically records recovery quarantine.
  Reconciled/rotated IDs are accepted only when the current durable value matches the
  attached adapter identity, preventing stale replacement.

### Final bounded backend TDD and verification evidence

- Initial focused RED could not collect the backend cases because the intentionally
  required `sanitize_user_text` boundary did not exist. The independent desktop RED
  ran separately. After adding that boundary, the focused set exposed the old
  post-ack persistence, attachment/cancel submission, routing-detail, and terminal
  arbitration behavior before turning green.
- Focused backend GREEN:
  `UV_CACHE_DIR=/tmp/jobos-phase6-review-cache uv run pytest -o addopts='' -q
  services/api/tests/test_activity.py services/api/tests/test_agent_contract.py
  services/api/tests/test_hermes_adapter.py` -> `91 passed in 0.86s`.
- Final full Python suite:
  `UV_CACHE_DIR=/tmp/jobos-phase6-review-cache uv run pytest` -> `256 passed in
  2.18s`.
- Root lint passed: contracts and desktop oxlint plus Ruff reported `All checks
  passed!`. Root typecheck and production build passed, including generated-contract
  build/typecheck, both desktop TypeScript projects, Electron/preload compilation,
  1,794-module Vite production build, and packaged-renderer verification.
- Standalone preload and packaged-renderer verifier commands passed with no output.
  Contract generation was byte-stable across OpenAPI and all 16 generated contract
  files; the complete 17-file before/after SHA-1 manifests were identical.
- Repository-wide Ruff format check reported only four pre-existing untouched files
  (`activity.py`, `jobs.py`, `workspace.py`, and `test_jobs_contract.py`) as needing
  formatting; all 20 other checked files were formatted. `git diff --check` passed.
