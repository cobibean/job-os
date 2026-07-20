# JobOS V1 Phase 1 Connected Shell Memory - 2026-07-19

## Session summary

- Closed the Phase 0 runtime gate through read-only inspection of the live Mac Mini, job-hunter, Hermes, SQLite job store, resume artifact, launchd services, and unified desktop gateway.
- Recorded the verified topology in `docs/architecture/v1-runtime-contract.md`.
- Implemented the Phase 1 pnpm/uv workspace, secure Electron shell, FastAPI service, SQLite migration foundation, generated OpenAPI client, MCP package scaffold, locked workbench frame, tests, and CI workflow.
- Proved the packaged Electron app against a temporary authenticated API on the Mini. The native status bar reported `Mac Mini connected · API 0.1.0`.
- Removed the temporary Mini process and files after verification. Nothing was deployed permanently.

## What we learned

- The authoritative SSH route is `jacobilangemm@100.123.109.19`; the target is an Apple Silicon Mac Mini reachable over Tailscale.
- The existing job-hunter runtime uses its own Python 3.11 environment and SQLite store. Read-only construction and job listing succeeded without changing its dirty user worktree.
- Hermes 0.18.2 exposes the required session lifecycle over the loopback unified desktop WebSocket backend. A read-only `session.list` request succeeded against the live gateway.
- Sandboxed Electron preload scripts must be emitted as CommonJS. A TypeScript `.cts` preload producing `preload.cjs` works; an ESM preload did not expose the bridge.
- Packaged Vite renderers need `base: './'`. Root-relative assets produced a blank `file://` window even though browser development verification passed.
- A clean workspace must build the generated contracts package before Electron typechecking; an already-present `dist` directory can hide this ordering problem.

## Decisions made

- Use Node.js 26.5.0 and TypeScript 7.0.2 for the application and contracts.
- Keep `@hey-api/openapi-ts` in an isolated build-tool package using TypeScript 5.9.3 because the current generator crashes when executed by TypeScript 7. Application and contract compilation remain on TypeScript 7.
- Use an authenticated FastAPI endpoint bound to the private Tailscale address for the connected-shell proof.
- Keep renderer privileges narrow: no Node integration, raw IPC, filesystem access, permissions, new windows, or untrusted navigation.
- Treat Linear issue `CLO-47` as awaiting PM validation. The implementor does not close or change its status.

## Files created or changed

- Workspace: `.node-version`, `package.json`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`, `pyproject.toml`, `uv.lock`, `.gitignore`.
- Desktop: `apps/desktop/` Electron main/preload/renderer implementation, tests, styles, and build configuration.
- API and MCP: `services/api/`, `services/mcp/`.
- Contracts and tooling: `packages/contracts/`, `tools/openapi-generator/`, `scripts/export_openapi.py`, `scripts/verify-packaged-renderer.mjs`.
- CI: `.github/workflows/ci.yml`.
- Runtime evidence: `docs/architecture/v1-runtime-contract.md`.

## Source-of-truth docs

Read before implementation in this order:

1. `docs/planning/brainstorming/v2-brainstorm-doc.md`
2. `docs/planning/specs/v1-workbench-contract.md`
3. `docs/architecture/v1-technical-architecture.md`
4. `docs/planning/implementation/v1-implementation-plan.md`
5. `docs/design/references/jobos-v1-locked-direction.png`

The implementation issue is Linear `CLO-47 — JobOS V1 Phase 1 — Application Skeleton and Contracts`.

## Commands and verification

- `pnpm check` passed on Node 26.5.0: lint, TypeScript checks, renderer tests, Python tests, generated contracts, Electron build, and packaged-renderer asset verification.
- A clean-room copy with frozen pnpm and uv installs also passed the full `pnpm check` command.
- Renderer/Electron tests: 3 passed. FastAPI tests: 3 passed.
- Mini API proof: unauthenticated device-session request returned `401`; authenticated request returned `200`.
- Native proof screenshot: `/Users/cobibean/.codex/visualizations/2026/07/20/019f7cf0-360e-7ff2-bac1-962ce4446986/jobos-connected-shell.png`.
- Browser verification confirmed the workbench DOM, the locked three-panel composition, and a real Research preset interaction before native Electron capture.

## Gotchas and constraints

- Do not treat browser-only rendering as packaged-app proof; it missed both the Vite asset-path and preload-module failures.
- The Phase 1 credential was ephemeral and was not written to project memory. Production credential persistence remains a later Keychain-backed implementation target.
- The Mini API proof was temporary, not a deployment or launchd installation.
- No files were staged, committed, pushed, or deployed during this implementation.
- Preserve the existing `docs/planning/.DS_Store` modification and the intentional absence of `docs/planning/brainstorming/v1-brainstorm-doc.md`.

## Open decisions

- PM validation still needs to decide whether the Connected Shell gate is accepted and whether `CLO-47` should close.
- Permanent Mini service installation, production credential storage, signing, and packaging remain outside Phase 1.

## Recommended next work

- Have the PM agent validate the screenshot, clean-room evidence, security boundary, and issue acceptance criteria.
- After PM acceptance, Phase 2 real jobs and Phase 3 persistent layout can proceed from the connected shell without changing the Phase 1 architecture boundaries.
