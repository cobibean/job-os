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
        +---- Career Profile + immutable Evidence vault (staging-only)
        +---- Artifact repository
        +---- Browser/agent capabilities
```

## JobOS Profiles

One JobOS installation may contain multiple named **JobOS Profiles**. Exactly one
is active for the installation at a time; switching profiles restarts the API and
desktop, then verifies the target's opaque profile ID before reopening. The
existing installation is adopted as the first profile by recording its validated
storage locations. Adoption moves, copies, renames, or rewrites no user data.

The installation owns binaries, device credentials, the profile registry, and
optional agent connection setup. Each JobOS Profile owns separate state and jobs
SQLite databases, artifacts, Career Profile Evidence, conversations and stored
agent session IDs, workspace/browser metadata, renderer and browser partitions,
and desktop DOCX client state. A managed profile always uses local SQLite jobs and
local artifacts. Display names never determine filesystem paths.

Authenticated desktop requests pin the expected profile with an opaque ID and
fail with `profile_context_changed` after another desktop switches the
installation. Profile-owned SQLite stores also bind once to that ID. MCP follows
the one currently active profile and cannot resume a conversation ID from another
profile because conversation records live in the isolated state database.

### Connected Agent persistence

The versioned installation registry owns durable Connected Agent identities,
non-secret connection metadata, lifecycle, model defaults, and each profile's
default-agent reference. Profile SQLite owns chat history and the immutable
agent/provider/model/effort binding captured for each conversation. Provider
session references remain opaque profile data; raw credentials never enter either
store.

Legacy installations upgrade through a persistent cross-store journal. Existing
Hermes chats remain readable offline and retain their IDs, transcript, timestamps,
and recovery/session references. A chat's model and reasoning effort are sealed
only from authoritative session evidence; unresolved history stays visibly locked
rather than inheriting or guessing a current default. The five-active-chat limit is
transactional per profile across all authorized devices and agents, and locked
active chats count until archived.

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

## Stable macOS identifiers

The desktop bundle, LaunchAgent, and Keychain service identifiers predate the
source-first release work. They remain stable so existing local installations
can update without losing macOS identity or stored credentials. These identifiers
are public compatibility strings, not operator configuration or secrets. Runtime
locations, hosts, devices, and optional integrations still come from validated
profile configuration.

## Current transition state

The public composition now uses a dedicated mutable SQLite canonical-jobs
repository, separate from workbench state. Job and artifact providers are selected
independently: a private install may keep SQLite jobs while explicitly selecting
the JobHunter artifact gateway. JobHunter loads dynamically only for a selected
private capability. `JobOsStateStore`
owns editable metadata, snapshots, registry metadata, revision, and replay state;
`SQLiteJobRepository` owns jobs; and `LocalArtifactRepository` owns bytes beneath
the configured application-data artifact root. Editable local publication writes
one validated DOCX/PDF pair, then registers both against the same editable
revision. The private `ArtifactGateway` remains the optional seam for JobHunter
publish, render, and refresh and reports an unconfigured capability when absent.
This source behavior is not proof that packaged onboarding or clean-clone
installed-app acceptance has passed.

The Career Profile cutover candidate uses typed records for **My Career**, **What
I'm Looking For**, and **My Evidence**. Accepted values, proposed or conflicting
imports, provenance, Claims, qualifiers, and forbidden-use boundaries share the
existing immutable global profile revision. Imported Source Evidence metadata
lives in SQLite while original bytes live in a JobOS-owned hash-verified vault;
API clients receive opaque Evidence IDs rather than local paths. A journaled,
idempotent migration command can build a fresh staging candidate from an explicit
bundle. Complete consumer projections remain dormant until a separately persisted
owner operation uses the exact cutover confirmation phrase. Candidate construction
does not migrate or replace any live profile authority.

Canonical-job mutations commit before the related workbench selection/audit
event. If the second local write fails, retrying converges through canonical-URL
deduplication and the existing state-store idempotency ledger; JobOS does not
claim a distributed transaction across the two SQLite databases.

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
reimplementing domain behavior. MCP file publication has one app-owned route:
`document_publication_prepare` creates a private inbox scoped to the current
conversation and job, and `document_publish` reads only from that inbox. It does
not trust process working directories, agent workspaces, Hermes profile caches,
or operator-defined document roots. The API validates the uploaded PDF/DOCX bytes
and stores its own immutable copy in JobOS's local artifact repository; publication
does not require a JobHunter checkout or artifact provider.

## Document provenance

The DOCX packages include modified Apache-2.0 GenOffice sources. Each package
preserves its own `LICENSE`, `NOTICE`, and `UPSTREAM.md`, including the pinned
source commit and JobOS modification boundary. See `THIRD_PARTY_NOTICES.md`.
