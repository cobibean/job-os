# Agent Turn Output Ordering Memory - 2026-07-24

## Session summary

Implemented the renderer-only agent-turn ordering plan. JobOS now presents each owned agent turn as one visual run: grouped activity first, an explicit terminal notice when applicable, and one assistant response last.

The activity group stays open while a turn is active, defaults closed after completion, preserves manual disclosure choices, and reports partial, waiting, stopping, failed, and interrupted states truthfully. A persistent status strip above the composer keeps working/waiting/stopping state and Stop visibility in view.

## Architecture and decisions

- The persisted `ConversationEvent` and backend/API contracts were not changed.
- `projectConversation()` now builds a `ProjectedAgentTurn` per `turnId` in one chronological pass.
- Activity phases remain deduplicated by `activity_id`, scoped per turn so equal IDs cannot merge across turns.
- Assistant deltas remain coalesced and `message.complete` replaces the accumulated draft.
- Internal visual order is fixed as activity group, terminal notice, assistant response.
- Ownerless assistant events remain filtered as defense against old restart pollution.
- Valid ownerless MCP activity and terminal records remain visible, preserving pre-existing behavior.
- Waiting and terminal state remains authoritative over late activity updates; later terminal completion can supersede an earlier terminal event.
- Activity summaries and icons distinguish partial completion, waiting, stopping, failed, interrupted, and completed states.
- Scroll follows output only while pinned within 64px of the bottom. Detached readers get a `Jump to latest` control, and a fresh session resets the detached state.

## Files changed

- `apps/desktop/src/renderer/hooks/useAgentConversation.ts`
- `apps/desktop/src/renderer/hooks/useAgentConversation.test.tsx`
- `apps/desktop/src/renderer/components/AgentActivityGroup.tsx`
- `apps/desktop/src/renderer/components/ActivityRow.tsx`
- `apps/desktop/src/renderer/components/AgentPanel.tsx`
- `apps/desktop/src/renderer/components/AgentPanel.test.tsx`
- `apps/desktop/src/renderer/styles.css`

The unrelated existing edit in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` remained untouched and unstaged.

## Verification and review

- Focused projection/component suites: 58 tests passed.
- Full desktop suite: 207 tests passed across 26 files.
- Full Python suite: 339 passed, 1 skipped.
- Repository lint, typecheck, production build, packaged-renderer verification, and `git diff --check` passed through `pnpm check`.
- Static added-line security scan found no hardcoded-secret, shell-injection, eval/exec, unsafe-deserialization, or SQL-formatting concerns.
- Independent Codex review at medium reasoning passed with no security concerns, logic errors, or suggestions after edge-case corrections.

## Acceptance boundary

Source implementation and automated verification are complete. The exact installed JobOS application was not packaged, installed, or visually accepted because the plan explicitly requires separate approval before packaging or distribution. Do not claim installed-app acceptance from these source checks.
