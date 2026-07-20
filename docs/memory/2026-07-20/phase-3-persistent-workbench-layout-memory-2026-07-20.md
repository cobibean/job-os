# JobOS V1 Phase 3 Persistent Workbench Layout Memory - 2026-07-20

## Session summary

- Implemented Linear `CLO-49` only: the three locked presets, continuous pointer resizing, keyboard resizing, collapse/reopen, restrained reorder, active-preset reset, and per-device restoration.
- Added one authenticated Workspace snapshot Interface shared by the Electron renderer and JobOS API. Snapshots save preset, per-preset geometry, selected job, and active center surface together.
- Kept the accepted Phase 2 job navigator and the center/agent placeholder surfaces mounted across layout changes.
- Did not touch job-hunter, the Mac Mini runtime, Hermes, browser implementation, documents, or agent chat.

## Decisions made

- Persist one complete JSON snapshot per explicit `JOBOS_DEVICE_ID` in the JobOS-owned SQLite store. Device identity is independent from the revocable device token so credential rotation does not lose layout state.
- Use optimistic monotonic revisions. A stale write returns `409`; the renderer fetches the current revision and retries its latest coherent snapshot.
- Keep the three primary surface wrappers stable and change presentation with CSS order, basis, and hidden state. Reordering does not reconstruct content surfaces.
- Use 220 px as the renderer minimum for every primary panel. The API accepts bounded compatible widths and repairs malformed preset geometry to canonical defaults.
- Reset replaces only the selected preset's order, widths, and collapse list. It retains the selected preset, other preset adjustments, selected job, and active surface/content state.
- Research activates the browser placeholder, Review activates the document placeholder, and Agent Focus preserves the current center surface while making chat dominant.

## Recovery behavior

- Startup restores the last coherent per-device snapshot and overlays the authoritative Phase 2 selected job, preventing stale layout data from erasing valid selection state.
- Corrupt or unknown geometry repairs only the affected preset. Other presets and job selection survive.
- Every collapsed panel leaves a named `Reopen ...` control with semantic expanded state.
- If a save fails, the visible local layout remains and an accessible status message reports the failure.

## Files created or changed

- Workspace API/state: `services/api/jobos_api/workspace.py`, `state_store.py`, `app.py`, device settings/auth, schema migration 4, API tests, and generated contracts.
- Electron boundary: `apps/desktop/src/main/workspace.ts`, strict main/preload wiring, and shared contracts.
- Renderer: `workspaceLayout.ts`, `useWorkspace.ts`, `WorkbenchLayout.tsx`, `App.tsx`, locked-direction styles, and focused renderer/accessibility tests.

## Commands and verification

- Pinned runtime: Node.js 26.5.0 via the repository-required runtime, pnpm 10.33.1, and Python 3.11.15.
- Final local `pnpm check` passed: lint, generated contract drift, TypeScript, 22 renderer/Electron tests, 22 Python tests, production Electron build, and packaged-renderer verification.
- Frozen clean room `/tmp/jobos-phase3-final-clean.wLDTr2` passed `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, and the full `pnpm check`.
- Automated behavior covers canonical dominance, minimum widths, keyboard resizing, every-panel collapse/reopen, keyboard and drag reorder, insertion preview, selected-preset reset, revision conflicts, corrupt-state repair, restoration, and stable surface DOM identity.
- Production renderer pointer proof: `output/playwright/jobos-phase3-pointer-resize.png` at 1440 x 1024; live accessibility state reported `Job navigation 360 pixels`.
- Production renderer keyboard proof: `output/playwright/jobos-phase3-keyboard-collapse.png`; Arrow-key resize plus tab/Enter collapse removed the center and exposed `Reopen Center workspace`.
- Native Electron proof: `output/playwright/jobos-phase3-native-electron-connected.png`, launched from `dist/main/main.js` against a disposable authenticated API on `127.0.0.1:8767`. The temporary API was stopped; this was not a Mini deployment.
- The only browser console error was a static proof-server favicon `404`; the application emitted no runtime error.

## Gotchas and constraints

- Author CSS must retain `.workbench-panel[hidden] { display: none; }`; without it, flex display overrides the browser's native hidden rule.
- The source shell defaulted to Node 22, so verification explicitly invoked Node 26.5.0.
- A clean-room `uv sync` must use `--all-packages` and exclude the source `.venv`; otherwise the copied workspace can inherit or omit the member-package tool environment.
- Preserve the unrelated `docs/planning/.DS_Store` modification. It is not part of Phase 3.
- Preserve all accepted Phase 1/2 behavior and leave later-phase controls disabled.

## Explicit defers

- Phase 4 owns real `WebContentsView` browser state, tabs, navigation, downloads, permissions, and session continuity.
- Phase 5 owns document registry and faithful artifact preview.
- Phase 6+ owns Hermes conversation/activity and browser capability parity.
- No arbitrary dashboard composition, additional presets, job-hunter changes, or Mac Mini service work was added.

## PM review

- Implementation is complete, committed evidence should be reviewed against `CLO-49` before closing it.
- The implementor leaves `CLO-49` in `Building` and does not transition or close it.

## PM correction - 2026-07-20

### Superseding state-ownership and continuity notes

- The initial statement that a layout snapshot save persists selected job together with geometry is inaccurate and is superseded here. Phase 2 `job_workspace.selected_job_id` is the sole selection authority. A Phase 3 layout command may carry the selection it observed, but the server ignores that value for ownership, reads the current authoritative selection inside the same `BEGIN IMMEDIATE` transaction as the layout revision write, and returns/stores the coherent snapshot view without updating job selection.
- The exact stale race is covered: select `job-1`, read its layout snapshot, select `job-2` through the user/MCP selection path, then save the stale layout snapshot. The save response, Job Workspace state, and restored device snapshot all remain on `job-2`.
- Startup restoration now journals layout operations made while `workspace.get()` is pending, applies them over the restored snapshot, and persists the reconciled result against the restored revision. The first resize, reset, preset, collapse, or reorder action cannot be silently replaced by hydration.
- The initial CSS-order decision is also superseded. Persisted panel order now drives keyed React DOM order directly. The jobs, center, and agent wrappers keep stable keys and DOM identities while visual order, focus traversal, and screen-reader reading order move together.
- Layout controls occupy a compact reserved rail above each panel rather than overlaying the existing job ordering, center tabs, or agent-context controls. Collapsed-panel recovery uses a separate reserved row.
- Collapse and reopen now use stable `workbench-panel-*` IDs, correct `aria-controls` and `aria-expanded` relationships, and deterministic focus transfer from the disappearing collapse control to its recovery control and back to the restored panel control.

### Correction verification

- Focused API/state regressions and renderer accessibility/continuity regressions passed, including the stale selection race and delayed-hydration mutation.
- Pinned Node.js 26.5.0 `pnpm check` passed with 25 renderer/Electron tests and 24 Python tests, plus lint, generated contracts, TypeScript, production Electron build, and packaged-renderer verification.
- Frozen clean room `/tmp/jobos-phase3-correction-clean.I2om8h` passed `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, and the complete pinned `pnpm check`.
- Production renderer proof at 1440 x 1024 exercised continuous pointer resize, keyboard reorder, DOM-order equivalence, keyboard collapse focus transfer, reopen focus return, and unobstructed existing panel headers without runtime console errors.
- Production-built native Electron launched against a disposable authenticated local API on `127.0.0.1:8768`; the API was stopped after capture. The Mac Mini runtime and job-hunter were not touched.

### Retained proof

- The earlier `output/playwright/...` references describe transient local files and are not durable candidate-tree evidence. The retained correction proof is attached to Linear `CLO-49` at the locations below.
- Pointer resize and unobstructed headers: `https://uploads.linear.app/25ed0851-7a55-4629-9373-9a425d9e572b/f385b2a2-eda2-4348-96a0-f1554ef1939e/e77f8742-ebd6-4d58-a84a-ae1935723548`
- Persisted visible/DOM order: `https://uploads.linear.app/25ed0851-7a55-4629-9373-9a425d9e572b/96b70e17-9b0e-48b0-a20e-682b5fd2de7b/13feb0ad-a735-48d9-8438-32ee84365858`
- Keyboard collapse focus recovery: `https://uploads.linear.app/25ed0851-7a55-4629-9373-9a425d9e572b/d09b3034-60a8-42dd-8426-2c516ad24d3d/65f9a49d-72a4-4144-af20-9c6ec774ccf9`
- Native Electron correction proof: `https://uploads.linear.app/25ed0851-7a55-4629-9373-9a425d9e572b/65df9de1-4255-46e2-88cf-6e5fb208c9fc/2c24f403-20c2-473c-ba93-004664d83b74`

## Second PM correction - startup recovery and CI accuracy

- A failed initial `workspace.get()` no longer discards the pending layout operations after showing the safe local fallback. Those operations remain replayable until an authoritative save succeeds.
- If the speculative fallback save receives a revision conflict, the renderer fetches the remote snapshot, replays only the preserved startup operations over that snapshot, updates the visible workspace to the reconciled result, and saves against the recovered revision. Unrelated remote presets, geometry, order, collapsed panels, selected job, and active surface remain intact unless the original user operation intentionally changes them.
- The exact regression covers initial GET rejection, an early Research action, a conflicting speculative save, recovery of a deliberately non-default remote snapshot, and a final save containing the remote snapshot plus only the Research preset/surface intent.
- Pinned Node.js 26.5.0 `pnpm check` passed with 26 renderer/Electron tests and 24 Python tests, plus lint, generated contracts, TypeScript, production Electron build, and packaged-renderer verification.
- Frozen clean room `/tmp/jobos-phase3-recovery-clean.zTMPLn` passed `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, and the complete pinned `pnpm check`.
- GitHub Actions run `29714570158` concluded `failure` before any workflow steps started because the Actions budget prevented startup. The `quality` job records an empty step list. This is an infrastructure/billing failure, not a code-test failure; CI is not green. The pinned local and frozen clean-room gates above remain green.

## Final PM correction - exact recovery order and durable workspace mutation

- The preceding startup-regression claim is superseded by an exact post-failure sequence: the initial `workspace.get()` rejects and the safe-fallback handler settles first; the user then performs the first layout operation; the revision-0 save conflicts; recovery fetches the non-default authoritative snapshot; and the final save replays only that operation over the remote state.
- After a failed startup GET, JobOS now remains in startup-recovery mode even when no operation was pending at failure time. The first later resize, reset, preset, collapse, or reorder operation is journaled until an authoritative save or conflict recovery succeeds.
- The exact regression uses a post-failure Agent-chat reorder. It preserves the remote selected preset, every width, every collapse value, both unrelated preset layouts, active center surface, and authoritative selected job; only the intended active-preset order changes.
- `PUT /v1/workspace` now requires `origin` and an `idempotency_key`. The authenticated device identity supplies the actor. The desktop creates one key per logical save and reuses it when retrying an ambiguous transport failure.
- Workspace snapshot idempotency extends the existing `job_events` mutation ledger in schema migration 5. The revision write, original result, and audit record commit in one transaction. An identical retry returns the original result without another revision or audit row; reusing the key for a different command returns `409`.
- Each successful workspace mutation records origin, actor identity, timestamp, target resource, command, outcome, and safe detail. Audit detail is limited to layout revision, preset, active surface, and repaired preset names; it excludes credentials, selected-job identity, and content. Workspace audit rows are not emitted through the Phase 2 job-event stream.
- OpenAPI and generated desktop contracts separate mutation metadata from the snapshot response. Focused state, API, OpenAPI, renderer, and Electron-main tests cover exact-order recovery, first execution, identical retry, revision invariants, audit invariants, key-reuse rejection, and desktop retry-key stability.
- Pinned Node.js 26.5.0 `pnpm check` passed with 27 renderer/Electron tests and 26 Python tests, plus lint, regenerated contracts, TypeScript, production Electron build, and packaged-renderer verification.
- Frozen clean room `/tmp/jobos-phase3-final-contract-clean.JNx1Bh` passed `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, and the complete pinned `pnpm check`. A post-run directory comparison confirmed the regenerated contract outputs matched the source candidate exactly.
- GitHub Actions run `29714570158` remains an infrastructure/billing startup failure with no executed steps. CI is not claimed green. The Mac Mini, job-hunter, Phase 4 browser scope, and the accepted Phase 3 visual/accessibility behavior were not touched.

## Final PM blocker correction - persisted repair safety and generated transport retry

- Persisted layout normalization now verifies that `selected_preset`, `active_center_surface`, every `order` element, and every `collapsed` element have the expected scalar string type before any membership or set operation. Valid JSON containing objects, arrays, or other non-scalar values can no longer crash workspace restoration.
- The API regression inserts a revision-7 snapshot with a non-scalar selected preset and `collapsed: [{"bad":"value"}]`, then calls `GET /v1/workspace`. Only the malformed Review layout is repaired; the revision, authoritative selected job, active center surface, and deliberately non-default Research and Agent Focus layouts remain intact.
- The Electron main client now recognizes the generated client's actual response-less transport failure shape (`{ error, response: undefined }`) and retries exactly once with the same prebuilt body and idempotency key. A real HTTP response is never retried; focused coverage explicitly proves a `409` produces one request.
- Focused verification passed for the generated-client retry/no-retry cases and the API targeted-repair case. Pinned Node.js 26.5.0 `pnpm contracts:check` and the full `pnpm check` passed with 28 renderer/Electron tests and 27 Python tests, plus lint, TypeScript, production Electron build, and packaged-renderer verification.
- Frozen candidate clean room `/tmp/jobos-phase3-final-pm-clean.xsUXuJ` passed `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, and the complete pinned `pnpm check`. Regenerated contract outputs matched the source candidate. The final committed tree is rechecked from a detached clean checkout before push.
- Scope remained limited to these two PM blockers and this append-only evidence. `docs/planning/.DS_Store`, job-hunter, the Mac Mini runtime, Phase 4 browser work, and the accepted Phase 3 visual behavior were not touched.
