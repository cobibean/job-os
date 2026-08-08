# JobOS Document Editor Real-DOCX Acceptance Blocked Memory - 2026-08-07

## Session summary

A large open-source document-editor implementation reached `main`, but the release is **not accepted** because the exact installed app has not yet visibly opened and edited one of Cobi's real DOCX files.

Cobi's first real-file attempt failed with `DOCX contains an unsafe external hyperlink`. Investigation across all 11 DOCX files under `Documents/Resumes` found two overbroad importer rules. Both were repaired and the package was rebuilt/reinstalled, but the final visible file-picker/edit/save/reopen path remains unproved.

## What we learned

- Normal Word documents commonly use `tel:` external relationships for phone numbers.
- Normal Word documents can include `application/vnd.ms-word.stylesWithEffects+xml`; this is not macro content.
- Importer, API, IPC, blank-document, and synthetic-fixture success do not prove the installed user workflow.
- Native macOS open panels can defeat background Accessibility routing; inability to automate the picker is a test blocker, not product acceptance.

## Decisions made

- JobOS is a personal, private, single-user app. Avoid enterprise-grade security architecture and speculative validation.
- Keep proportional baseline safeguards against public data exposure, secrets leakage, actually hostile network content, traversal, and destructive actions.
- Never call document-editor work complete until a real DOCX opens and edits through the exact installed app.
- Paired DOCX/PDF publication from one canonical revision remains required.

Repository `AGENTS.md` now contains this posture and acceptance requirement.

## Files created or changed

- `apps/desktop/src/main/document-import/docxImporter.ts`
- `apps/desktop/src/main/document-import/docxImporter.test.ts`
- `AGENTS.md`

Preserve Cobi's unrelated modified file:

- `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md`

## Commands and verification

- Focused importer tests: 12/12 passed.
- Actual importer run over all 11 real DOCX files: 11/11 passed.
- Full desktop suite: 284/284 passed after one unrelated race-test rerun.
- Typecheck and lint: passed.
- arm64 packaging and signature verification: passed.
- Packaged/installed `app.asar` byte comparison: passed.
- Exact installed executable launched normally.

Relevant commits:

- `4a45a82ff0542aac08979de67d27d6ed89180153` — importer false-positive repair.
- `d487707f4e7ff7a058d3073606641ed3f81500b2` — JobOS security/product posture in `AGENTS.md`.

## Gotchas and constraints

- Do not infer acceptance from the 11/11 importer result.
- The installed file picker opened, but Devonte did not select the file and reach the editor through that visible path.
- Do not broaden security work without a concrete failure in the real single-user environment.
- Keep `.agent/**`, `.hermes/**`, screenshots, and temporary run artifacts out of product commits.
- Temporary feature worktree/branch cleanup remains pending until acceptance is complete.

## Recommended next work

Use Computer Use as if operating JobOS manually:

1. Launch `/Users/jacobilangemm/Applications/JobOS.app` normally.
2. In Review, choose `Import DOCX` and select a real file from Cobi's Resumes folder.
3. Type a unique sentence, wait for `Saved`, leave Review, reopen, and verify persistence.
4. Export/open the edited DOCX in Word or Pages, then verify PDF and paired publication.
5. Only after visible success, clean the temporary branch/worktree and write final closeout memory.
