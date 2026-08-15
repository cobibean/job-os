# Architecture

JobOS is a local-first desktop application, not a hosted multi-tenant service.
The public architecture keeps the product core usable on one machine and treats
external systems as replaceable capabilities.

## System map

```text
Electron + React desktop
        |
        | generated TypeScript contracts
        v
FastAPI application core <---- MCP adapter
        |
        +---- Job repository
        +---- Workbench state
        +---- Artifact repository
        +---- Browser/agent capabilities
```

## Repository boundaries

- `apps/desktop/` owns the Electron process, React renderer, native desktop
  capabilities, and user-facing state projections.
- `services/api/` owns application behavior and stable HTTP contracts.
- `services/mcp/` is a thin MCP translation layer over the API; it should not
  become a second application core.
- `packages/contracts/` contains generated/shared client contracts.
- `packages/docx-engine/` and `packages/docx-editor-core/` contain the local
  retained-OOXML document stack.

## Public and private composition

The accepted public alpha will use:

- loopback transport;
- a built-in SQLite repository for canonical jobs and history;
- a separate SQLite store for workbench state;
- local artifact ownership;
- an offline/not-configured agent state;
- one clearly labeled synthetic demo job.

JobHunter is a private adapter implementation. It is not part of the JobOS
public source contract and must not be imported or required by public startup.
The public core defines focused interfaces and stable errors; private deployments
may supply compatible adapters without changing the default composition.

## Current transition state

The present source still has direct JobHunter coupling and operator-specific
runtime defaults. Permanent public-boundary tests intentionally remain red until
those dependencies are removed. This document describes the accepted target and
must not be read as proof that clean-clone acceptance has already passed.

## Design principles

1. Keep application contracts typed and transport-independent.
2. Keep canonical jobs separate from UI/workbench state.
3. Prefer one supported local storage engine over speculative database layers.
4. Make optional capability states explicit and truthful.
5. Keep secrets out of renderer state, logs, process arguments, and diagnostics.
6. Preserve local file containment, symlink checks, checksums, and recoverable
   writes.
7. Verify the real desktop path—not only mocks or direct API calls.

## Contract flow

The FastAPI OpenAPI schema is the contract authority for generated TypeScript
clients. Changes must regenerate committed outputs and pass:

```bash
pnpm contracts:check
```

The MCP adapter maps those application operations and errors rather than
reimplementing domain behavior.

## Document provenance

The DOCX packages include modified Apache-2.0 GenOffice sources. Each package
preserves its own `LICENSE`, `NOTICE`, and `UPSTREAM.md`, including the pinned
source commit and JobOS modification boundary. See `THIRD_PARTY_NOTICES.md`.
