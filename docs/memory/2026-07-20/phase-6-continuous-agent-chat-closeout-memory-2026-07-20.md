# Phase 6 Continuous Agent Chat Closeout Memory - 2026-07-20

## Phase status

Phase 6 is closed as a verified single-user MVP. Cobi explicitly chose MVP closure over continuing an unbounded adversarial-review loop after the core product path worked end to end. Six low-probability hardening findings are accepted and documented in `phase-6-mvp-review-disposition-memory-2026-07-20.md`.

## What shipped

- One continuous JobHunter/Hermes conversation across selected-job changes.
- Backend-owned `AgentGateway`; renderer never connects to Hermes or owns Hermes credentials.
- Durable SQLite conversation, turn, transcript, activity, cursor, connection, and recovery state.
- Ordered cursor-based SSE with replay and `Last-Event-ID` resume.
- Distinct chronological activity identities with compact expandable native rows.
- Send, Stop, Retry, reconnect/offline/waiting/interrupted/failure presentation.
- Relaunch restoration and durable Hermes reattachment/recovery handling.
- Redaction and purpose-specific output bounds before new persistence and renderer delivery.
- Electron main/preload/renderer integration with a self-contained sandbox-compatible production preload.
- Restrained three-pane native UI that keeps the center workspace dominant.
- No Phase 7 browser-command parity.

## Major runtime discoveries

- Hermes uses one JSON-RPC object per WebSocket text frame and unsolicited `method: "event"` envelopes.
- Hermes provides no replay sequence/cursor; JobOS owns durable ordering and replay.
- Live `session_id` and durable `stored_session_id` are distinct; stored identity can rotate.
- Prompt acknowledgement is not completion; cancellation/error may terminate without `message.complete`.
- Hermes profile reporting is lazy and can return the dashboard launch profile before/after a verified `job-hunter` attachment.
- Isolation proof is sticky only for the same live attachment; reconnect/rebind requires fresh proof.
- Reusing a verified live attachment is required for continuous-conversation and Stop semantics.
- Production Electron sandboxed preload could not safely load emitted sibling bridge modules; the final preload is self-contained.

## Real runtime acceptance evidence

Harmless live turn through JobOS API → AgentGateway → Hermes:

- submit HTTP `201`;
- terminal state `completed`;
- exact assistant marker matched;
- activity completed;
- JobOS cursors were monotonic;
- connection state was online;
- protected device and Hermes credentials were absent from the isolated proof SQLite database.

Live Stop proof:

- submit HTTP `201`;
- real terminal activity observed before cancellation;
- cancel HTTP `200`;
- returned and persisted state `interrupted`;
- active turn cleared;
- intentionally late reply remained absent.

Relaunch and SSE proof after the final implementation pass:

- 97 prior entries and cursor 97 restored;
- no stale active turn restored;
- same conversation and prior history preserved;
- first post-relaunch turn completed with the exact expected response;
- replay IDs were exact, ordered, and duplicate-free;
- `Last-Event-ID` resume returned no consumed events;
- retry directive was present.

Activity cardinality proof:

- fifteen real Hermes tool invocations;
- thirty lifecycle events;
- fifteen unique activity identities;
- one working and one completed event per identity;
- native production screenshot showed compact distinct rows rather than a merged activity.

Native Electron proof:

- production main/preload/renderer launched against the authenticated private API;
- transcript and activity hydrated;
- historical interrupted turns no longer claimed to be streaming;
- completed relaunch reply and settled interruption/Retry state rendered correctly;
- composer remained usable;
- no blocking clipping, overflow, or hierarchy regression was observed.

## Final automated verification

Final post-fix execution evidence:

- Python: `256 passed`.
- Desktop: `91 passed` across 17 files.
- Contracts and desktop lint: passed.
- Ruff: passed.
- TypeScript typecheck: passed.
- Production Electron/renderer build: passed; 1,794 modules transformed.
- Self-contained preload artifact verifier: passed.
- Packaged-renderer verifier: passed.
- Contract generation: stable.
- `git diff --check`: passed.
- Node gates ran with Node.js 26.5.0.

## Review history and MVP decision

Three fail-closed review rounds produced useful hardening, including:

- stronger credential redaction;
- atomic terminal-state settlement;
- startup recovery quarantine and remote interruption;
- safer reconnect/offline handling;
- durable session identity persisted before submission;
- renderer hydration dedupe;
- live routing metadata removed from new normalized event detail;
- long user-message bounds separated from generic detail bounds;
- cancel response handling that retains nonterminal recovery state.

The final review still identified six edge cases. They are not represented as fixed. Cobi accepted them as post-MVP debt because JobOS is local, authenticated, loopback-only, single-user software and the main flow was repeatedly proven. See `phase-6-mvp-review-disposition-memory-2026-07-20.md` for exact risks, workarounds, and escalation triggers.

The most important field trigger is a composer or active-turn indicator that remains stuck after a completed turn. If observed, relaunch restores backend truth; the renderer mutation/SSE ordering race should then become the first hardening task.

## Files and boundaries

Primary implementation areas:

- `services/api/jobos_api/agent_gateway.py`
- `services/api/jobos_api/hermes_adapter.py`
- `services/api/jobos_api/conversations.py`
- `services/api/jobos_api/activity.py`
- `services/api/jobos_api/redaction.py`
- `services/api/jobos_api/state_store.py`
- `services/api/jobos_api/app.py`
- `apps/desktop/src/main/agent.ts`
- `apps/desktop/src/main/agentIpc.ts`
- `apps/desktop/src/preload/preload.cts`
- `apps/desktop/src/renderer/hooks/useAgentConversation.ts`
- `apps/desktop/src/renderer/components/AgentPanel.tsx`
- `apps/desktop/src/renderer/components/ActivityRow.tsx`
- shared/generated contracts and focused backend/desktop tests.

Temporary proof credentials, databases, and proof API processes were removed/stopped after evidence was captured. No credential values are recorded here.

## Next phase boundary

Phase 7 may add browser-command parity only under a new scoped plan. Do not fold the accepted Phase 6 hardening backlog into Phase 7 unless one of the documented escalation triggers occurs or JobOS moves beyond its single-user local MVP boundary.
