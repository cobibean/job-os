# Multi-document artifact workflow shipment memory

Date: 2026-07-24

## Goal

Extend the existing JobOS artifact pipeline and `DocumentWorkspace` so one job can expose, identify, view, cycle between, approve, revise, preview, and export both a resume and cover letter without creating a parallel store or viewer.

The producer boundary lives in the sibling Job Hunter repository. The persistence/API/contracts/Electron/renderer boundary lives in JobOS.

## Final source state

### JobOS

- Repository: `cobibean/job-os`
- Branch: `main`
- Product implementation commit: `92394ec76bcd5e47a04e2854eec524e555e5acd9` (`feat: support multi-document artifact workflows`)
- The implementation commit was pushed and fetched remote parity was verified before packaging.
- This memory is a later documentation-only commit. Do not confuse its SHA with the tested product implementation SHA above.

### Job Hunter

- Repository: `cobibean/job-hunter-agent-workspace`
- Branch: `main`
- Producer implementation commit: `508a04a4ba23cda1515be4a8ee63206dd3659a84` (`feat: publish typed document artifacts`)
- The producer commit was pushed and fetched remote parity was verified.
- The JobOS host runtime facade checkout at `/Users/jacobilangemm/DEV/dependencies/job-hunter-jobos-facade` was moved to this exact detached commit before installed acceptance.

## What shipped

### Typed document identity

Artifact rows now carry:

- `document_key`: exactly `resume` or `cover_letter`
- `document_label`: validated, trimmed, nonblank, maximum 80 characters
- `render_sequence`: stable producer ordering

Legacy rows remain readable with `resume` / `Resume` defaults.

### Job Hunter producer

Job Hunter now provides a safe `publish_document_artifact(...)` path and matching `job-hunter publish-artifact` CLI.

The publication boundary includes:

- workspace containment and symlink-escape protection;
- regular-file checks;
- PDF suffix and `%PDF-` signature validation;
- DOCX suffix and ZIP member validation;
- source and artifact SHA-256 values;
- idempotent repeat publication;
- monotonic render-sequence allocation;
- cross-process `fcntl` locking plus in-process locking;
- atomic manifest replacement;
- backward-compatible artifact-manifest normalization.

`render_resume` explicitly emits resume identity.

### JobOS persistence and API

- State schema advanced from 10 to 11.
- `document_artifacts` persists document key, label, and render sequence.
- Migration 11 deterministically backfills per-job legacy render ordering.
- Stable artifact registry identity remains independent of mutable document label/key metadata.
- Refresh reconciliation updates persisted key, label, and producer sequence in place while preserving the stable artifact ID.
- Legacy approval pointers are cleared when they do not satisfy successful resume-PDF approval semantics; `approved_at` is cleared at the same time.
- Valid resume approval survives benign metadata/order reconciliation.
- API/OpenAPI/generated TypeScript contracts expose the typed fields end to end.
- New approval requests fail closed for cover letters and non-PDF variants.

### Installed desktop experience

The existing `DocumentWorkspace` remains the single artifact UI. It now:

- groups variants by `documentKey` plus `sourceRevision`;
- treats paired PDF/DOCX files as one logical revision;
- orders Resume before Cover Letter;
- uses the latest render sequence within a revision;
- shows accessible, non-wrapping previous/next document controls;
- shows document name plus `x of y`;
- scopes the revision selector to the selected document;
- prefers the PDF representative and falls back to DOCX;
- resets page/zoom when the logical document/revision changes while preserving restored-view hydration;
- keeps Open/Reveal bound to the active representative;
- opens an accessible export menu offering only available successful PDF/DOCX variants;
- keeps approval Resume-only;
- computes “newest” within the selected document family rather than trusting the old global current marker;
- guards refresh and export completion against stale job/selection changes.

## Implementation chronology

1. Mapped the existing Job Hunter manifest, JobOS registration store, schema migrations, API response contracts, Electron bridge, and `DocumentWorkspace` before coding.
2. Wrote the cross-repository plan at `.hermes/plans/2026-07-24_202410-jobos-multi-document-viewer-and-format-export.md` in the JobOS workspace.
3. Implemented the Job Hunter producer in only four approved paths while preserving its heavily dirty workspace.
4. Implemented JobOS schema 11, persistence/API behavior, generated contracts, Electron behavior, and renderer controls.
5. Ran focused API, state migration, main-process, renderer, type, lint, contract, MCP, and Job Hunter checks throughout.
6. Ran three independent fail-closed reviews because Codex CLI OAuth refresh was unavailable.
7. Fixed every concrete blocker from the first two reviews and obtained a clean third review.
8. Committed/pushed the producer first, then JobOS, and verified local/remote SHA equality in both repositories.
9. Installed the exact JobOS build on the Mac mini, restarted the launchd API, pinned the live facade checkout to the exact producer commit, published real paired artifacts, and exercised the installed app.
10. Generated and Taildropped the canonical outer MacBook updater after all source, review, install, and visual gates were complete.

## Review findings and fixes

### First fail-closed review

The first review caught:

- stale legacy document identity when an artifact was reclassified;
- missing migration render-sequence backfill;
- refresh paths that did not reconcile typed metadata;
- stale async export state;
- menu accessibility concerns.

Fixes preserved stable artifact IDs while safely updating typed metadata and tightened the renderer async/menu behavior.

### Second fail-closed review

The second review caught:

- migrated legacy artifacts retaining duplicate zero sequences;
- legacy DOCX approval pointers surviving the new PDF-only rule;
- stale refresh completion overwriting newer navigation;
- stale export completion leaking into a newly selected job.

Migration, refresh reconciliation, approval cleanup, and request-scoped async guards were corrected with regression tests.

### Third fail-closed review

The final review returned:

- `passed: true`
- no security concerns
- no logic errors

Its nonblocking consistency suggestion to clear `approved_at` alongside an invalid approval pointer was applied, followed by the final full checks.

## Verification

### JobOS

Passed after the final source edits:

- `pnpm check`
- desktop tests: 210 passed
- Python tests: 343 passed, 1 skipped
- desktop type checking
- API Ruff checks
- production Vite build
- generated OpenAPI/contract consistency
- focused `DocumentWorkspace.test.tsx`
- focused Electron `documents.test.ts`
- focused state migration/job/health contracts
- MCP test suite
- staged and working-tree diff checks

The third reviewer independently reported 193 targeted API tests, all 210 desktop tests, production desktop build, API Ruff, and 32 targeted Job Hunter tests passing.

### Job Hunter

Passed:

- full pytest suite in an isolated environment;
- 32 focused publication tests;
- scoped Ruff over the four task files;
- diff whitespace checks.

A broad Ruff run over the entire already-dirty Job Hunter workspace was not used as task evidence because it traversed unrelated pre-existing work. The exact task paths passed.

## Real installed acceptance

### Exact Mini installation

- Packaged from JobOS product commit `92394ec76bcd5e47a04e2854eec524e555e5acd9`.
- Installed destination: `/Users/jacobilangemm/Applications/JobOS.app`.
- Packaged and installed `app.asar` SHA-256 matched: `9842e40427f000cbe30d078d1cf8d0ccab940dd7d2bd6d6021465775bad85dfe`.
- Deep strict signature verification passed.
- The running PID resolved to the exact installed executable.
- Launchd API restarted against state schema 11.
- API health returned `ready`.
- Live facade source checkout was verified at Job Hunter commit `508a04a4ba23cda1515be4a8ee63206dd3659a84`.

### Real acceptance artifacts

The existing Cresta Forward Deployed Product Manager job `97efa9e6f703048b030c8db2` was used.

Four typed artifacts were published through the real Job Hunter CLI:

- Resume PDF, render sequence 5
- Resume DOCX, render sequence 6
- Cover Letter PDF, render sequence 7
- Cover Letter DOCX, render sequence 8

The cover-letter PDF was produced from the real packet `cover-letter.md` with the native macOS CUPS text-to-PDF filter because no Office/LibreOffice converter was installed. It contains the real cover-letter content and was used only as installed acceptance data.

The real API refresh returned eight total artifacts for the job, including the four new typed rows with stable artifact IDs and successful status.

### Visible installed behavior

The exact installed BrowserWindow visibly proved:

- Resume active as `1 of 2`;
- Cover Letter active as `2 of 2` after cycling;
- both real PDFs rendered;
- Resume showed its scoped revision selector and approval action;
- Cover Letter showed its own scoped revision selector and no approval action;
- page count changed from the two-page resume to the one-page cover letter;
- zoom remained stable at 100%;
- export menu offered both `Export PDF` and `Export DOCX` for the paired cover-letter revision;
- no clipping, overlap, or obvious visual regression at 1440×960.

Evidence screenshots:

- `jobos-multidocument-cresta.png`
- `jobos-multidocument-cover-letter.png`
- `jobos-multidocument-export-menu.png`

They are stored under the Devonte profile cache at `cache/screenshots/`.

Computer Use lost its cua-driver session during acceptance, so the exact installed app was inspected and operated through a temporary Electron CDP port selected by the exact JobOS title/file URL. The app was then quit and relaunched normally without the diagnostic port.

The export menu and native macOS Save dialog were opened in the installed app. Actual destination-file confirmation was not used as shipment evidence; the exact PDF/DOCX artifact identity and file behavior are covered by the main-process tests and checksum-backed API/preview path.

After the final normal relaunch, API health remained `ready`, while the independent agent connection field reported `offline`. That agent-channel status was not part of the multi-document artifact pipeline and was not changed in this slice.

## MacBook outer updater delivery

Canonical command:

```text
pnpm --filter @jobos/desktop package:macbook-update
```

Verified outer wrapper:

- Filename: `JobOS-MacBook-Update-20260725022056096-92394ec7-6fa43e7bc2b5e0761cead243dc840e40.zip`
- Source commit: `92394ec76bcd5e47a04e2854eec524e555e5acd9`
- Size: `143499158` bytes
- SHA-256: `3be6c5999cc914225caeae0d1f950f12aaaa322285be62a05cf5d748bedd1e39`
- Inner ZIP: `JobOS-0.1.0-arm64.zip`
- Inner size: `143888113` bytes
- Inner SHA-256: `8c7996c6edfa8d37b2337ebe7d9963b77f6021a05b5b3ab1a64c6fc4cc79ea99`

The outer archive contains exactly:

- executable `Update JobOS.command` (`755`);
- `VERIFIED.txt`;
- the inner app ZIP.

Outer/inner ZIP integrity, deep app signature checks, arm64 packaging, fresh extraction, updater syntax, receipt/hash agreement, and disposable updater smoke installation all passed.

Taildrop target `jacobis-macbook-pro` replied over the local network. `tailscale file cp --verbose` exited 0 and printed the final `sent` receipt for the exact outer filename.

Taildrop proves delivery only. MacBook installation remains pending until Cobi accepts the file, unzips it, and runs `Update JobOS.command`.

## Changed files

### JobOS product commit

- `apps/desktop/src/main/documents.ts`
- `apps/desktop/src/main/documents.test.ts`
- `apps/desktop/src/renderer/components/DocumentWorkspace.tsx`
- `apps/desktop/src/renderer/components/DocumentWorkspace.test.tsx`
- `apps/desktop/src/renderer/styles.css`
- `apps/desktop/src/shared/contracts.ts`
- `packages/contracts/openapi.json`
- `packages/contracts/src/generated/types.gen.ts`
- `services/api/jobos_api/app.py`
- `services/api/jobos_api/documents.py`
- `services/api/jobos_api/state_store.py`
- `services/api/tests/test_health_contract.py`
- `services/api/tests/test_jobs_contract.py`
- `services/api/tests/test_state_store.py`

### Job Hunter producer commit

- `src/job_hunter/facade.py`
- `src/job_hunter/cli.py`
- `tests/test_facade.py`
- `tests/test_cli_publish_artifact.py`

## Durable decisions

- Keep one artifact registry and one viewer; do not create document-specific stores or parallel UI.
- Document identity must be explicit at the producer boundary, not inferred from filename or MIME type.
- Stable registry identity must not include mutable document key/label metadata.
- PDF/DOCX variants with one source revision are one logical document revision.
- Approval remains resume-PDF-only.
- “Newest” is document-family-relative, not a global artifact flag.
- Migration must normalize ordering and unsafe legacy approval state, not merely add columns with zero/default values.
- The operator-facing MacBook handoff is always the outer updater ZIP, never the bare inner app ZIP.

## Workspace boundaries and gotchas

- JobOS still contains the unrelated pre-existing local edit `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md`. It was never staged into the product implementation or this memory commit.
- Job Hunter remains heavily dirty with unrelated user/agent data, resume, skill, vendor, and packet work. Only the four explicit producer paths were committed.
- Generated acceptance packet artifacts and the Job Hunter database are runtime data, not product source commits.
- Do not run broad cleanup, reset, or blanket staging in either workspace.
- Schema 11 and the exact producer facade commit must be active together for the complete typed-document contract.

## Remaining operator action

On the MacBook:

1. Accept the Taildrop file.
2. Unzip the uniquely named outer wrapper.
3. Run `Update JobOS.command`.
4. Wait for `JobOS updated and opened successfully.`

No additional feature implementation is pending from this session.
