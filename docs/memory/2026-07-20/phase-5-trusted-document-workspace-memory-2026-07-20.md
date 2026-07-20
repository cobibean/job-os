# JobOS V1 Phase 5 Trusted Document Workspace Memory - 2026-07-20

## Session summary

- Implemented Linear `CLO-51` only: a trusted job-associated document workspace in the center surface that discovers registered artifacts, faithfully renders authoritative PDFs, identifies the viewed source/render revision, and keeps the last successful preview when the newest render fails.
- Added page navigation, zoom, refresh, revision selection, download/export, Reveal in Finder, Open in Default App, explicit job association, and Workspace restoration of the active artifact/page/zoom.
- Preserved accepted Phase 1-4 layout, jobs/facade, shared Workspace, browser, audit/idempotency, and generated-contract behavior.
- Did not add editing, authoring, chat/activity, browser commands, agent parity, application submission, a generalized file manager, or any Mac Mini/job-hunter/Hermes runtime mutation.

## Product and trust decisions

- Artifacts are addressed only by opaque generated IDs. The desktop and API never accept an arbitrary filesystem path from the renderer.
- The registry records job association, source revision, artifact/render revision, media type, SHA-256, render status, current artifact, and last successful artifact. A failed or in-progress newest render cannot replace the last successful preview.
- Every streamed artifact is looked up by registered ID, canonicalized under an explicitly configured artifact root, re-hashed, checked against registered size/media metadata and PDF/DOCX file signatures, and returned with content type, content length, digest, artifact/revision headers, and a safe filename.
- PDF is the only authoritative in-app preview. DOCX remains download/export or external-open only; JobOS does not imply a conversion is authoritative.
- Refresh calls the existing `JobHunterFacade` render/list seam and reconciles its manifest into the local registry. The facade remains the only backend boundary; no second backend or raw SQL path was introduced outside the existing state store.
- Refresh preserves page and zoom when the same artifact remains active. Workspace continuity persists the active opaque artifact ID plus bounded page and zoom values through the existing atomic snapshot path.
- The duplicate Phase 5 task's unstaged `documents.py`, `settings.py`, `jobs.py`, and `state_store.py` changes were treated as untrusted partial same-scope work. The compatible registry-field, facade-protocol, configured-root, and migration concepts were retained; their implementations were substantially revised and completed with opaque-ID routing, root/hash/media verification, current/last-success state, Workspace continuity, API/desktop integration, and regression coverage. Nothing was blindly reverted.

## Implementation surfaces

- API trust and registry: `services/api/jobos_api/documents.py`, `app.py`, `jobs.py`, `settings.py`, `main.py`, `state_store.py`, `workspace.py`, and `responses.py`.
- Desktop boundary: `apps/desktop/src/main/documents.ts`, `main.ts`, `preload/preload.cts`, and `shared/contracts.ts`.
- Workspace UI: `DocumentWorkspace.tsx`, `PdfPreview.tsx`, `CenterWorkspace.tsx`, `App.tsx`, Workspace hooks/layout, and styles.
- Generated contract: OpenAPI plus the checked-in TypeScript SDK/types.
- Regression coverage: API artifact/state/contract tests and desktop document-client/renderer tests.

## Verification

- Pinned runtime: Node.js 26.5.0 from `/Users/cobibean/.nvm/versions/node/v26.5.0/bin`, pnpm 10.33.1, Python 3.11.15.
- Full source-tree gate: `PATH=/Users/cobibean/.nvm/versions/node/v26.5.0/bin:$PATH pnpm check` passed lint, contract generation, TypeScript checks, 59 desktop tests across 13 files, 153 Python tests, the production Electron/Vite build, and packaged-renderer verification. The production bundle includes the PDF.js worker.
- Focused document/state gate: `PATH=/Users/cobibean/.nvm/versions/node/v26.5.0/bin:$PATH pnpm --filter @jobos/desktop test -- src/main/documents.test.ts src/renderer/components/DocumentWorkspace.test.tsx && uv run pytest services/api/tests/test_jobs_contract.py services/api/tests/test_state_store.py services/api/tests/test_health_contract.py -q && git diff --check` passed.
- Automated Artifact Trust coverage proves job discovery, successful and failed-newest reconciliation, last-success retention, byte/hash/revision metadata, registered-root enforcement, traversal/arbitrary ID/root escape/wrong media/hash mismatch rejection, DOCX external-only behavior, verified temporary external-open caching, and Workspace artifact/page/zoom restoration.
- Native Electron/API proof used a disposable API database and desktop profile plus the repository-safe fictional PDF `/Users/cobibean/DEV/job-hunter/fictional-resumes/design-variants/Jacobi_Lange_Linear_Command_Center.pdf`. The selected job automatically exposed its registered resume and the production PDF.js path visibly rendered the real one-page artifact with association, revision, current/newest-success status, page/zoom, refresh, export, reveal, and open controls.
- Native visual evidence: `/Users/cobibean/.codex/visualizations/2026/07/20/019f7ff6-b7cf-7e01-9b7c-e39c9d88ba22/jobos-phase5-artifact-trust.png`.
- In-app-browser proof loaded the production renderer, exercised the Research/Review layout control, and confirmed the renderer-only degraded state remains coherent when the Electron bridge is intentionally unavailable.
- All disposable proof processes, database, script, and browser tabs were stopped or removed after verification. No live Mini state was contacted.
- Generated-contract drift: `PATH=/Users/cobibean/.nvm/versions/node/v26.5.0/bin:$PATH pnpm contracts:check` passed from committed candidate `ec8fcf424f5eef36ca5e1c87a504a9bd98d5644e`.
- Frozen candidate clean room: `/tmp/jobos-phase5-candidate-clean.NCYJXw` was created from `git archive ec8fcf424f5eef36ca5e1c87a504a9bd98d5644e`, given a disposable local Git baseline for drift comparison, and passed `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, the complete pinned `pnpm check`, and `pnpm contracts:check` with the same 59 desktop and 153 Python tests plus production/package verification.
- Secret scan: gitleaks 8.30.0 scanned 17 commits / about 927 KB and found no leaks.

## Constraints and honest remaining gate

- The real facade-backed resume-render pipeline is Mac Mini-only and was not executed from this MacBook. The local fixture proves the exact API/desktop seam and trust behavior, but PM/Mini acceptance must still validate one live Mini render/manifest refresh against its configured artifact roots.
- Native actions were wired and regression-tested through Electron's save dialog and shell APIs. The visible proof shows the controls and authoritative preview; PM should still perform the final human click-through for Export, Reveal in Finder, and Open in Default App on the target desktop.
- No hosted CI run is claimed green. Local source-tree and frozen exact-commit gates are the implementation evidence.
- Preserve the unrelated `docs/planning/.DS_Store` modification. It is not staged, committed, reverted, or modified by Phase 5.

## Exact handoff state

- Implementation candidate: `ec8fcf424f5eef36ca5e1c87a504a9bd98d5644e` (`feat: add trusted document workspace`).
- The closeout commit containing these exact verification results is documentation-only; resolve it as the commit containing this memory file after the final push. The final response and Linear comment record its exact SHA and confirmed `origin/main` equality.
- Remote: pending the final documentation-only closeout commit and push to `origin/main`.
- Frozen implementation-commit clean room, generated-contract drift, and gitleaks are complete and green. The documentation-only closeout commit receives a final exact-commit contract/status gate before push.
- Implementation and implementor verification are otherwise complete. Leave `CLO-51` in `Building`; PM owns acceptance and closure.
