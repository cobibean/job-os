# JobOS Agent Guidance

## Product feature ideas

Before planning new JobOS features, read the lightweight wishlist:

- `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md`

Treat wishlist entries as ideas for discussion, not approved implementation work.

## Installed desktop acceptance

For browser-save fixes, only the exact installed JobOS UI path counts as acceptance:

- keep the real BrowserView bounds; do not force a wider viewport;
- activate the real **Save this job to JobOS** button rather than calling extraction IPC directly;
- prove the diagnostic port owner is the expected installed executable/PID before trusting CDP evidence;
- remember that client-rendered sites can finish Electron navigation before their job-detail DOM hydrates.

Synthetic fixtures and direct IPC are useful diagnostics, not shipment proof.
