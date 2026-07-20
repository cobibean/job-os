# JobOS V1 Phase 2 Real Jobs and Shared Status Memory - 2026-07-20

## Session summary

- Implemented the Phase 2 narrow slice for Linear `CLO-48`: real job listing, inspection, filtering, ordering, selection, status changes, lead history, and resumable shared events.
- Kept JobOS as the application boundary. Electron and the five MCP tools call the authenticated JobOS API; neither surface opens job-hunter storage directly.
- Added a reviewed job-hunter facade on the separate branch `codex/clo-48-jobos-facade` at `2f48190936b92cafb901a47893bc420b4b8e378a`. That branch is pushed but intentionally not merged pending review.
- Proved the production-built native Electron app with real Mini-backed job data and proved user-to-MCP convergence using only disposable mutation state.
- Did not run a Hermes turn, install a permanent service, or mutate the live job-hunter database or its dirty worktree.

## What we learned

- The live job-hunter store can be composed read-only through `JobStorage(..., initialize=False)` and the narrow facade without leaking SQLite access into JobOS.
- JobOS needs its own workspace state for selection, manual order, current sort, and the resumable event cursor. These are product state, not job-hunter domain state.
- Filtering the visible navigator must not clear the selected job from the agent context. The renderer now retains the active job even when it is temporarily outside the filtered result set.
- A single authenticated API command path gives Electron and MCP the same transition validation and event semantics. Invalid transitions return `409` and create neither an upstream history entry nor a JobOS event.

## Decisions made

- Preserve the job-hunter status vocabulary exactly and group every status into `Inbox`, `Considering`, `Applied`, `Interviewing`, `Closed`, or `Inactive` only for display.
- Keep manual order complete and authoritative across alternate sorts and filters. Reordering is enabled only in the unfiltered manual view.
- Persist JobOS selection, sort, order, and events in the JobOS-owned SQLite database at schema version 3.
- Use an authenticated resumable SSE stream for cross-surface refresh. MCP remains a thin HTTP adapter with exactly five tools: `job_list`, `job_inspect`, `job_select`, `job_reorder`, and `job_update_status`.
- Leave future browser, document, and agent controls visibly disabled; Phase 2 does not expand into later workbench phases.

## Files created or changed

- API and state: `services/api/jobos_api/jobs.py`, `adapters.py`, `app.py`, `state_store.py`, settings/version responses, and API tests.
- MCP: `services/mcp/jobos_mcp/jobs.py`, `server.py`, package entry point, and MCP tests.
- Desktop: `apps/desktop/src/main/jobs.ts`, strict main/preload IPC wiring, `useJobs.ts`, `JobNavigator.tsx`, active-context wiring, styles, and desktop tests.
- Contracts: generated OpenAPI and TypeScript contract outputs under `packages/contracts/`.
- Separate job-hunter review branch: `src/job_hunter/facade.py` and `tests/test_facade.py` at commit `2f48190936b92cafb901a47893bc420b4b8e378a`.

## Source-of-truth docs

Read before implementation in this order:

1. `docs/planning/brainstorming/v2-brainstorm-doc.md`
2. `docs/planning/specs/v1-workbench-contract.md`
3. `docs/architecture/v1-technical-architecture.md`
4. `docs/architecture/v1-runtime-contract.md`
5. `docs/planning/implementation/v1-implementation-plan.md`
6. `docs/memory/2026-07-19/phase-1-connected-shell-memory-2026-07-19.md`
7. `docs/design/references/jobos-v1-locked-direction.png`
8. Linear `CLO-48 — JobOS V1 Phase 2 — Real Jobs, Shared Status, and Agent Action`

## Commands and verification

- JobOS focused and full gate: `pnpm check` passed on pinned Node.js 26.5.0, covering lint, regenerated contracts, TypeScript checks, 15 desktop tests, 19 Python tests, production Electron build, and packaged-renderer verification.
- Job-hunter facade: 3 focused tests passed; the complete upstream suite passed with 123 tests.
- Browser interaction proof covered selection, filtering with retained agent context, manual reordering, alphabetical sort, and a status change. The application produced no runtime console errors; the static proof server returned only a missing-favicon `404`.
- Native real-data proof: `jobos-phase2-real-jobs.png` shows the production-built Electron app rendering the real Mini-backed Apollo.io job.
- Native shared-event proof: `jobos-phase2-mcp-event.png` shows the Electron row changing to `Applied` and reporting `Agent changes synced` after the actual `JobOsMcpClient` command.
- Browser proof: `jobos-phase2-browser-proof.png` records the production renderer after selection, ordering, sorting, and status interaction.
- Live job-hunter database SHA-256 before and after proof was identical: `2a887fd17072ab93f1c0a02fa299b7bce35e4c9ad903cebf95a35a662b2c974b`.
- Live job-hunter dirty-worktree fingerprint before and after proof was identical: `79b1c33be7eebff81ea266b15a5a68c6ded500cfde7b335cc1cb1494bab8cc6e`.
- Disposable convergence proof reached `scored -> reviewed -> shortlisted -> applied`; an invalid `shortlisted -> interviewing` request returned `409`, left status at `shortlisted`, and left history count unchanged at 4.

## Gotchas and constraints

- The Mac Mini job-hunter database and worktree are authoritative and read-only from JobOS verification. Any status-change proof must use a copied database and disposable JobOS state.
- The job-hunter facade branch is deliberately separate and unmerged. JobOS imports it only when the configured runtime contains the reviewed facade.
- The Mini proof used temporary API processes and a temporary root under `/tmp`; both processes were stopped, the ports were confirmed closed, and the disposable root was removed.
- No credential value is stored here. Proof credentials were ephemeral and removed.
- Do not run MacBook-side Hermes turns for this phase. Hermes integration and live-turn proof remain Phase 6 work on the Mini boundary.
- Preserve the unrelated `docs/planning/.DS_Store` modification; it is not part of CLO-48.

## Open decisions

- PM must review and merge or otherwise accept `codex/clo-48-jobos-facade` before the production JobOS runtime can depend on that facade.
- PM validation still decides whether the Phase 2 product and evidence satisfy `CLO-48`. The implementor leaves the issue in `Building` and does not close or transition it.

## Recommended next work

- PM-review the native screenshots, read-only boundary evidence, invalid-transition proof, full checks, and separate facade branch.
- After Phase 2 acceptance and facade integration, proceed to the next approved workbench slice without expanding Phase 2 into Hermes or permanent Mini deployment.
