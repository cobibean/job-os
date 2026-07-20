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

- Final correction implementation: `51602b07870dda220fb81512a541d63324a1f205` (`fix: align trusted artifact identity`), based on reviewed Phase 5 tip `11c05b405c34c9167512962e09515f9560d14e52`.
- `origin/main` was fetched without divergence, pushed, and confirmed byte-for-byte at `51602b07870dda220fb81512a541d63324a1f205`. The documentation-only closeout commit containing this section is the remote tip recorded in the final Linear comment and implementor response.
- The obsolete pre-review “pending” state is fully superseded by this block. Implementation and implementor verification are complete; `CLO-51` remains in `Building` for PM acceptance and closure.
- The only working-tree difference outside the committed candidate is the user's preserved, unstaged `docs/planning/.DS_Store`.

## PM artifact-trust correction - 2026-07-20

### Corrected behavior

- Job and revision changes immediately invalidate prior payload state. The renderer only mounts `PdfPreview` when `payload.artifactId` exactly equals the active artifact, remounts the PDF canvas when artifact identity changes, and clears canvas pixels before a new PDF render.
- Failed or delayed loads never leave prior bytes displayed under a new job/revision. Viewed filename, artifact/source revision, media type, render status, preview behavior, and Open/Reveal/Export target all derive from the same active artifact. Newest-render state is a separate banner.
- Initial restoration and automatic refresh preserve an older deliberate selection while it remains successful. An identity fallback occurs only when the selected artifact disappeared or became unusable, and then page/zoom reset to page 1 / 100%; restored page values are not prematurely clamped before the PDF page count arrives.
- A failed newest render with a last-successful DOCX and older PDF selects the DOCX, exposes only its external/export actions, and never presents the older PDF as the DOCX revision.
- The facade artifact manifest is explicitly order-independent: each item must carry a unique non-negative `render_sequence`; highest sequence is current and highest successful sequence is last-successful. Oldest-first and newest-first inputs yield identical pointers, and duplicate sequences are rejected.
- Artifact content responses now hash, validate, and return one byte buffer from one filesystem read. Metadata headers and response bytes cannot diverge through a replacement between verification and response construction.
- The two App test bridges that previously returned `undefined` during the delayed jobs refresh now return deterministic job arrays.

### Correction verification

- Focused renderer/main-process/App suite ran three consecutive times: `PATH=/Users/cobibean/.nvm/versions/node/v26.5.0/bin:$PATH pnpm --filter @jobos/desktop exec vitest run src/renderer/components/DocumentWorkspace.test.tsx src/main/documents.test.ts src/renderer/App.test.tsx`; each run passed 34 tests across 3 files.
- Focused API/state/contract suite: `uv run pytest services/api/tests/test_jobs_contract.py services/api/tests/test_state_store.py services/api/tests/test_health_contract.py -q` passed.
- Full pinned source-tree gate: `PATH=/Users/cobibean/.nvm/versions/node/v26.5.0/bin:$PATH pnpm check` passed lint, contract generation, TypeScript, 64 desktop tests across 13 files, 157 Python tests, production Electron/Vite build, PDF worker packaging, and packaged-renderer verification.
- Generated contract drift: `PATH=/Users/cobibean/.nvm/versions/node/v26.5.0/bin:$PATH pnpm contracts:check` passed.
- Frozen exact-correction clean room: `/tmp/jobos-phase5-correction-clean.NAn1uK` was created from `git archive 51602b07870dda220fb81512a541d63324a1f205`, given a disposable local Git baseline, and passed `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, full `pnpm check`, and `pnpm contracts:check` with the same 64 desktop / 157 Python counts plus production/package verification.
- The documentation-only closeout commit containing this section also passed the frozen exact-final full gate and contract drift check before its final push.
- Gitleaks 8.30.0 scanned the final 20-commit history / about 953 KB and found no leaks.
- In-app-browser production-renderer proof showed a failed-newest banner separately from `Viewing northstar-resume.docx · revision render-2 · source source-2`, no older-PDF active identity, DOCX external-only behavior, Export targeting the DOCX opaque ID, and no application console errors or warnings.

### Remaining Mini/native defers

- No Mac Mini, live job-hunter database/render process, or Hermes runtime was contacted or changed. PM/Mini acceptance still needs one live facade manifest using the documented `render_sequence` contract and one live render/refresh.
- PM should still click Export, Reveal in Finder, and Open in Default App on the native target desktop. Automated and rendered proofs verify identity targeting, but do not claim this human native-shell acceptance.
- No hosted CI-green claim is made; pinned local and frozen exact-commit gates are the correction evidence.
