# JobOS V1 Runtime Contract

## Status

Verified read-only on July 19, 2026. This closes the Phase 0 runtime-topology gate. The Phase 0/1 baseline was committed and pushed as `7dafb43eb111a44517a1474a371ae5748f5c73e1`; subsequent PM corrections remain additive to that history.

## Authoritative Mini Route

- SSH target: `jacobilangemm@100.123.109.19`
- Tailscale device: `Jacobi’s Mac mini` (`jacobis-mac-mini.tailf1a3a1.ts.net`)
- Remote host and user: `Jacobis-Mini.home.local`, `jacobilangemm`
- Architecture: Apple silicon (`arm64`), macOS 26.5.1
- MacBook identity file: `/Users/cobibean/.ssh/id_ed25519`
- SSH host keys observed over the verified private Tailscale route:
  - ED25519: `SHA256:T2s4jn4hHEXlfrsKyAAT6f40wGmDOpPa/cLiMhaqqho`
  - ECDSA: `SHA256:g4pD7YeF2G7oQNPx9lro1wILj0yKF9DkQpFYPlcrTFA`
  - RSA: `SHA256:Zja78aJ+5egSUGDh+HzzRjaRKx92wCVQNPtU9s3+a1k`

The MacBook had no persisted `known_hosts` entry for this address during the spike. Inspection used an ephemeral host-key policy after independently reconciling the IP through Tailscale; it did not change the user's SSH files.

## Job-Hunter Authority

- Live checkout: `/Users/jacobilangemm/DEV/agents/job-hunter`
- GitHub remote: `https://github.com/cobibean/job-hunter-agent-workspace.git`
- Branch and commit: `main`, `5b07bbaa`
- Runtime: Python 3.11.15 from `.venv/bin/python`
- System Python 3.9.6 is not the application runtime.
- The checkout contains active user changes and untracked application/resume work. JobOS must not clean, migrate, stage, or modify it during scaffolding.

### Data boundaries

- Authoritative job database: `data/jobs/jobs.db`
- Resume sources and outputs: `resume/`
- Rendered artifacts: `resume/exports/`
- Job-hunter application workspaces: `applications/jobs/`

All are writable by the Mini user, but Phase 0 used them read-only. JobOS V1 must cross the `JobHunterFacade` Seam; no JobOS Module may issue raw SQL or write these paths directly.

### Read-only smoke proof

- `JobStorage(..., initialize=False).list_jobs()` succeeded against the live database.
- The database returned one current job record during the spike (Apollo.io, Account Executive SMB); the result proves the live path, not the eventual V1 fixture size.
- A current resume PDF was found at `resume/exports/wellfound-applied-ai-systems-builder/Jacobi_Lange_Applied_AI_Systems_Builder.pdf`.
- Its observed SHA-256 was `e9307450862da4a51b148bfdd2628f76ea701c1ee152ebb9f9393c0f3908ca56`, and its adjacent render manifest was readable.

## Hermes Runtime Contract

- Install: `/Users/jacobilangemm/.hermes/hermes-agent`
- Version: Hermes Agent 0.18.2 (`2026.7.7.2`)
- Runtime: Python 3.11.15 from `~/.hermes/hermes-agent/venv`
- Live job-hunter profile: `~/.hermes/profiles/job-hunter`
- Job-hunter gateway label: `ai.hermes.gateway-job-hunter`
- Unified desktop backend label: `ai.hermes.dashboard-fleet`
- JobOS integration backend: `127.0.0.1:9120` (dedicated loopback port; ordinary Hermes dashboards retain their default `9119`)
- Health/status: `GET /api/status`
- Structured conversation transport: JSON-RPC over WebSocket at `/api/ws`

The unified backend is deliberately loopback-only. A live, read-only WebSocket handshake returned `gateway.ready`, and `session.list` returned a JSON-RPC result without creating a turn or changing job-search data.

### AgentGateway Adapter surface

The verified Hermes implementation supports the JobOS `AgentGateway` Interface:

- create a live conversation attachment: `session.create`
- submit a turn: `prompt.submit`
- stream text: `message.start`, `message.delta`, `message.complete`
- stream activity: `tool.start`, `tool.progress`, `tool.complete`, plus structured status, approval, clarification, and error events
- recover identity and transcript: `session.resume`, `session.activate`, `session.history`
- cancel current work cooperatively: `session.interrupt`

`prompt.submit` acknowledges with `{"status": "streaming"}`. Completion is event-driven rather than carried by the request response. `message.complete` includes final text and a `complete`, `interrupted`, or `error` status.

The profile's session store was healthy during the spike and reported 60 sessions and 3,587 messages. Phase 0 proves the Mini-hosted topology, gateway availability, session listing, protocol surface, and safe connectivity. It does not require an agent turn from the MacBook development device.

### Phase boundary for live turn proof

The actual Hermes submit, streamed-event, completion, cancellation, and session-recovery proof is deferred to Phase 6, when the Mini-hosted Hermes Adapter is integrated behind `AgentGateway`. That proof must execute on the Mac Mini runtime and must not call job/workspace tools or change job-search data. This is a PM-approved reclassification of the gate, not a Phase 0 failure.

All disposable diagnostic sessions and processes created while reviewing this boundary were cleaned up. The final read-only check found zero matching disposable sessions and zero matching disposable processes. No further MacBook-side turn attempts are permitted for this Phase 1 checkpoint.

## MCP Contract

Hermes 0.18.2 supports command-based local MCP servers. The live job-hunter profile already has an enabled stdio server (`knwldg_fleet`) alongside a remote HTTP server (`linear`). Therefore JobOS V1 should keep the architecture's preferred local stdio MCP Adapter. A network fallback is not currently required.

The JobOS MCP Adapter will run beside the Mini API and call the same application API over loopback. It must not read the job-hunter database or filesystem directly.

## Service Lifecycle

The Mini uses per-user `launchd` agents.

- Inspect: `launchctl print gui/$(id -u)/ai.hermes.gateway-job-hunter`
- Restart after an approved configuration change: `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway-job-hunter`
- Gateway logs:
  - `~/.hermes/profiles/job-hunter/logs/gateway.log`
  - `~/.hermes/profiles/job-hunter/logs/gateway.error.log`
- Unified backend logs:
  - `~/.hermes/logs/dashboard-fleet.out.log`
  - `~/.hermes/logs/dashboard-fleet.err.log`

No service was restarted during Phase 0.

## Credentials and Secrets

Secret values were not read or recorded. Existing credentials are referenced only by their storage mechanisms:

- Hermes profile environment: `~/.hermes/profiles/job-hunter/.env`
- Hermes provider authentication: profile `auth.json`
- MCP OAuth/service credentials: profile `mcp-tokens/`
- Hermes configuration: profile `config.yaml`
- New JobOS device authentication: `JOBOS_DEVICE_TOKEN`, supplied to the API and Electron main process at runtime and never exposed to the renderer

Keychain-backed desktop credential persistence remains the production implementation target. The Phase 1 proof uses an ephemeral process environment value only.

## Safe Phase 1 Development Topology

- Run the Electron desktop on the MacBook.
- Run a temporary JobOS API from an isolated `/tmp/jobos-v1-phase1-*` directory on the Mini.
- Use the Mini's installed `~/.hermes/bin/uv` and a dedicated `.venv`.
- Bind only to the Mini's private Tailscale address on a checked, unused development port.
- Store the JobOS development SQLite database inside that temporary directory.
- Do not import, migrate, or modify job-hunter data.
- Stop the temporary process after visible verification; do not install a launch agent or deploy it.

The MacBook-to-Mini Tailscale path is the Phase 1 acceptance host pair. Whether the packaged desktop must also run on the Mini remains a Phase 4 human-check decision and does not alter this connected-shell gate.

## Phase 0 Decision

The live topology does not materially conflict with the locked architecture. The exact Hermes Adapter is JSON-RPC/WebSocket over the loopback unified backend, and local stdio remains the correct MCP transport. Phase 1 may proceed behind the documented Interfaces without weakening browser independence, continuity, artifact fidelity, or human-agent parity.
