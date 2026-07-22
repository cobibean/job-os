# JobOS Agent Guidance

## Product feature ideas

Before planning new JobOS features, read the lightweight wishlist:

- `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md`

Treat wishlist entries as ideas for discussion, not approved implementation work.

## Installed desktop acceptance

For browser-save fixes, only the exact installed JobOS UI path counts as acceptance:

- keep the real BrowserView bounds; do not force a wider viewport;
- activate the real **Save this job to JobOS** button rather than submitting an agent turn or API mutation directly;
- confirm the visible action dispatches JobHunter against the active live browser tab, creates or finds the canonical job through MCP, and associates that same tab;
- do not reintroduce a site-specific Electron/renderer extraction parser—the semantic browser tools are the general interface for JobHunter;
- preserve JobHunter's general-purpose `browser_click` and `browser_type` tools for future browser features; constrain the Save prompt/flow rather than deleting those capabilities;
- in multi-device operation, route every MCP browser command to the desktop that originated the active turn: authenticated desktop `device_id` → turn `origin_device_id` → `CapabilityBroker.execute(..., device_id=origin_device_id)` → that exact capability socket; never route by “last connected desktop”;
- do not build, package, or distribute another updater until visible-button acceptance proves canonical job creation/deduplication, exact source-tab association, and visible Saved state;
- prove the diagnostic port owner is the expected installed executable/PID before trusting CDP evidence;
- remember that client-rendered sites can finish Electron navigation before their job-detail DOM hydrates.

Synthetic fixtures and direct IPC are useful diagnostics, not shipment proof.
