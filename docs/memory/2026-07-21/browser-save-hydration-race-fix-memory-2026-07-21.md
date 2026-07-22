# Browser Save Hydration Race Fix — 2026-07-21

## Session summary

Fixed the remaining installed-app Wellfound Save-button failure. The earlier detail-root selector fix was valid, but the actual user-path failure was a second race: Electron marked the page load complete and re-enabled Save before Wellfound's client-rendered detail pane existed.

## What we learned

- Exact installed app: `/Users/jacobilangemm/Applications/JobOS.app`.
- Exact target: `https://wellfound.com/jobs/starred?job_listing_slug=4467759-sales-ai-agent-builder`.
- Real embedded BrowserView remained `783×753`; no viewport override was used.
- On a fresh normal navigation, Save became enabled with `document.readyState=complete`, body text length `58`, zero headings, and no detail title/location/description.
- Clicking at that first enabled moment reproduced the latest exact error:
  - `Error invoking remote method 'jobos:browser:extract-job': Error: Could not extract a complete job listing; missing location and description.`
- Wellfound then hydrated in stages: list content appeared first, and the complete Recurring Decimal detail pane appeared roughly 1.3–1.6 seconds after Save had become enabled.
- The previous forced-width success was confounded by elapsed hydration time; viewport width was not the remaining root cause.

## Fix

`BrowserManager.extractJob` now retries whole-document extraction up to 21 attempts at 250 ms intervals (five seconds maximum). It never combines fields from different attempts.

Each attempt pins and verifies:

- the raw `webContents` URL;
- the normalized HTTP(S) URL;
- the tab navigation/loading generation (`targetEpoch`).

URL changes, `about:blank`, in-page navigation, and same-URL reload/context-destruction races fail closed with the existing page-changed message. Unrelated script failures remain unchanged.

## Files changed

- `apps/desktop/src/main/browser.ts`
- `apps/desktop/src/main/browser.test.ts`
- `AGENTS.md`

## Verification

- Regression was red before implementation with the same `missing location and description` failure, then green.
- Browser tests: 24 passed.
- Full `pnpm check` passed:
  - 151 desktop tests passed;
  - 323 API tests passed, 1 skipped;
  - lint, typecheck, contracts generation, build, preload verification, and packaged-renderer verification passed.
- Independent Codex medium-reasoning review passed after navigation-race hardening.
- Fresh arm64 package built; deep ad-hoc signature verification passed.
- CDP port `9222` owner was proven as PID `24041`, executable `/Users/jacobilangemm/Applications/JobOS.app/Contents/MacOS/JobOS`.
- Installed-app proof used a trusted CDP mouse event (`event.isTrusted=true`) on the real **Save this job to JobOS** button at the first enabled moment, while the detail pane was still absent and BrowserView width remained 783 px.
- UI progressed `Save job` → `Reading…` → `Saved`; result was `Already in JobOS` and the tab associated with `Recurring Decimal`.
- Safe extraction diagnostics showed incomplete attempts 1–6, then complete attempt 7 with title length 22, location length 22, description length 1,935.
- Screenshot: `/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-wellfound-save-passed-2026-07-21.png`.
- Machine-readable proof: `/Users/jacobilangemm/.hermes/profiles/devonte/cache/jobos-installed-trusted-save-proof.json`.

## Follow-up: salary before location

A second real listing exposed a separate header format:

- URL: `https://wellfound.com/jobs/starred?job_listing_slug=3931880-senior-software-engineer-backend-ai-agent`
- Title: `Senior Software Engineer, Backend (AI Agent)`
- Company: `Cresta`
- Header: `$205k – $270k | Remote ( United States ) | 5 years of exp | Full Time`
- Exact installed-app failure: `Could not extract a complete job listing; missing location.`

The nearby rendered-location fallback previously required `Remote (...)` to appear immediately after the title. It now accepts Wellfound's bounded salary-range prefix, while requiring known metadata after the location (`years of exp`, `Full Time`, or end of header). A negative regression prevents salary-prefixed descriptive prose from being mistaken for location metadata.

Verification after this follow-up: 26 focused browser tests passed, full `pnpm check` passed, the arm64 bundle was rebuilt, its deep signature verified, and it was installed. Final human-click acceptance remained pending because repeated automated Wellfound navigation stopped rendering any detail pane and only returned the saved-list shell; do not treat that external incomplete DOM as proof of either success or failure.

## Gotchas

- Do not accept direct extraction IPC, renderer `.click()`, a forced BrowserView size, or an unverified CDP port owner as installed-app acceptance.
- The package/check commands passed under Node `22.23.1` but emitted the existing engine warning because the repo declares Node `>=26.5.0 <27`.
