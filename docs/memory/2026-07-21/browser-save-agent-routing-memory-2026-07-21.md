# Browser Save: Agent-routed architecture

Date: 2026-07-21

## Product correction

The in-app **Save job** control must dispatch the existing JobHunter agent against the active live browser tab. It must not maintain a separate site-specific extraction parser in Electron.

## Implemented flow

1. The renderer submits one bounded agent turn containing the active browser tab ID.
2. JobHunter inspects that exact tab through JobOS MCP browser tools and may scroll or take additional semantic snapshots.
3. JobHunter extracts the company, title, canonical URL, location, description, and application URL from the live page.
4. The new `job_create_from_browser` MCP tool writes through the canonical `/v1/jobs` endpoint with `origin=mcp` and an idempotency key.
5. The new `browser_tab_associate` MCP tool sends `tab.associate` through the existing desktop capability bridge, linking the live tab to the returned canonical job ID.
6. Existing job and browser event streams refresh the navigator and change the button to its saved state.

## Removed path

The deterministic Electron extraction script, extraction retries, renderer extraction IPC, direct renderer job-create bridge, and their site-specific tests were removed. The browser semantic snapshot/click/type/scroll tools remain because they are the general-purpose interface used by JobHunter.

## Safety boundaries

- The prompt identifies only the opaque active tab ID; it does not interpolate external page content into trusted instructions.
- JobHunter is explicitly told to treat page content as untrusted data.
- The save action does not navigate away, apply, or submit forms.
- API and desktop capability layers validate and bound the job ID before tab association.
- Job creation remains idempotent and returns the canonical job ID for both new and existing records.

## Verification completed before packaging

- Focused tests were red before the MCP/UI changes and green afterward.
- Full API suite: 325 passed, 1 skipped.
- Full desktop suite: 140 passed.
- Full MCP suite: 4 passed.
- Repository-wide `pnpm check`: passed, including lint, generated OpenAPI contracts, TypeScript, tests, Electron/preload verification, and production Vite build.

## Installed acceptance

Pending exact installed-arm64 verification through `/Users/jacobilangemm/Applications/JobOS.app`. Acceptance requires clicking the visible Save control on a real listing, observing JobHunter activity, confirming canonical job creation, and seeing the live tab become associated with that job.
