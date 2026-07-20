# Phase 8 Targeted Resume Approval Closeout Memory - 2026-07-20

## Phase status

Phase 8 is closed as a verified single-user, local-first MVP. JobOS can ask the real Job Hunter facade to render a job-specific tailored resume, persist the exact artifact and revision identity, automatically move successful agent work into the existing document review surface, and approve one concrete successful artifact across API, MCP, desktop, and restart boundaries.

The locked product shape remains unchanged:

```text
Job Navigation | Dominant Browser or Document Workspace | Continuous Agent Chat
```

## What shipped

- A filesystem-backed Job Hunter artifact facade that uses the existing resume renderer rather than duplicating resume-generation logic in JobOS.
- Job-specific tailored-source resolution, safe source identity normalization, explicit missing-source failures, sequential render history, source SHA-256, rendered artifact SHA-256, and retained failed-render records.
- JobOS wiring for the real local Job Hunter facade and its configured database/workspace roots.
- State schema 8 with one exact approved artifact per job, including approval timestamp and artifact identity validation.
- Authenticated approval API: `POST /v1/jobs/{job_id}/artifacts/{artifact_id}/approve`.
- Approval restricted to the selected job's successful, checksum-verified artifact. Viewing, previewing, downloading, or selecting remains distinct from approval.
- MCP `document_approve` parity through the same API behavior.
- Generated OpenAPI and TypeScript contracts for approval state and mutation.
- Electron main/preload/renderer approval transport.
- Compact document controls for newest/older revision selection, source and artifact revision visibility, exact approval, and approved-state restoration.
- Automatic center-surface focus after completed `document.render` agent activity. Failed renders remain failed activity and do not trigger successful-document focus.
- Last-successful preview preservation when a newer render fails.

## Runtime-driven hardening

The real native smoke found and drove bounded fixes for:

- failed Job Hunter renders being recorded as completed agent activity;
- the document workspace reloading artifacts when the same job object was recreated after persistence;
- Electron `contextBridge` nested proxies changing identity across renders and retriggering effects, workspace hydration, and PDF requests;
- successful PDF bytes being cleared when automatic refresh returned the same content-addressed artifact;
- the added viewed-artifact status row displacing the document canvas because the CSS grid still declared the old row count.

Regression coverage now protects failed-render activity state, stable same-job rerenders, stable context-bridge capture, retained PDF bytes after same-artifact refresh, automatic document focus, approval transport, approval persistence, and invalid approval rejection.

## Native golden-path acceptance

The proof used:

- the production-built Electron main/preload/renderer;
- a persistent disposable JobOS schema-8 database;
- a disposable copy of the actual Job Hunter database with the real Precision Castparts packet represented as job `22773`;
- the actual Job Hunter workspace and tailored source `resume/tailored/22773-precision-castparts-senior-ai-app-developer.md`;
- the real Job Hunter resume renderer with its local Playwright/Chromium runtime prerequisite installed;
- the real generated three-page PDF under the configured trusted artifact root.

Observed evidence:

1. Precision Castparts Corp. / Senior AI App Developer was listed and selected.
2. The center surface was deliberately placed in browser mode before rendering.
3. Job Hunter produced a job-specific successful PDF with concrete source revision, artifact revision, and SHA-256 identity.
4. Completed `document.render` activity automatically moved the live desktop to the document surface and focused the newest successful artifact.
5. Older/failed history remained available without replacing the last successful preview.
6. The exact visible successful artifact was approved.
7. API and desktop restart restored selected job `22773`, document surface, active artifact, page 1, zoom 1.0, approved artifact ID, source revision, artifact revision, checksum, and `is_approved=true`.
8. The final native screenshot showed the rendered resume page in the dominant center pane, selected PCC job at left, compact approved-revision state, revision selector, and completed render activity at right with no blocking overlap or clipping.

Final visual evidence is attached from the Hermes profile cache as `jobos-phase8-final.png`; it is intentionally not committed to the repository.

## Final automated verification

Final post-fix execution evidence:

- Canonical repository gate: `pnpm check` passed.
- Desktop: **107 passed** across 18 files.
- Python: **276 passed**.
- Contract and desktop lint: passed with zero warnings/errors.
- Ruff: passed.
- TypeScript contract and desktop typechecks: passed.
- OpenAPI and generated TypeScript contracts: generated successfully.
- Electron/preload build and self-contained preload verification: passed.
- Vite production renderer build: passed; 1,794 modules transformed.
- Packaged-renderer verification: passed.
- Job Hunter focused Ruff check passed for the facade and its tests.
- Job Hunter: **125 passed**.
- `git diff --check` passed in both repositories.

## Bounded limitations

- This remains a local, one-user MVP. There is no multi-user approval model, cloud artifact store, distributed job queue, workflow engine, or generic version-control layer.
- The Job Hunter PDF renderer still requires a local Playwright CLI and installed Chromium. Permanent packaging and host cutover remain Phase 9 work.
- DOCX stays external-only; the rendered PDF is the trusted in-app review surface.
- The smoke API used a disposable copy of the local Job Hunter database to avoid mutating the primary database while pairing it with the real saved packet and tailored resume source.
- Hermes itself was offline in the visual proof, so activity was driven through the authenticated parity API/MCP path; continuous-agent rendering behavior remains covered by the shared activity contract and desktop tests.

## Primary implementation areas

- Job Hunter `src/job_hunter/facade.py` and `tests/test_facade.py`.
- `services/api/jobos_api/adapters.py`.
- `services/api/jobos_api/app.py`.
- `services/api/jobos_api/documents.py`.
- `services/api/jobos_api/state_store.py`.
- `services/mcp/jobos_mcp/jobs.py` and server registration.
- `apps/desktop/src/main/documents.ts` and Electron IPC/preload wiring.
- `apps/desktop/src/renderer/components/AgentPanel.tsx`.
- `apps/desktop/src/renderer/components/DocumentWorkspace.tsx`.
- Renderer bridge-stability hooks, document styles, shared contracts, generated contracts, and focused tests.

## Next phase boundary

Do not expand Phase 8 into a generalized document system or autonomous application workflow. Phase 9 should own permanent local runtime packaging, Playwright/Chromium prerequisite management, launch policy, operator setup, and cutover. Reopen Phase 8 only for a reproducible regression in exact artifact identity, approval, successful-render focus, or trusted PDF review.