# Third-Party Browser Save QA — 2026-07-21

## Session summary

Tested JobOS browser-to-canonical-job creation against a live third-party job board rather than the deterministic local fixture.

Target listing:

- Figma on Greenhouse
- `https://job-boards.greenhouse.io/figma/jobs/5364702004?gh_jid=5364702004`

## What happened

The first production Electron attempt reached the real Figma listing without a bot challenge, but extraction failed with a clear error: company, location, and description were missing.

Inspection showed the current Greenhouse hosted-board markup uses:

- `.job__location`
- `.job__description`
- a company logo with an `alt` value such as `Figma Logo`
- a document title ending in `at Figma`

The extractor previously supported hyphenated generic selectors but not these Greenhouse-specific double-underscore classes.

## Fix

Added narrow Greenhouse fallbacks while preserving the extraction priority:

1. schema.org `JobPosting` metadata;
2. existing generic DOM/meta fallbacks;
3. Greenhouse hosted-board logo/title, location, and description selectors.

Added a focused regression test using representative current Greenhouse markup.

## Verification

- Focused Greenhouse extraction test passed.
- Full `pnpm check` passed:
  - 146 desktop tests passed;
  - 323 API tests passed and 1 skipped;
  - lint, TypeScript, generated contracts, and production build passed.
- Relaunched the production Electron bundle against isolated API and Job Hunter SQLite databases.
- Loaded the real Figma listing inside JobOS's actual `WebContentsView`.
- First save produced `Saved to JobOS`.
- Second save produced `Already in JobOS`.
- Canonical SQLite verification showed exactly one job row:
  - company: `Figma`;
  - title: `Account Executive, Emerging Enterprise (Berlin, Germany)`;
  - location: `Berlin, Germany`;
  - description length: 4,624 characters;
  - source: `jobos_browser`.
- The canonical job was selected immediately and the tab's `associatedJobId` matched the saved job ID.
- Two observations were recorded for the two save attempts without creating a duplicate canonical job.

Final renderer evidence:

`/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-browser-save-chrome.png`

## Computer Use status

The `computer_use` tool was attempted repeatedly after Electron launched, but its cua-driver session had ended and rejected `list_windows`, `capture`, and `focus_app`. `hermes computer-use doctor` returned only an incomplete platform summary.

The live test therefore used CDP to press the actual rendered Save button in the production Electron application. This exercised the real preload, IPC, extractor, API, facade, dedupe, SQLite, navigator selection, feedback, and tab-association path, but it was not an OS-level mouse click from Computer Use.

Future Computer Use work requires a healthy/restarted cua-driver session before capture; do not describe the CDP interaction as Computer Use.
