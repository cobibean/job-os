# Phase 7 MVP Review Disposition Memory - 2026-07-20

## Decision context

Phase 7 is a local, authenticated, single-user MVP. Its core path was exercised through the real production-built Electron browser using API-only MCP calls, and the full automated gate passed after multiple bounded review and hardening rounds.

The remaining items below are explicit product-boundary limitations, not unreported fixed behavior. They do not invalidate the demonstrated local happy path, expose device credentials to remote page content, create a second browser, or prevent recovery by refreshing the snapshot/retrying/relaunching.

## Accepted MVP limitations

1. **Hostile or highly dynamic page mutation**
   - Semantic target identity is held only in Electron main and actions recompute candidate index, visibility, and fingerprint immediately before operating.
   - A hostile page can still race DOM mutation after the final validation or replace a control with an identical fingerprint at the same bounded index.
   - Refresh the semantic snapshot and retry if a target changes or disappears. Revisit before automating untrusted consequential flows without supervision.

2. **Cross-origin frames and unusual web applications**
   - Semantic snapshots cover the accessible top-level document and common visible links, buttons, inputs, textareas, selects, editable elements, and tab-indexed controls.
   - Cross-origin iframe content, closed shadow DOM, canvas controls, anti-bot challenges, and unusual framework widgets may be unavailable or ambiguous.
   - The MVP fails explicitly rather than accepting caller selectors or arbitrary JavaScript.

3. **Consequential click policy remains above the browser primitive**
   - The fixed `click` primitive can activate a visible page control. It does not understand business meaning such as “submit application,” “purchase,” or “send message.”
   - User intent and the agent approval policy must gate consequential external actions. Phase 7 does not add a generalized semantic approval engine.
   - Escalate immediately if JobOS will be allowed to perform irreversible external actions unattended.

4. **Single-process idempotency serialization**
   - Per-key locks prevent duplicate execution inside the one local API process and are cleaned up after use.
   - They are not distributed reservations and do not coordinate multiple API processes or hosts.
   - Revisit before multi-process, remote, shared, or multi-user deployment.

5. **Crash-window chronology repair requires replay**
   - The durable mutation ledger and visible conversation chronology are separate SQLite writes.
   - If the process stops between them, replaying the same command deterministically restores the missing activity row without repeating the side effect.
   - Until replay occurs, that one activity can be temporarily absent from the visible chronology. Merge both writes transactionally if this appears in field use or audit requirements become strict.

6. **Capability availability waits for browser restoration**
   - The desktop does not advertise browser-command readiness until restored tabs finish their initial load settlement, preventing commands from discarding or racing the saved layout.
   - A very slow or hung restored page may delay capability availability; the desktop UI remains the recovery surface.
   - Revisit with bounded per-tab restoration timeouts only if this occurs in normal use.

7. **One authenticated desktop lease**
   - Phase 7 supports one configured desktop, a short heartbeat lease, reconnect, and immediate offline failure with no queue.
   - There is no device fleet, multi-desktop routing, durable offline queue, or remote browser worker.
   - This is intentional. Reopen only if the product boundary changes.

## Escalation triggers

Reopen Phase 7 hardening if any of these occur:

- a click/type action operates on a visibly different control than the latest snapshot described;
- a normal page repeatedly returns stale-target errors despite a fresh snapshot;
- duplicate external side effects occur from one idempotency key;
- a completed MCP action is missing from chronology after retry;
- browser restoration routinely prevents the desktop capability from becoming available;
- a document from one job appears while another job is selected;
- the API becomes multi-process, remote-accessible, shared, or multi-user;
- JobOS is authorized to perform irreversible submissions, purchases, messages, or similar external actions unattended.

## Verified evidence informing this disposition

- Desktop suite: 101 passed across 18 files.
- Python suite: 273 passed.
- Lint, Ruff, TypeScript, contract generation, Electron/preload build, Vite production build, packaged-renderer verification, and staged diff checks passed.
- Native MCP-to-Electron proof completed browser typing, clicking, page-state verification, scrolling, job mutation, artifact render/register/select, and exact-one activity reporting.
- Independent review confirmed the main-process target map, restore readiness, per-key locking, chronology repair, artifact ownership, and stale-socket hardening. The final activity-identity collision and stale tests were fixed and reverified.

## Boundary

This disposition closes the Phase 7 MVP without pretending the web is deterministic or turning JobOS into an unattended generalized automation platform. Preserve these limitations as documented debt and fix them only when field evidence or a product-boundary change justifies the complexity.