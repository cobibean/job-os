# JobOS agent offline: launchd recovery and idle reconnect

Date: 2026-08-12

## User-visible failure

The installed JobOS app showed the agent as offline even though the JobOS API process was running.

## Root cause

Two recovery gaps combined:

1. A Hermes self-update stopped the dedicated dashboard process on port 9120, then relaunched its captured command as a detached process instead of restarting its owning macOS LaunchAgent (`ai.hermes.dashboard-fleet`). The detached process did not inherit the protected launchd session token. Meanwhile launchd repeatedly failed to restart because the detached copy still owned port 9120.
2. Once the dashboard returned, JobOS only retried the Hermes gateway when a user submitted a message. An idle installed app therefore remained offline until the API itself restarted or the user sent again.

## Fixes

### JobOS source

- `services/api/jobos_api/conversations.py`
  - Added a bounded background gateway reconnection loop.
  - The service now retries while idle whenever the gateway reports `offline`.
  - Shutdown cancels both the event consumer and reconnect task cleanly.
- `services/api/tests/test_agent_contract.py`
  - Added a regression test proving an initially unavailable gateway reconnects without a user submission.

### Mac runtime guard

- `~/.hermes/bin/dashboard-fleet-with-token`
  - Before starting the protected port-9120 dashboard, it removes only an unmanaged Hermes dashboard process occupying that exact port.
  - It refuses to kill a process owned by another launchd service.

### Hermes updater source (local checkout)

- The updater now detects launchd ownership and uses `launchctl kickstart -k` rather than detached argv respawn for launchd-managed dashboards.
- Regression tests cover launchd owner lookup and supervised restart behavior.
- This patch remains local to the Hermes checkout; it was not published upstream.

## Verification

- Full JobOS API test suite passed: 368 passed, 1 skipped.
- Hermes updater regression suite passed: 14 passed, 3 skipped.
- Ruff and diff checks passed for both edited code paths.
- A real dashboard restart produced an observable online → offline → online transition while the JobOS API PID stayed unchanged.
- The post-restart dashboard listener PID exactly matched the LaunchAgent-managed PID.
- The installed `~/Applications/JobOS.app` showed `Mac Mini connected`, an enabled agent composer, and a real agent reply (`Yep, I’m here. What do you need?`).

## Operational invariant

The Mac mini JobOS integration uses the dedicated protected Hermes dashboard on loopback port 9120. Ordinary Hermes dashboards remain on 9119. A port-9120 listener is healthy only when its PID matches the PID owned by `ai.hermes.dashboard-fleet`.
