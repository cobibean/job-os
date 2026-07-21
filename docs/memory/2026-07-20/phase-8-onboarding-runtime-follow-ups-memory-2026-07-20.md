# JobOS Phase 8 Onboarding Runtime Follow-Ups Memory - 2026-07-20

## Status

- Phase 8 remains shipped.
- Cobi selected a packaged Electron app as the durable opening/access direction.
- Both onboarding defects are fixed and verified in the disposable production runtime.
- An unsigned Apple-silicon `JobOS.app` and zip were built and launched successfully.
- No `launchd` installation, remote exposure, production cutover, or primary Job Hunter data mutation was performed.

## Runtime proof context

The onboarding run used the production-built Electron desktop against a temporary Mac Mini API and isolated copies of the JobOS state database, Job Hunter database, artifact workspace, and Electron profile. The JobOS and facade repositories remained clean.

The safe test selected job `22773` (Precision Castparts Corp. · Senior AI App Developer), opened its listing, exercised the live job-hunter Hermes conversation, rendered a three-page PDF, approved the exact successful artifact, restarted the API and desktop, and inspected restoration.

## Bug 1 — Approved PDF preview does not restore reliably

### User-visible impact

The approved artifact persisted, but the in-app document preview did not become trustworthy after restart:

- the center workspace showed **No trusted preview yet**;
- the restored controls initially showed page 2 and 110% zoom without a resolved page count;
- Electron emitted repeated PDF-load timeouts;
- the saved active-artifact pointer and page/zoom state were eventually reset;
- the underlying PDF remained valid and could still be reviewed through **Open**.

This blocks calling the document-restoration path ready for dependable everyday use.

### Reproduction used during onboarding

1. Select job `22773`.
2. Render the tailored source `22773-precision-castparts-senior-ai-app-developer` to PDF.
3. Confirm the render succeeded and the activity panel recorded **Rendered resume artifact · completed**.
4. Approve the exact displayed artifact.
5. Save the Review workspace with the approved artifact active, page `2`, and zoom `1.1`.
6. Stop and restart both the JobOS API and Electron desktop.
7. Observe the restored document surface and wait for PDF loading and workspace persistence to settle.

### Verified evidence

- The artifact API continued to report the exact artifact as approved after restart.
- The generated PDF was valid, 144,252 bytes, and loaded through `pdfjs-dist` as three pages.
- A direct first-page raster review showed readable content without obvious overlap or missing glyphs.
- JobOS health remained `ready` and agent connectivity remained `online`.
- The API logged many successful `GET /v1/artifacts/<artifact-id>/content` responses while Electron simultaneously logged repeated `fetch failed` / `connect ETIMEDOUT 127.0.0.1:8784` errors.
- The UI screenshot showed **Approved revision** but **No trusted preview yet**.
- After the failed preview cycle, the workspace no longer retained the active artifact, page 2, or 110% zoom.

### Investigation boundaries

Do not assume the PDF bytes or API availability are the root cause. The repair should trace the full restore sequence across:

- `DocumentWorkspace` artifact list/refresh/preview effects;
- Electron `documents.loadPdf` request lifecycle;
- workspace hydration and persistence ordering;
- restored `activeArtifactId`, page, and zoom ownership;
- retry or duplicate-load behavior after a preview request fails or the app is shutting down.

### Acceptance criteria

1. Restarting both API and desktop restores the approved artifact as the active preview.
2. The three-page PDF renders in the JobOS canvas without a request storm or timeout loop.
3. Page 2 and 110% zoom restore after restart.
4. A transient preview failure does not clear the approved artifact or silently reset the saved view pointer.
5. API logs and Electron logs show one bounded load lifecycle rather than repeated concurrent content requests.
6. The proof passes in a production build with disposable state and a real rendered artifact.

### Resolution and proof

`DocumentWorkspace` was persisting stale local state during workspace hydration. Workspace props and local document state synchronize in effects; the persistence effect could observe the old local value in the same render where a new restored value arrived, write it back, and create a null/restored ping-pong. The job list also hydrates independently, so the temporary absence of a selected-job object was incorrectly treated as a user deselection.

The fix now:

- preserves restored artifact state while the selected job is still hydrating;
- suppresses persistence during a render that is synchronizing newer restored props;
- still clears document state when a real non-null job-to-job transition occurs;
- covers initial null-to-restored hydration and delayed selected-job hydration in regression tests.

The disposable restart proof restarted both API and desktop, loaded the approved artifact once, rendered the real PDF at **Page 2 of 3** and **110%**, retained workspace revision `201` without another save, and showed no PDF-load timeout or content-request storm. The final packaged-app screenshot repeated the same result.

## Bug 2 — Agent turn context omits selected company and role

### User-visible impact

The right-panel context chip correctly displayed **Precision Castparts Corp. · Senior AI App Developer**, but the live agent turn only received selected job ID `22773`.

When asked to identify the selected company and role without using tools, the agent replied that job `22773` was selected but that the company and role were not included in its provided context.

Until fixed, users must repeat the company and role in important prompts even though the UI implies that the agent already has that context.

### Reproduction used during onboarding

1. Select job `22773` in JobOS.
2. Confirm the right-panel context chip shows the company and role.
3. Send: `Onboarding check only: reply with the company and role currently selected in JobOS. Do not call tools, edit files, or take any action.`
4. Observe that the agent identifies only the selected job ID and says company/role are absent.

### Acceptance criteria

1. Each submitted turn snapshots the selected job at submission time.
2. The agent receives at least `job_id`, `company`, and `title` in a bounded, explicitly structured context block.
3. The visible context chip and the context actually delivered to the agent agree.
4. Changing jobs after submission does not rewrite the in-flight turn's context.
5. A no-tools regression test proves the agent can identify the selected company and role from supplied context alone.
6. No raw database rows, secrets, filesystem paths, or unbounded listing content are added to the prompt.

### Resolution and proof

Conversation submission now snapshots a bounded `selected_job` object containing `job_id`, `company`, and `title`. The snapshot is stored with the turn and included in the structured Hermes prompt context. Company and title values are sanitized and capped at 200 characters; descriptions, raw rows, paths, and credentials are not included.

The final security review correctly identified company/title as externally sourced, untrusted data rather than instructions. Hermes prompt assembly now:

- labels the JSON block as untrusted reference data;
- explicitly forbids interpreting its values as instructions or tool requests;
- allowlists and individually bounds the three selected-job fields and six workspace fields;
- emits complete parseable JSON instead of truncating a serialized payload;
- removes credential carriers including OAuth codes, session IDs, and encoded SAML artifacts;
- escapes angle brackets so listing text cannot close the context delimiter;
- places the user request after the bounded context and policy reminder.

Adversarial adapter regressions cover instruction-like titles, delimiter closure, quotes, backslashes, newlines, oversized fields, OAuth codes, PHP session IDs, percent-encoded SAML artifacts, valid JSON parsing, omitted fields, and user-request ordering. A focused independent security re-review returned `PASS` after these cases were implemented.

Focused contract tests cover both turn snapshotting and prompt delivery. In the disposable live runtime, a no-tools verification turn replied exactly:

`Precision Castparts Corp. — Senior AI App Developer`

## Launch and access decision — packaged Electron

### Package result

- Added Electron Builder `26.15.3` with an arm64 macOS directory and zip target.
- Added product metadata for `JobOS`, bundle ID `com.cobibean.jobos`, and a custom checked-in `.icns` source icon.
- Final zip: `release/desktop/JobOS-0.1.0-arm64.zip`.
- Final zip size: `144,466,354` bytes.
- SHA-256: `ed4b7f5c364b67ace5a2d7c1bd4f3e1410215b181c1c18e486850fd8123ceef9`.
- The zip integrity check passed and its executable is arm64 Mach-O.
- A scan confirmed that the disposable device token is absent from both `app.asar` and the zip.
- The final packaged executable connected to the private API and rendered the complete three-pane workbench, restored approved PDF, and corrected live-agent answer.

### Verification gates

- `pnpm check`: passed.
- Desktop: 18 test files, 111 tests passed.
- API: 279 tests passed.
- Lint, type checks, generated-contract drift checks, preload verification, production renderer build, and packaged-renderer verification passed.
- `pnpm --filter @jobos/desktop package:mac`: passed on the final source.
- Final packaged-app visual smoke: passed.
- `git diff --check`: passed.

### Deliberate remaining boundary

This is a **verified unsigned package**, not yet a finished one-click installation:

- no Apple Developer ID signing or notarization credentials are configured, so Gatekeeper rejects the package by default;
- the app still receives API URL, device ID, and device token through its launch environment;
- it does not install or supervise the Mac Mini API service;
- direct MacBook use still needs a private Tailscale API route, device provisioning, and a secure local credential store;
- the package is arm64-only.

Do not describe the current zip as Finder-ready or install it as the everyday launcher until Phase 9 adds credential storage, API lifecycle/readiness handling, private MacBook connectivity, and the signing/notarization decision.

## Files changed

- Desktop document restoration component and regression tests.
- API conversation context, Hermes adapter, and contract tests.
- Desktop package metadata, Electron Builder configuration, lockfile, custom app icon, and release ignore rule.
- This updated project-memory note.

## Next work

1. Add secure device-token storage and non-secret endpoint/device configuration for Finder launches.
2. Add Mac Mini API lifecycle/readiness ownership and clean recovery behavior.
3. Provision the MacBook through a private Tailscale route without public exposure.
4. Decide Developer ID signing/notarization before distributing an everyday installer.
5. Verify prior-install to packaged-install behavior and rollback before calling Phase 9 shipped.
