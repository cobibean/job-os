# JobOS V1 Phase 6 Desktop Major Slice — In-Progress Memory — 2026-07-20

## Checkpoint status

- **IN PROGRESS:** the Electron main/preload and renderer/UI major slice from Phase 6 Tasks 6–7 is implemented and locally verified.
- This is not Phase 6 acceptance or closeout. A 1440×960 production-built native Electron capture exposed a preload packaging failure, and a second direct production launch with Electron logging proved the first correction insufficient. A later user-provided production screenshot proves the self-contained preload correction connected and hydrated the Agent Panel; disposable live Hermes proof, relaunch/disconnect proof against a running API, and the integrated golden path remain deliberately unrun.
- No live Hermes prompt was submitted. No services were restarted. No credential or live JobHunter data/workspace was read or changed. No Phase 7 browser-command parity was added. No commit or push was made.
- The existing readiness and backend memory files were not altered by this desktop slice.

## Implemented desktop boundary

- Electron main owns the device token, generated OpenAPI client, authenticated HTTP calls, and resumable conversation SSE connection. The renderer receives no device/Hermes token, URL/method/header capability, raw Hermes frame, Hermes routing session ID, or unknown protocol field.
- The typed main client maps the generated current-conversation, send, cancel, and retry contracts into renderer-owned camel-case types and bounded normalized detail.
- The SSE decoder handles CRLF and arbitrary split chunks, ignores malformed frames, resumes with the latest durable `after` cursor, suppresses snapshot/stream and reconnect duplicates by event ID, and reconnects with bounded exponential backoff.
- Electron fetches the durable conversation cursor before opening the event stream without blocking window creation. Initial agent offline state remains distinct from event-stream transport reconnection.
- Fixed IPC handlers validate bounded message text, idempotency keys, and turn IDs. The deeply frozen preload bridge exposes only `get`, `send`, `cancel`, `retry`, and removable normalized-event subscription operations.

## Native production preload failures and resolution

- Native production evidence showed the otherwise correct three-pane shell with the Agent Panel reporting `JobOS API offline` / `Agent is available in the desktop app`, while Electron main successfully received HTTP 200 responses from `/v1/conversations/current` and `/events/stream`. `window.jobos` was absent because Electron could not load the preload.
- The emitted `dist/preload/preload.cjs` used CommonJS `require('./agentBridge.js')`, but `dist/preload/agentBridge.js` contained ESM `export` syntax under the desktop package's `"type": "module"`. The preload dependency graph was therefore not CommonJS-loadable in production despite passing source-level tests.
- The first correction renamed the helper source from `.ts` to `.cts`, producing `agentBridge.cjs`. A second direct production launch with `ELECTRON_ENABLE_LOGGING=1` then proved Electron's sandbox preload loader still rejected that valid local dependency: it reported `Unable to load preload script: .../dist/preload/preload.cjs` and `Error: module not found: ./agentBridge.cjs`. The resulting screenshot was byte-identical to the first failure and `window.jobos` remained absent.
- The actual resolution removes the local preload dependency entirely. The small five-method frozen agent bridge is inlined in `preload.cts`, while its contract references remain type-only. `dist/preload/preload.cjs` is now a self-contained sandbox-compatible preload whose sole runtime module request is Electron itself; the duplicated helper source and helper-only test were removed.
- `apps/desktop/scripts/verify-preload-artifact.mjs` statically rejects every relative runtime `require()` in `dist/preload/preload.cjs`, then executes that exact compiled artifact in a VM with a custom loader that permits only mocked `electron`. It proves one `contextBridge.exposeInMainWorld('jobos', bridge)` call, a frozen agent bridge with exactly `get`, `send`, `cancel`, `retry`, and `subscribe`, fixed IPC channel routing, normalized subscription delivery, and listener removal. This catches both the original ESM edge and the sandbox-local-require defect and runs after every Electron TypeScript build and before the desktop test suite.
- The resolution does not change context isolation, renderer capability shape, IPC channels, credential/URL ownership, or Node exposure. No service was restarted. The later production screenshot shows the native Agent Panel connected and hydrated, proving the preload recovery while also exposing the historical transcript-state defect recorded below.

## Native hydrated screenshot and terminal transcript-state correction

- The user-provided production screenshot after the preload correction shows a connected, hydrated Agent Panel with durable transcript content. It also shows one interrupted historical turn whose earlier assistant placeholder still carries the purple `Streaming` label immediately before the correct `Turn interrupted` card and Retry control, even though `active_turn` is null. This is screenshot evidence of a renderer projection defect, not a preload, hydration, or backend-state defect.
- Strict component RED added three focused scenarios. The interrupted and failed historical fixtures preserve streamed draft text, activity/ordering where present, terminal card, and Retry while requiring no `Streaming`; both failed against the prior renderer because the purple `Streaming` span remained. The matching active-turn fixture passed and continues to require `Streaming` for the current running turn.
- GREEN derives a per-turn terminal map from chronological durable entries. A later terminal status, error, or assistant completion settles the earlier assistant placeholder's visual state without mutating the entry array or removing content. A working assistant item may render `Streaming` only when its turn ID matches the current `activeTurn` and no later terminal event exists.
- The same active-turn rule now prevents a stale historical waiting entry from visually claiming `Waiting for you`; it is presented as the settled historical label `Turn paused`. Existing live waiting behavior remains unchanged for the matching active turn.
- No layout or stylesheet redesign was needed. The correction is confined to `AgentPanel` projection/rendering, focused component tests, and this desktop memory. It did not touch backend code, submit a prompt, restart a service, commit, or push.

### Terminal transcript correction verification

- Focused RED: `pnpm --filter @jobos/desktop exec vitest run src/renderer/components/AgentPanel.test.tsx` → `2 failed`, `6 passed`; only the interrupted and failed historical `Streaming` assertions failed.
- Focused GREEN: the same command → `1 passed` test file, `8 passed` tests.
- All desktop tests: `pnpm --filter @jobos/desktop test` → `17 passed` test files, `83 passed` tests; its Electron build also passed the preload artifact verifier.
- Desktop lint: `pnpm --filter @jobos/desktop lint` → passed with no findings.
- Desktop typecheck: `pnpm --filter @jobos/desktop typecheck` → both renderer/shared and Electron TypeScript checks passed.
- Production desktop build: `pnpm --filter @jobos/desktop build` → passed; 1,794 modules transformed and production renderer assets emitted.
- Preload artifact verifier: `node apps/desktop/scripts/verify-preload-artifact.mjs` → passed independently with no output.
- Packaged-renderer verifier: `node scripts/verify-packaged-renderer.mjs` → passed independently with no output.

## Implemented renderer behavior

- `useAgentConversation` hydrates from the durable snapshot, safely merges early/duplicate stream events, retains one conversation independently of selected-job changes, and keeps a draft in the stable mounted panel.
- Assistant `message.start`/`message.delta` events project to one streaming response per turn. `message.complete` replaces the accumulated projection with the durable final text, avoiding duplicate completion text.
- Activity lifecycle events project by `activity_id`: start/progress/complete update one row while distinct tool identities remain distinct and chronological. The deterministic 15-tool fixture renders exactly 15 rows.
- The placeholder panel is replaced with a continuous transcript, compact collapsed activity rows, safe expanded detail, anchored multiline composer, streaming/waiting/completed/failed/interrupted presentation, Stop and Retry controls, and restoring/empty/API-offline/agent-offline/reconnecting states.
- Drafting remains available during an active turn while Send is serialized. Enter sends only when eligible; Shift+Enter preserves multiline drafting. Job selection changes update only the lightweight context header.
- New terminal/waiting/connection state is announced through a polite atomic live region. Controls have visible semantic labels, disclosures expose `aria-expanded`/`aria-controls`, and the transcript auto-scrolls only while the user remains near the bottom.
- Styling preserves the locked dark three-pane hierarchy: restrained right-column chrome, compact bordered rows, modest radii, quiet state color, dominant center work surface, and no dashboard/chatbot shell.

## Exact desktop implementation files

- Created: `apps/desktop/src/main/agent.ts`
- Created: `apps/desktop/src/main/agent.test.ts`
- Created: `apps/desktop/src/main/agentIpc.ts`
- Created: `apps/desktop/src/main/agentIpc.test.ts`
- Modified: `apps/desktop/src/main/main.ts`
- Modified: `apps/desktop/src/main/connectivity.ts`
- Modified: `apps/desktop/src/main/connectivity.test.ts`
- Modified: `apps/desktop/src/preload/preload.cts`
- Created: `apps/desktop/scripts/verify-preload-artifact.mjs`
- Modified: `apps/desktop/package.json`
- Modified: `apps/desktop/src/shared/contracts.ts`
- Created: `apps/desktop/src/renderer/hooks/useAgentConversation.ts`
- Created: `apps/desktop/src/renderer/hooks/useAgentConversation.test.tsx`
- Created: `apps/desktop/src/renderer/components/ActivityRow.tsx`
- Replaced: `apps/desktop/src/renderer/components/AgentPanel.tsx`
- Created: `apps/desktop/src/renderer/components/AgentPanel.test.tsx`
- Modified: `apps/desktop/src/renderer/App.tsx`
- Modified: `apps/desktop/src/renderer/App.test.tsx`
- Modified: `apps/desktop/src/renderer/styles.css`
- Created: `docs/memory/2026-07-20/phase-6-desktop-major-slice-memory-2026-07-20.md`

## TDD evidence

- Main RED failed collection because `agent.ts` and `agentIpc.ts` did not exist. GREEN covers generated-contract mapping, chunk-split/CRLF SSE, durable cursor reconnect, duplicate suppression, fixed IPC operations, validation, and credential/raw-frame exclusion.
- The earlier preload RED failed collection because `agentBridge.ts` did not exist. Its initial GREEN proved fixed channels and a frozen removable-subscription facade, but the second native proof showed that source/helper coverage was insufficient for Electron's sandbox loader.
- The strengthened production-artifact RED ran against the native-failing `.cts`/`.cjs` output and rejected `require("./agentBridge.cjs")` because a sandboxed production preload must be self-contained. GREEN inlined the facade, removed the dead helper and helper-only test, statically proved zero local runtime requires, and executed the compiled artifact through the restricted VM loader to prove bridge exposure and behavior.
- Reducer/hook RED failed collection because `useAgentConversation.ts` did not exist. GREEN covers snapshot/early-stream overlap, 15 activity identities, delta completion replacement, restoration/subscription, serialized send, draft preservation, Stop/Retry, actionable errors, and reconnect state.
- Component RED ran against the Phase 1 placeholder and failed all five transcript/composer/state/accessibility scenarios. GREEN covers transcript/activity disclosure, 15 rows, keyboard composer behavior, job-change draft continuity, waiting/Stop, failed/Retry, API-versus-agent offline state, restore, reconnect, and trust-boundary copy.
- App coverage proves selected-job changes preserve the mounted conversation, transcript, and draft and load the singleton conversation only once.
- The managed sandbox forbids loopback binding. Existing connectivity tests were made transport-injectable and now use in-memory authenticated `Response` fixtures, preserving their production behavior while removing an environment-only port dependency.

## Exact verification

- Focused agent tests plus compiled preload verifier after the sandbox resolution: `2 passed` test files and `5 passed` tests; the artifact VM/static verifier passed separately in the same command.
- `pnpm --filter @jobos/desktop test` → `17 passed` test files, `80 passed` tests. The one-file/test reduction is the intentionally removed duplicated helper-only test; compiled preload behavior is now checked by the artifact verifier.
- `pnpm --filter @jobos/desktop lint` → passed with no output from `oxlint`.
- `pnpm --filter @jobos/desktop typecheck` → renderer/shared and Electron main/preload TypeScript checks passed.
- `pnpm --filter @jobos/desktop build` → Electron TypeScript, self-contained production preload static/VM verification, and production Vite build passed; 1,794 modules transformed.
- `node apps/desktop/scripts/verify-preload-artifact.mjs` → passed independently after the production build.
- `node scripts/verify-packaged-renderer.mjs` → passed after the production desktop build.
- `UV_CACHE_DIR=/tmp/jobos-phase6-uv-cache pnpm check` → passed completely: contracts and desktop lint; generated contracts; contract and desktop typechecks; 81 desktop tests; 206 Python tests; production build; packaged-renderer verification.
- `pnpm --filter @jobos/contracts lint` → passed.
- `pnpm --filter @jobos/contracts build` → passed.
- `pnpm --filter @jobos/contracts typecheck` → passed.
- `pnpm contracts:check` → generation passed, then the drift assertion failed because the correct Phase 6 OpenAPI/generated TypeScript files differ from committed Phase 5 `HEAD`. This is the expected limitation of a requested uncommitted checkpoint and is not claimed green.
- `git diff --check` → passed after this memory file.

## Remaining live/native and acceptance proof

1. Complete visual review in Research, Review, and Agent Focus layouts, including long activity labels, expanded detail, waiting/error states, and manual-scroll behavior. The user-provided production screenshot has already proved the self-contained preload recovery and connected Agent Panel hydration.
2. Run the production-built native Electron app against a disposable Mini API configuration and prove snapshot restoration, same-conversation job changes, cursor recovery, Stop/Retry, agent offline/reconnect, and exactly 15 activities through the full native stack.
3. Perform the separately approved harmless live Hermes proof with protected before/after fingerprints. This checkpoint did not submit any prompt or contact Hermes.
4. Complete the Phase 6 integrated golden path only after the live JobHunterFacade/runtime prerequisites remain verified, without mutating protected user data or expanding into Phase 7.
5. Run clean committed-candidate contract drift, frozen clean-room install/check, gitleaks with redacted output, and final closeout memory/PM handoff after live/native evidence exists.

## Handoff constraints

- Keep device and Hermes credentials, authenticated URLs, raw frames, session routing identities, and unrestricted detail out of renderer contracts, logs, screenshots, and project files.
- Keep using fake transports and durable API fixtures for automated tests. Do not turn unit tests into live Hermes prompts.
- Preserve one conversation across job selection. Keep submission serialized while allowing drafting.
- Do not change readiness/backend memories, touch live JobHunter data/workspaces, restart services, or begin Phase 7 browser-command parity during remaining Phase 6 proof.

## Independent-review desktop blocking-fix checkpoint (not closeout)

This addendum supersedes the earlier statement that Electron fetches a cursor
snapshot before opening SSE. Electron no longer performs that independent snapshot
read. It opens the durable conversation stream from cursor zero; the renderer keeps
its existing fixed frozen preload `get`/subscription surface, merges by event ID, and
reconciles streamed events newer than the hydration snapshot cursor. This closes the
snapshot/stream startup gap without renderer credentials, URLs, or transport
capabilities.

- Only terminal assistant/status/error entries for the matching turn clear
  `activeTurn`; a completed activity row cannot enable Send or remove Stop while the
  Hermes turn is still running.
- Hydration folds newer early-stream terminal and gateway-connectivity events over
  its snapshot, so a stale snapshot cannot resurrect a turn or overwrite a newer
  agent-offline transition.
- SSE transport reconnection uses `reconnecting`; it no longer reports Hermes
  `offline`. Durable `agent_connection` entries and the conversation snapshot own
  agent state, while the existing shell connectivity surface continues to own API
  reachability.
- When the API is connected, Send remains available for an initially offline agent.
  That fixed action reaches the backend reconnect/resume path; active-turn
  serialization remains unchanged.

### Desktop review-fix TDD and verification evidence

- Initial desktop RED:
  `pnpm --filter @jobos/desktop exec vitest run
  src/renderer/hooks/useAgentConversation.test.tsx src/main/agent.test.ts` → `3
  failed, 10 passed`; activity completion cleared the turn, stale hydration restored
  it, and offline Send never called IPC.
- Additional connectivity-hydration RED reported `1 failed` hook test while the
  offline-Send component control passed; the stale snapshot overwrote the newer
  durable offline event.
- Focused GREEN: agent main/IPC, hook, and panel tests → `4 passed` test files and
  `24 passed` tests.
- All desktop tests: `pnpm --filter @jobos/desktop test` → `17 passed` test files and
  `89 passed` tests; Electron compilation and the preload verifier also passed.
- Root lint and root typecheck passed. Production `pnpm build` passed, including
  contract generation, contract build, Electron build, self-contained preload
  verification, 1,794-module Vite production build, and packaged-renderer
  verification.
- `node apps/desktop/scripts/verify-preload-artifact.mjs` and `node
  scripts/verify-packaged-renderer.mjs` each passed independently with no output.
- Contract generation was byte-stable across before/after SHA manifests, and `git
  diff --check` passed with no output.

## Final bounded cancel-state review checkpoint (not closeout)

The renderer now clears `activeTurn` from a Stop response only when the API reports
`completed`, `failed`, or `interrupted`. A `running` or `waiting` response means
remote cleanup is not confirmed: the matching turn remains active, its cancellation
request is shown in state, drafting remains available, and Send/Retry stay blocked.
This checkpoint changes no renderer transport surface and exposes no URL, credential,
or session-routing identity.

- Desktop RED:
  `pnpm --filter @jobos/desktop exec vitest run
  src/renderer/hooks/useAgentConversation.test.tsx -t 'stop retains'` -> `2 failed,
  9 skipped`; both running and waiting cancel results incorrectly cleared the turn.
- Focused GREEN: the full hook file passed `11` tests.
- All desktop tests: `pnpm --filter @jobos/desktop test` -> `17` files and `91`
  tests passed, including Electron compilation and the compiled preload verifier.
- Root desktop/contracts lint, typecheck, and production build passed. The standalone
  preload and packaged-renderer verifiers also passed, contract generation remained
  byte-stable, and `git diff --check` passed.
