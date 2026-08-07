# Editable DOCX publication and export — 2026-08-07

## Outcome

JobOS now gives the embedded JobHunter agent a trusted `document_publish` MCP action for publishing completed PDF and DOCX files into the existing artifact manifest and JobOS document registry. The installed Wells Fargo cover-letter workspace was repaired with its real editable DOCX and visually accepted with both **Export PDF** and **Export DOCX** visible in the exact installed app.

## User-visible failure

The agent reported three finished files:

- tailored resume PDF;
- cover-letter PDF;
- editable cover-letter DOCX.

The PDF files were registered JobOS artifacts. The DOCX existed only under the JobHunter Hermes cache and was returned as a truncated `MEDIA:` attachment path. The document workspace therefore knew about two logical PDF documents and could only offer PDF export.

## Root cause

The multi-format document workspace and export menu already supported PDF and DOCX variants when both were present in the trusted registry. The missing seam was producer publication:

1. the agent could generate a DOCX in `~/.hermes/profiles/job-hunter/cache/documents`;
2. `document_register` could only import an artifact that was already published in the Job Hunter manifest;
3. the embedded MCP toolset did not expose the facade's existing `publish_document_artifact` capability;
4. the agent could therefore claim the DOCX was finished without making it a first-class JobOS artifact.

This was an artifact-publication failure, not a viewer/export-rendering failure.

## Fix

### Trusted MCP publication

Added `document_publish` to the JobOS MCP server. It:

- reads inputs only from the JobHunter workspace or the active JobHunter profile's `cache/documents` directory;
- rejects paths outside those roots, direct symbolic links, non-files, unsupported artifact suffixes, and oversized inputs;
- allows only PDF and DOCX artifacts;
- instructs the agent to publish every promised format from the same source file and verify the result with `document_list` before claiming completion.

### Authenticated API publication

Added `POST /v1/jobs/{job_id}/artifacts/publish`. It:

- requires the local device credential and trusted MCP credential;
- accepts bounded base64 source/artifact payloads with strict request validation;
- materializes content-addressed files inside the existing Job Hunter `resume/exports/jobos/<job>/imports` tree;
- calls the existing Job Hunter facade publisher, preserving signature/hash/manifest verification;
- registers the resulting artifact in JobOS and emits a completed `document.publish` activity;
- uses the existing mutation replay and serialization path so an idempotent retry does not publish/register a duplicate.

Using the same source file for PDF and DOCX gives both artifacts the same `source_revision`, which is how the workspace groups export variants into one logical revision.

### Desktop refresh

The Agent panel now refreshes/focuses the document workspace after completed `document.publish`, `document.register`, and `document.refresh` activities in addition to `document.render`.

Generated OpenAPI and TypeScript contracts were updated for the new endpoint.

## Verification

### Source gates

- Ruff: passed.
- Focused API contract suite: 105 passed.
- Focused MCP suite: 6 passed.
- Focused document/agent renderer suites: 47 passed.
- Full desktop suite: 214 passed.
- Full Python suite: 347 passed, 1 skipped.
- Production Electron/preload/Vite build: passed.
- Generated contract drift check: passed after the generated files were committed.
- `git diff --check`: passed for the focused change.

The first full gate correctly caught two integration omissions during implementation:

- a missing TypeScript string type guard for activity command values;
- the new endpoint missing from the exact OpenAPI-path contract test.

Both were corrected before packaging.

### Installed runtime

The running API configuration still pointed to the stale `/Users/jacobilangemm/DEV/worktrees/jobos-long-agent-responses` worktree. It was moved to the canonical reviewed repo `/Users/jacobilangemm/DEV/dependencies/job-os`, permissions remained `0600`, and only the JobOS API and JobOS-facing Hermes dashboard were restarted. Authenticated runtime status returned loaded/ready on `127.0.0.1:8766`.

A fresh arm64 package was built and deeply signature-verified, then installed at:

`/Users/jacobilangemm/Applications/JobOS.app`

The running executable was directly verified as:

`/Users/jacobilangemm/Applications/JobOS.app/Contents/MacOS/JobOS`

### Real Wells Fargo acceptance

Job:

`b50136f17b46b031f49de12e`

The real cover-letter source and existing DOCX from the user's screenshot were published through the new live endpoint.

- shared source revision: `9abc882b8c8e85d8931828f7f09519a5c6523f86a5662b054dd538d7cb9dfecb`;
- DOCX SHA-256: `357cd0028e4117ebebf3c20210572515edf6fb2dae923b261c586edd119487f1`;
- DOCX size: 38,114 bytes;
- live download: HTTP 200;
- MIME: `application/vnd.openxmlformats-officedocument.wordprocessingml.document`;
- downloaded Office ZIP contained `[Content_Types].xml` and `word/document.xml`.

The exact installed Wells Fargo workspace visually showed both:

- **Export PDF**;
- **Export DOCX**.

Proof screenshot:

`/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-docx-export-menu-installed.png`

The document indicator remains `2 of 2` by design because it counts logical documents (Resume and Cover Letter), not format variants.

## Review limitation

A local Codex CLI commit review was attempted with medium reasoning. The command could not run because the local ChatGPT OAuth token could not refresh (`401 Unauthorized`). This was an authentication blocker, not a review finding. No independent Codex review pass is claimed.

## Late review disposition

The delayed independent review arrived after the first push. Its user-visible finding was valid: `document.publish` focused the document surface but did not force an already-mounted `DocumentWorkspace` to reload. A monotonically increasing document-mutation generation now flows from `App` through `CenterWorkspace`; `DocumentWorkspace` refreshes in place when that generation changes, preserving the selected logical revision and view state. A regression starts with only PDF while Documents is already open, advances the generation, and requires the paired DOCX export option to appear.

The review also recommended three additional local threat-model hardenings: descriptor-anchored MCP reads, directory-descriptor-anchored API writes, and a transport-level pre-parse request-body cap. Cobi explicitly accepted deferring those items because JobOS is a one-person local/private app. Existing root containment, symlink rejection, authenticated trusted-MCP checks, payload-field limits, and tests remain in place; no claim is made that the deferred hardening was implemented.

Post-fix full verification passed with 215 desktop tests, 347 Python tests (1 skipped), lint, TypeScript, generated contracts, and production build.

## Preserved boundaries

- No arbitrary filesystem export path was added.
- No second artifact store was introduced.
- JobOS still streams only allowlisted, signature-verified, checksum-verified artifacts.
- Credentials were never printed or committed.
- The user's unrelated unstaged wishlist edit was not staged or modified by this work.
