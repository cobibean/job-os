# Retained-OOXML DOCX editor shipment memory — 2026-08-08

## Session summary

JobOS now edits canonical DOCX files without replacing them with HTML, JSON, or PDF. The retained-OOXML editor, packet direct-open workflow, Review PDF/DOCX switch, live current-DOCX Preview, autosave/recovery, local agent document operations, and portable document observation contracts shipped to `main`.

This closes the installed real-DOCX acceptance blocker recorded on 2026-08-07. The exact installed app opened the existing `(FAKE)` Northstar resume, saved a deliberate `(FAKE)` edit, refreshed Review immediately, showed the unchanged packet PDF, and retained the edit after switching back to current DOCX.

Product release commit:

- `fd580b25dfd1453d2d7eeef3f330ee80fe4eee59` — `feat: add retained OOXML document editing`

Both `devonte/jobos-ooxml-editor-20260808` and remote `main` were verified at that SHA before this documentation-only closeout.

## Decisions made

- DOCX is the sole persisted editable source of truth. Rendered HTML, PDF, and Preview output never replace it.
- Historical packet artifacts remain immutable. Editing creates or reuses a device-local canonical DOCX binding.
- Existing edited bindings take precedence over packet artifacts, so reopening cannot reset user work.
- Review labels and switches explicitly between the packet PDF and the current editable DOCX.
- Alignment is a paragraph property; Left, Center, Right, and Justify round-trip through OOXML (`both` for Justify).
- Exact SHA-256 values suppress self-save watcher events; agent writes publish explicit mutation events.
- Canonical mutations are serialized by deterministic binding ID. Renderer source epochs reject stale reload, restore, Save-a-Copy, and Preview completions.
- API observation revisions are ordered per device using `observed_device_id`; schema 14 preserves one portable latest observation without comparing unrelated device-local revisions.
- Keep the design proportional to a private, single-user app. No enterprise security expansion was added.

## User-visible behavior shipped

- Resume, Cover Letter, and References packet DOCX files open directly in the editor without Finder.
- Left, Center, Right, and Justify controls are available and survive save/reopen.
- Autosave reports dirty/saving/saved/conflict/error states and maintains recoveries.
- Save a Copy, recovery restore, local file changes, agent edits, close, and quit converge without stale canonical-path commits.
- Review can switch between immutable packet PDF and current editable DOCX.
- Returning from the editor refreshes current canonical DOCX bytes immediately.
- App-owned hash-prefixed filenames are hidden in presentation labels while storage identity remains unchanged.
- Remote API and MCP results expose portable document metadata, not device-local file paths.

## Important correctness fixes

Independent review found and drove fixes for:

- overlapping Save-a-Copy/source actions;
- stale asynchronous DOCX Preview commits;
- missing renderer notification after agent writes;
- temporary-file cleanup when coordinated replacement fails;
- per-binding serialization of autosave, Save a Copy, agent operations, choose file, blank creation, and packet materialization;
- the editor's initial source-subscription gap;
- stale reload/restore completion ordering;
- close/quit during source mutations;
- cross-device misuse of device-local observation revisions.

The final targeted independent review passed with zero blockers. Its sandbox could not start Vitest because it was read-only; the same focused suite ran successfully in the normal workspace.

## Verification

Source and contract gates:

- final root `pnpm check`: passed;
- focused DOCX lifecycle/Preview/editor suite: 54 passed;
- final binding-race suite: 7 passed;
- desktop TypeScript checks: passed;
- API: 363 passed, 1 skipped; Ruff passed;
- MCP: 15 passed; Ruff passed;
- generated OpenAPI/TypeScript contracts: regenerated and root drift checks passed;
- `git diff --check`: passed;
- final independent review: passed with zero blockers.

Packaging and installation:

- `pnpm --filter @jobos/desktop package:mac`: passed;
- packaged app deep code-signature verification: passed;
- installed `/Users/jacobilangemm/Applications/JobOS.app`: deep signature verification passed;
- packaged and installed `app.asar` SHA-256 matched exactly: `d75496fae9e69bb7a15e93abe4a8e280cf93e0098ee9c23d97a137d60c235ac1`.

Installed Northstar acceptance:

- editor visibly reached `Saved to DOCX` after appending one `(FAKE)` marker;
- Review immediately showed current editable DOCX with that marker;
- packet PDF rendered without the marker and remained immutable;
- switching back showed the marker still present;
- canonical DOCX local revision reached 25;
- canonical DOCX SHA-256 matched the Review UI: `defd1c873989fb63861ad8b97c82e38839c826b5d66cb322ebecc21cabbc53aa`;
- direct OOXML inspection found exactly one `(FAKE)` marker in `word/document.xml`.

Installed evidence:

- `/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-northstar-final-saved-edit-2026-08-08.png`
- `/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-northstar-final-docx-preview-refresh-2026-08-08.png`
- `/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-northstar-final-immutable-pdf-2026-08-08.png`
- `/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-northstar-final-pdf-docx-roundtrip-2026-08-08.png`

## Key files and boundaries

- `packages/docx-engine/` — retained OOXML parsing, generation, and patching.
- `packages/docx-editor-core/` — editor model, conversion, formatting, pagination, and operations.
- `apps/desktop/src/main/docxDocuments.ts` — canonical binding and serialized mutation service.
- `apps/desktop/src/main/docxFileStore.ts` — validated atomic persistence, metadata, and recovery.
- `apps/desktop/src/main/docxFileWatcher.ts` — external-change observation and exact self-save suppression.
- `apps/desktop/src/renderer/document-editor/DocxDocumentEditorShell.tsx` — editor UI and source-action coordination.
- `apps/desktop/src/renderer/components/DocumentWorkspace.tsx` — packet/current Preview selection and live refresh.
- `services/api/jobos_api/document_files.py` and `state_store.py` — portable document observations and schema 14.
- `services/mcp/` — document inspection/apply operations through the authenticated device route.

## Gotchas and constraints

- Preserve `packages/docx-engine` and `packages/docx-editor-core` upstream notices and source-boundary checks.
- Never reintroduce DOCX-to-HTML/JSON as the persisted canonical format.
- Do not expose device-local canonical paths through API, MCP, or browser surfaces.
- An app-owned canonical DOCX can contain protected passthrough content; unsupported content must be retained rather than silently flattened.
- The installed acceptance intentionally added one `(FAKE)` marker to the fake Northstar canonical resume. It is test data, not a production application document.
- Large renderer chunk warnings remain non-blocking and pre-existing release-quality debt; no code-splitting project was added to this shipment.
