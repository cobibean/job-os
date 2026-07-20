# JobOS V1 Phases 3-5 PM Closeout Memory - 2026-07-20

## Session summary

- PM-orchestrated, reviewed, corrected, and accepted JobOS V1 Phases 3-5.
- Linear `CLO-49` (Persistent Workbench Layout), `CLO-50` (Real Persistent Browser), and `CLO-51` (Trusted Document Workspace) are **Done**.
- The accepted product-code baseline before this documentation-only memory commit is `bf6574f560887a201caa5eec2f79a76ca4f16c51` on `main`.
- `HEAD` and `origin/main` matched at closeout. The only local worktree difference was the user's pre-existing `docs/planning/.DS_Store`; it was never staged, reverted, or modified by the PM/build workflow.
- Work intentionally stopped after Phase 5. Phase 6 was not started.

## Product state now

- The app reopens into a persistent three-surface workbench with the locked Research, Review, and Agent Focus layouts.
- Panels resize, collapse/reopen, intentionally reorder, reset per preset, and restore coherent per-device state without recreating mounted content surfaces.
- The center includes a real Electron `WebContentsView` browser with persistent tabs, session partition, navigation, downloads, recovery states, and job-independent browsing.
- Job selection never closes, navigates, reloads, or reassociates unrelated browser tabs.
- The Review surface discovers job-associated artifacts by opaque ID and renders authoritative PDFs with job/revision identity, page navigation, zoom, refresh, Export, Reveal, and Open controls.
- Failed newer renders retain the actual last successful artifact. DOCX remains external/download-only and is never presented as an authoritative PDF preview.
- Browser and document behavior remain accessible through the same application/API state boundaries intended for later MCP parity; Phase 7 agent parity itself is not implemented yet.

## Decisions that must survive the handoff

- JobOS is a persistent workbench, not a dashboard, browser wrapper, document editor, file manager, or separate agent-admin console.
- One continuous agent conversation must span job changes. Do not create one conversation per job.
- The user and agent eventually use the same application API. MCP is a thin adapter around that API, never a second backend.
- Browser sessions and credentials remain local to Electron. Remote pages receive no Node, preload, raw IPC, or arbitrary application capability.
- Job selection is independent from browser navigation. Optional job-tab association is metadata only.
- PDF is the authoritative in-app document preview. Content changes happen through the agent/chat, not inline editing.
- Document artifacts use opaque IDs and registered canonical roots. The desktop never requests arbitrary filesystem paths.
- The artifact facade manifest is order-independent: every entry must provide a unique non-negative `render_sequence`; highest sequence is current and highest successful sequence is last-successful.
- Visible document bytes, viewed metadata, and Export/Reveal/Open targets must always identify the same artifact through job changes, revision changes, pending loads, and failures.
- Preserve simple, proportionate safety boundaries. Do not restart the Phase 4-style speculative security-hardening loop unless an obvious core trust defect is demonstrated.

## Accepted phase evidence

### Phase 3 - `CLO-49`

- Final accepted implementation is recorded in `docs/memory/2026-07-20/phase-3-persistent-workbench-layout-memory-2026-07-20.md`.
- Exact accepted Phase 3 commit from the PM run: `e28d849197a3a3a7134788f85f0ff3c8511bf4ab`.
- Linear contains retained native Electron, pointer resize, keyboard collapse/focus, and DOM-order proof.

### Phase 4 - `CLO-50`

- Final accepted implementation is recorded in `docs/memory/2026-07-20/phase-4-real-persistent-browser-memory-2026-07-20.md`.
- Accepted Phase 4 tip: `1299354e822f813f7ab2433032a0c4af9bab3164`.
- Final gate passed 52 desktop and 146 Python tests plus contracts, types, production build, package verification, frozen exact-commit verification, and gitleaks.
- Native visual MacBook acceptance and authenticated Gmail continuity were deferred because the MacBook was locked. Core browser/session behavior has executable and disposable native proof.

### Phase 5 - `CLO-51`

- Final accepted implementation is recorded in `docs/memory/2026-07-20/phase-5-trusted-document-workspace-memory-2026-07-20.md`.
- Artifact-trust correction commit: `51602b07870dda220fb81512a541d63324a1f205`.
- Accepted Phase 5 closeout tip before this memory file: `bf6574f560887a201caa5eec2f79a76ca4f16c51`.
- Final gate passed 64 desktop and 157 Python/API tests, contracts, types, production Electron/Vite build, PDF worker packaging, packaged-renderer verification, contract drift, frozen exact-final verification, and gitleaks over the 20-commit history.
- PM review specifically corrected stale-PDF identity, restored revision selection, unordered artifact manifests, failed-newest DOCX behavior, byte/hash snapshot consistency, and a delayed test refresh race.

## Environment and verification constraints

- This PM/build run executed on the MacBook.
- The authoritative job-hunter repository, job data, resume pipeline, and Hermes job-hunter runtime live on the Mac Mini.
- Do not claim a Mac Mini test because the MacBook fixture or disposable local API passed.
- GitHub Actions repeatedly failed before executing workflow steps because of an account/budget condition. CI is not claimed green. Pinned local and frozen clean-room gates are the accepted executable evidence.
- Repository verification uses Node.js 26.5.0, pnpm 10.33.1, and Python 3.11.15.
- Primary full gate:

  ```sh
  PATH=/Users/cobibean/.nvm/versions/node/v26.5.0/bin:$PATH pnpm check
  ```

- Contract drift gate:

  ```sh
  PATH=/Users/cobibean/.nvm/versions/node/v26.5.0/bin:$PATH pnpm contracts:check
  ```

## Mandatory Mac Mini follow-through

Before treating the document workflow as release-candidate ready, JobHunter should run these real-host checks:

1. Pull `main` on the Mac Mini and read this file plus all Phase 0-5 memory files.
2. Verify the live `JobHunterFacade.list_job_artifacts(job_id)` manifest supplies a unique non-negative `render_sequence` for every item, independent of list order.
3. Run one harmless live facade-backed resume render/manifest refresh against the configured artifact roots and confirm JobOS discovers the new artifact without manual filesystem browsing.
4. Confirm the previewed PDF matches the exported artifact and that a forced render failure preserves the last successful preview.
5. On the native target desktop, click through Export, Reveal in Finder, and Open in Default App using a disposable or safe artifact.
6. Preserve the existing job-hunter database, resume workspace, Hermes profile, and credentials. Do not migrate, clean, or rewrite them as part of verification.

## Recommended next work

- The next scoped Linear issue is `CLO-52`, **JobOS V1 Phase 6 - Continuous Agent Chat and Activity**. It remains `Scoped`.
- Phase 6 is the correct point to move primary implementation execution to the Mac Mini/JobHunter agent because the gate requires the real Hermes runtime.
- Start with the Phase 0 Hermes contract and implement `AgentGateway` behind the existing API boundary.
- First prove one harmless disposable Hermes turn on the Mini: submission acknowledgement, ordered streamed events, completion, safe cancellation where possible, and session recovery, with no job/workspace mutations.
- Then build one persistent continuous conversation, SSE reconnection, concise ordered activity rows, stop/retry/offline states, and secret-safe expandable details.
- Keep Phase 7 browser-command/MCP parity out of Phase 6.

## PM operating mode and review threshold

- The established workflow is traditional PM mode with a fresh Codex builder task per phase.
- The builder reads Linear, canonical docs, and project memory; implements and verifies; appends phase memory; pushes; comments that work is awaiting PM review; and leaves the Linear issue open.
- PM review uses three narrow read-only lanes: product/UX, core contract/data correctness, and verification/handoff.
- Only demonstrated product blockers, broken core trust boundaries, or false verification claims should trigger correction work. Reject speculative security completeness, taste-only polish, and unrelated refactors.
- Send one deduplicated bounded correction prompt back to the same builder when fixes are required. The PM alone closes the Linear issue.
- Codex desktop duplicated task creation during this run. Duplicate tasks were stopped immediately. Confirm the task list after every future dispatch so only the intended builder/review lanes remain active.

## Source-of-truth docs

- `docs/planning/brainstorming/v2-brainstorm-doc.md`
- `docs/planning/specs/v1-workbench-contract.md`
- `docs/architecture/v1-technical-architecture.md`
- `docs/architecture/v1-runtime-contract.md`
- `docs/planning/implementation/v1-implementation-plan.md`
- `docs/design/references/jobos-v1-locked-direction.png`
- `docs/memory/2026-07-19/phase-1-connected-shell-memory-2026-07-19.md`
- `docs/memory/2026-07-20/phase-2-real-jobs-shared-status-memory-2026-07-20.md`
- `docs/memory/2026-07-20/phase-3-persistent-workbench-layout-memory-2026-07-20.md`
- `docs/memory/2026-07-20/phase-4-real-persistent-browser-memory-2026-07-20.md`
- `docs/memory/2026-07-20/phase-5-trusted-document-workspace-memory-2026-07-20.md`

## Handoff note for JobHunter

- Pull `main`, confirm `git status` is clean on the Mini, and treat the repository's current `HEAD` as the documentation-inclusive starting point.
- Read this closeout first, then the Phase 5 memory and runtime contract before touching live artifacts or Hermes.
- Resolve the mandatory Mac Mini artifact checks above, record their evidence append-only in a new memory file, and then begin `CLO-52` as a separate narrow phase.
- Do not reopen accepted Phases 3-5 unless live-host evidence demonstrates an actual regression.
