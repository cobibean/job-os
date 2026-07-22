# Browser Save persistence debug handoff - 2026-07-22

## Session summary

The visible JobOS **Save job** flow is not accepted. On the Mac Mini, JobHunter can inspect and scroll the live listing, but the latest fresh run did not call `mcp__jobos__job_create_from_browser` or `mcp__jobos__browser_tab_associate`; no canonical job was created and the source tab remained unassociated. The UI correctly showed an error rather than false success. The MacBook additionally experienced `ERROR_TAB_NOT_FOUND`; a real multi-device routing defect was found and patched, but not functionally retested on the MacBook.

There is no commit or push. The worktree contains a broad uncommitted Save-flow patch set; inspect `git diff` and do not reset it.

## Controlling product decisions

- Keep JobHunter's general-purpose `browser_click` and `browser_type` MCP tools for future features. They were restored in source/tests and the JobHunter gateway was restarted; its 2026-07-22 08:59 CDT log registered 34 JobOS tools.
- Do not solve Save by deleting browser capabilities.
- Do not build, package, distribute, or describe another updater as fixed until the exact installed visible-button flow creates/deduplicates a real job, associates the exact source tab, and displays Saved.
- Keep the flow agent-driven; do not restore site-specific Electron/renderer extraction.

These rules and the multi-device contract are now in repo-root `AGENTS.md`.

## Multi-device routing contract

The old `CapabilityBroker` stored one desktop, allowing the last-connected machine to receive another device's browser commands. The patched contract is:

```text
authenticated desktop device_id
→ conversation context origin_device_id
→ active turn context in state store
→ MCP POST /v1/browser/commands
→ CapabilityBroker.execute(..., device_id=origin_device_id)
→ exact originating capability socket
```

Relevant files:

- `services/api/jobos_api/capabilities.py`: per-device desktop registry, targeted execution, socket-bound pending commands.
- `services/api/jobos_api/app.py`: saves `origin_device_id` in conversation context and selects it for MCP browser commands.
- `services/api/jobos_api/state_store.py`: retrieves the active turn's origin device.
- `services/api/tests/test_browser_capability.py`: multi-device and originating-turn regressions.
- `AGENTS.md`: durable non-regression rule.

Focused browser-capability suite on 2026-07-22: `14 passed`.

## Required Save contract

```text
visible button[aria-label="Save this job to JobOS"]
→ exact active source tab ID
→ JobHunter turn ID + originating device ID
→ browser_snapshot / browser_scroll as needed
→ mcp__jobos__job_create_from_browser
→ returned created/deduplicated canonical job ID
→ mcp__jobos__browser_tab_associate(source tab ID, same job ID)
→ tab.associated_job_id == job ID
→ visible Saved / Already in JobOS
```

A terminal turn without a turn-correlated association event is failure. Existing, manual, or unrelated jobs/associations do not count.

## What was tried

1. Replaced the prior deterministic Wellfound extraction path with a JobHunter turn scoped to the exact active tab.
2. Added `job_create_from_browser`, `browser_tab_associate`, API validation for unknown job IDs, navigation invalidation, snapshot/scroll hardening, and renderer false-success protections.
3. Fixed per-device capability routing and propagated `origin_device_id`.
4. Strengthened the Save prompt with fully qualified mutation tool names.
5. Repeated exact-installed-app acceptance through `/Users/jacobilangemm/Applications/JobOS.app` using the visible Save control. Snapshots and scrolling worked on the Mini; persistence did not.
6. Restarted JobHunter, reset stale conversations, and confirmed the MCP server registered the mutation tools. A fresh turn still chose `job_list` and ended `JOBOS_SAVE_RESULT:ERROR_REQUIRED_TOOL_UNAVAILABLE` without attempting creation.
7. Temporarily removed `browser_click`/`browser_type` to prevent navigation drift. That did not fix persistence. The removal was reverted at Cobi's direction; source/tests/runtime now expose both again.
8. Configured `/Users/jacobilangemm/.hermes/profiles/job-hunter/config.yaml` with `platform_toolsets.jobos: [jobos]` to keep JobOS-origin sessions scoped to the JobOS MCP server. This profile-local diagnostic configuration remains and must be considered during diagnosis.

## Verification retained

- `uv run pytest services/mcp/tests/test_jobs_tools.py::test_mcp_server_exposes_phase_seven_parity_tools_while_retaining_job_tools -q` → passed.
- `cd services/api && uv run pytest tests/test_browser_capability.py -q` → 14 passed.
- `git diff --check` → passed.
- Earlier in the broader run: full API suite, desktop typecheck, focused renderer Save test, package signature/integrity checks passed, but none prove Save persistence.
- Latest accepted persistence result: **failed**. No job ID and no exact source-tab association.

## Current runtime evidence

- Installed target: `/Users/jacobilangemm/Applications/JobOS.app`
- JobOS API: `http://127.0.0.1:8766`
- JobHunter gateway profile: `/Users/jacobilangemm/.hermes/profiles/job-hunter`
- JobHunter log: `/Users/jacobilangemm/.hermes/profiles/job-hunter/logs/agent.log`
- At 08:59 CDT the restarted gateway registered 34 JobOS MCP tools, including `job_create_from_browser`, `browser_tab_associate`, `browser_click`, and `browser_type`.
- MacBook was offline during the last capability check, so the routing patch lacks real MacBook acceptance.

## Useful harnesses and evidence

- `/Users/jacobilangemm/.hermes/profiles/devonte/cache/jobos-agent-save-acceptance.mjs`
- `/Users/jacobilangemm/.hermes/profiles/devonte/cache/jobos-installed-save-monitor.mjs`
- `/Users/jacobilangemm/.hermes/profiles/devonte/cache/jobos-navigate-acceptance.mjs`
- `/Users/jacobilangemm/.hermes/profiles/devonte/cache/images/img_7fa26087e1a8.png` (MacBook snapshot failure)

Any updater ZIP already present is diagnostic and unaccepted. Do not repackage before persistence passes.

## Recommended diagnosis

Start root-cause-first. Do not add more prompt wording until proving what tool schemas the model actually receives for a `platform=jobos` turn. Compare:

1. MCP registration log vs the exact provider request tool schema/allowlist.
2. JobHunter `platform_toolsets.jobos` resolution vs global MCP discovery.
3. Whether `job_create_from_browser` is omitted, policy-gated, approval-gated, malformed, or simply ignored by the model.
4. Whether a narrow atomic `save inspected listing and associate tab` capability is needed; if considered, preserve agent extraction and the required canonical persistence/association evidence rather than restoring site-specific parsing.

Add a regression that fails when a Save turn can finish without either a valid association event or an explicit actionable failure tied to the missing mutation. Then rerun exact installed visible-button acceptance. Only after Mini persistence passes should the MacBook routing fix be packaged and tested.

## Suggested skills

- `systematic-debugging`
- `diagnosing-bugs`
- `hermes-agent`
- `project-memory`
- `requesting-code-review` before commit
