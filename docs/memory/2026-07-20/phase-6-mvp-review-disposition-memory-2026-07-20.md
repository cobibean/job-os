# Phase 6 MVP Review Disposition Memory - 2026-07-20

## Decision context

Phase 6 is a local, single-user MVP for Cobi. Core behavior has been exercised against the real Hermes runtime and production Electron surface: continuous conversation, durable relaunch restoration, prompt completion, Stop/interruption, Retry presentation, ordered resumable SSE, distinct activity rows, native preload isolation, and redaction/bounds checks.

The final adversarial review continued to identify low-probability lifecycle and scale edge cases after two bounded correction passes. This note separates those findings from MVP launch blockers so later agents do not restart an unbounded review loop.

## Recommended MVP disposition

**Close Phase 6 and carry the six findings below as accepted post-MVP hardening debt.**

Reason: none invalidates the demonstrated single-user happy path, corrupts durable source data, exposes credentials to an untrusted remote client in the intended deployment, or prevents recovery by relaunching the local application. Continued review has diminishing returns for this MVP.

## Accepted post-MVP hardening debt

1. **Raw Hermes frame/session scoping**
   - Some supported top-level frames are accepted without the strongest possible live-session envelope check.
   - MVP risk is low because Hermes is authenticated, loopback-only, and used by one local operator rather than multiple tenants.
   - Revisit before multi-user, remote, or shared-runtime deployment.

2. **Cancellation during deferred session verification**
   - A narrow timing window may allow submission after cancellation while initial session verification is finishing.
   - The normal Stop path was proven live. The remaining race is limited to initial/reconnect attachment timing.
   - If observed, relaunch JobOS/Hermes and retry. Add a final pre-submit active-turn check before broader release.

3. **Over-conservative recovery quarantine**
   - Some definitely pre-submit verification failures may be classified as possibly remote and temporarily block new work.
   - This fails safe rather than allowing overlapping work. Relaunch/recovery is the MVP workaround.
   - Refine failure classification only if it appears in actual use.

4. **Renderer mutation/SSE ordering race**
   - A very fast terminal SSE event may arrive before the send/retry mutation response and briefly leave a stale running indicator.
   - Durable backend state remains authoritative; hydration or relaunch corrects the display.
   - This is the first hardening item to fix if Cobi sees a stuck Stop/Send state.

5. **Prompt edge whitespace is trimmed**
   - Leading/trailing whitespace and intentional terminal newlines are normalized by the current renderer/IPC/API path.
   - Normal conversational prompts are unaffected. Preserve exact whitespace before supporting code-block-sensitive workflows.

6. **Activity identity lifetime and reuse**
   - Activity IDs are retained for the process lifetime and renderer projection does not fully namespace identity by turn.
   - The real fifteen-call proof rendered fifteen distinct rows. Collision or memory-growth risk is negligible at personal-MVP scale.
   - Namespace by `(turn_id, activity_id)` and prune settled normalizer state before sustained/high-volume use.

## Escalation triggers

Reopen Phase 6 hardening only if one of these occurs:

- Stop visibly allows work to continue after cancellation;
- the composer remains stuck after a completed turn;
- reconnect/relaunch cannot recover the conversation;
- activity rows merge or duplicate in normal use;
- JobOS becomes multi-user, remote-accessible, or long-running at high activity volume.

## Verified evidence informing this recommendation

- Full Python suite: 256 passed.
- Desktop suite: 91 passed across 17 files.
- Root lint, Ruff, typecheck, production build, preload verification, packaged-renderer verification, contract generation stability, and `git diff --check` passed.
- Real Hermes relaunch-resume completed with the exact expected response and preserved one conversation/history.
- Real Stop proof observed activity before cancellation, returned interrupted, cleared the active turn, and suppressed the late reply.
- SSE replay was exact, ordered, duplicate-free, and respected the resume cursor.
- Fifteen real tool calls produced fifteen distinct activity identities and compact native rows.

## Boundary

This is an explicit MVP risk disposition, not a claim that the six edge cases were fixed. They remain documented technical debt. Phase 7 browser-command parity remains out of scope.
