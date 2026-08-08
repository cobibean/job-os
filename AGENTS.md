# JobOS Agent Guidance

## Product scope and security posture

JobOS is a personal, private, single-user application for Cobi. It is not a public SaaS product, a multi-tenant platform, or enterprise software.

- Optimize first for core workflows that work reliably in the exact installed app.
- Do not add enterprise-grade security architecture, speculative threat-model defenses, policy layers, approval systems, or validation gates unless they address a concrete risk in JobOS's real single-user environment.
- Security work must be proportional and evidence-based. Prefer the smallest maintainable safeguard that prevents a demonstrated problem.
- The primary security boundary is preventing Cobi's private data, files, credentials, conversations, and application state from being exposed to the public Internet or unauthorized external parties.
- Continue using normal baseline protections for secrets, untrusted network content, path traversal, and destructive actions, but do not let speculative hardening block ordinary local files or basic product functionality.
- A feature is not complete because its backend, schema, or tests are sophisticated. Its primary user workflow must work end to end through the exact installed application using representative real data.
- For document-editor work specifically, opening and editing Cobi's real DOCX files is a required acceptance gate. Synthetic fixtures, direct importer calls, blank documents, and direct IPC/API tests are diagnostics only.

## Product feature ideas

Before planning new JobOS features, read the lightweight wishlist:

- `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md`

Treat wishlist entries as ideas for discussion, not approved implementation work.

## Resume and cover-letter artifact pairing

- Every final resume or cover letter must be created, published, and delivered as a matched PDF/DOCX pair generated from the same document revision.
- A PDF without its corresponding DOCX, or a DOCX without its corresponding PDF, is incomplete and must not be described as finished, published, submission-ready, or delivered.
- Before reporting completion or delivery, verify that both files exist and report the exact path for each file.
- If cross-device delivery was requested, transfer and verify both files individually.
- If either format cannot be produced or delivered, report the blocker plainly and treat the document as incomplete.

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

## MacBook update packaging

- A MacBook handoff is always the **outer updater ZIP**, never `release/desktop/JobOS-<version>-arm64.zip` by itself.
- Build the complete handoff with `pnpm --filter @jobos/desktop package:macbook-update`.
- The outer ZIP must contain `Update JobOS.command`, `VERIFIED.txt`, and the inner app ZIP. Extract and verify all three before delivery.
- Use the unique timestamp-and-commit filename printed by the command; never overwrite or relabel an older MacBook artifact.
- Deliver only after confirming the source commit includes every user-visible change represented to Cobi.
