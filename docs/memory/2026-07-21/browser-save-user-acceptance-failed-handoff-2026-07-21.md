# Browser Save User Acceptance Failed — Handoff (2026-07-21)

## Current truth

**The Wellfound Save job bug is still broken in the user-visible installed app.**

Cobi installed and opened the update built from product commit `b663f5207f4570a36eccf8a226735b892ef6355e`. After the Mini API/runtime was repaired and the UI showed connected, Cobi tested the real **Save this job to JobOS** button and reported the same problem and same error. The prior note `browser-save-detail-scoping-fix-memory-2026-07-21.md` therefore overstates acceptance: its direct production-IPC proof did not prove the actual user Save-button path.

Historically the repeated error was:

```text
Error invoking remote method 'jobos:browser:extract-job': Error: Could not extract a complete job listing; missing location.
```

The latest report said “same problem and same error”; the exact latest error text was not independently recaptured in this final turn.

## What was diagnosed

Safe extraction diagnostics originally proved that JobOS inspected the correct, fully loaded Wellfound starred-jobs document but used page-global rendered selectors:

- selected URL was correct;
- `document.readyState` was `complete`;
- the selected listing was Recurring Decimal / Sales AI Agent Builder / Remote (United States);
- extraction instead selected the global H1 `Search for jobs` and unrelated page-level company/description content;
- location was empty and validation threw `missing location`.

This remains a valid diagnosis of one failure mode, but it did not fully explain the installed Save-button failure.

## What changed

Product commit `b663f5207f4570a36eccf8a226735b892ef6355e` changed:

- `apps/desktop/src/main/browser.ts`
- `apps/desktop/src/main/browser.test.ts`

The implementation identifies a rendered detail root from the selected H1 and visible `About the job` heading, then scopes company, title, location, description, and application-link fallbacks to that root. Structured `JobPosting` remains highest priority.

The regression is named:

```text
job extraction scopes every field to the active Wellfound detail pane despite page-level headings and other listings
```

It was proven red before implementation and green afterward.

## Verification completed — and why it was insufficient

`pnpm check` passed on the final source:

- 147 desktop tests passed;
- 323 API tests passed, 1 skipped;
- lint, typecheck, build, contracts generation, preload verification, and packaged-renderer verification passed;
- `git diff --check` passed;
- arm64 packaging and deep ad-hoc signature verification passed.

A packaged production app returned exact live values when extraction IPC was called through CDP:

- company `Recurring Decimal`;
- title `Sales AI Agent Builder`;
- location `Remote (United States)`;
- description length 1,935;
- correct active URL.

**Critical invalidating detail:** that live CDP proof only succeeded after manually forcing the embedded browser bounds from approximately `783×753` to `1180×800`. At the narrower real/runtime width, the direct URL showed the saved-jobs list but did not hydrate the desktop detail pane; the expected title and `About the job` root were absent. The synthetic/direct IPC path was therefore not equivalent to Cobi clicking Save in the actual installed UI. Treating it as release proof was a mistake.

A stale Electron process previously owned CDP port `9222`, which also caused misleading package-verification results until its exact PID was killed. Future work must prove the executable/PID owning the diagnostic port before trusting CDP output.

## Failed artifacts — do not resend

The latest failed user-acceptance artifact is:

```text
JobOS-Active-Detail-Fix-2026-07-21.zip
size: 143122731 bytes
sha256: 5f6c1ccab0b23f9db776a687170f84d7f3f69de4acc29b9b88ee1aebfab42b90
```

It passed archive/signature checks but did **not** fix the real Save button. Older browser-save and speculative Wellfound ZIPs are also obsolete.

## Separate Mini runtime repair completed

The updater initially left an old JobOS process running and the replacement app lacked usable local runtime enrollment. This was repaired separately:

- installed app: `/Users/jacobilangemm/Applications/JobOS.app`;
- runtime config: `/Users/jacobilangemm/Library/Application Support/JobOS/runtime.json`;
- local API: `http://127.0.0.1:8766`;
- device ID: `cobi-mac-mini`;
- credential is in Keychain under service `com.cobibean.jobos.device-token` (never print it);
- API health returned HTTP 200 / `status=ready` / schema 8;
- authenticated device-session probe succeeded;
- JobOS UI showed Job changes synced, Mac Mini connected, and API 0.1.0 before the final failed Save-button test.

The user-facing Save failure is therefore not explained by the temporary API-offline/setup issue.

## Current repo/runtime state at handoff

- Repo: `/Users/jacobilangemm/DEV/dependencies/job-os`
- Branch: `main`
- Verified product commit before this handoff doc: `b663f5207f4570a36eccf8a226735b892ef6355e`
- Product worktree was clean and `origin/main` matched.
- Installed app PID observed before reset prep: `14443` (recheck; PIDs are ephemeral).
- Local API was listening on `127.0.0.1:8766` and health was ready; its health payload reported `agent_connection: offline`, while the desktop connectivity UI showed the Mini/API connected.

## Required next investigation

1. **Reproduce only through the real installed Save button.** Do not call extraction IPC directly as the primary proof.
2. Launch the exact installed app with `JOBOS_EXTRACTION_DIAGNOSTICS=1` and a clean CDP port only after proving all old JobOS/Electron PIDs exited. Verify the port owner maps to `/Users/jacobilangemm/Applications/JobOS.app`.
3. Have Cobi navigate/click exactly as normal, then click Save. Capture the exact thrown error plus safe diagnostic metadata at that moment: viewport dimensions, selected tab URL, in-page URL, detail-root presence, candidate field lengths, and missing fields. Never log descriptions or credentials.
4. Test the leading hypothesis: the real embedded browser is in a narrower responsive Wellfound layout where the selected detail content is represented differently or absent from the DOM used by the current `About the job`/H1 root heuristic.
5. Write a regression from the **actual failing responsive DOM/state**, prove it red, make the smallest fix, run `pnpm check`, package, then verify by the actual installed Save-button flow before creating any new updater.

## Cautions

- Do not weaken required fields or make location optional.
- Do not resend any existing ZIP.
- Do not trust a synthetic fixture, direct IPC, forced viewport, or stale CDP process as user acceptance.
- Preserve structured `JobPosting` priority and canonical Job Hunter persistence/deduplication behavior.
- Do not touch `/Users/jacobilangemm/DEV/agents/job-hunter`; it is a separate dirty workspace.
- Do not print device tokens, Keychain values, descriptions, or credentials.
