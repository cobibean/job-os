# Connected Agents and ChatGPT/Codex Implementation Plan

- **Status:** Phases 0–7 delivered; Phase 8 acceptance complete in final delivery PR #129
- **Wayfinder:** [#100](https://github.com/cobibean/job-os/issues/100)
- **Planning ticket:** [#109](https://github.com/cobibean/job-os/issues/109)
- **Verified source baseline:** `5ef9730c12b41c0e03bac6533aaaf92192c9f893` on `origin/main`, verified 2026-08-25
- **Progress update (2026-08-25):** Phases 0–7 are merged and verified. Phase 6 shipped the Connected Agents roster/inspector, live provider model defaults, device-code and disconnect flows, immutable New Chat selection, readable locked history, and five-chat archive recovery in [PR #127](https://github.com/cobibean/job-os/pull/127). Phase 7 shipped scoped concurrency/failure/recovery behavior, honest remote-host and authentication states, bounded credential-free diagnostics, and terminal Codex rate-limit handling that does not blindly retry or switch providers in [PR #128](https://github.com/cobibean/job-os/pull/128); its closeout evidence is recorded on [#118](https://github.com/cobibean/job-os/issues/118#issuecomment-5407647236). Phase 8 candidate `5881d9d3adf07eadc9937ce80f6f43b55833dbb4` builds and verifies an arm64 package containing the exact pinned Codex runtime, license/notice receipts, valid signatures, a JobOS-owned Codex home, and launchd/MCP wiring that retrieves credentials from macOS Keychain without credential files or token-bearing app-server environment. Its approval-gated installed run preserved 148 existing jobs, restarted the exact release healthy, exercised visible per-request approvals, completed a durable synthetic-fixture editable-document snapshot without changing content, removed bounded diagnostic CDP afterward, and proved the Tailscale Serve health route. Cobi explicitly accepted the installed experience and approved private merge/redistribution of the candidate; production/public release remains separately gated.

**Goal:** Let a JobOS user connect and manage multiple agent identities, choose a profile default, and create a chat permanently owned by a selected Hermes or ChatGPT/Codex agent and supported model—without weakening JobOS context, tools, recovery, privacy, or existing Hermes behavior.

**Architecture:** JobOS owns installation-global Connected Agent identities, per-profile defaults, immutable chat bindings, trusted turn envelopes, normalized events, and local conversation history. Thin provider adapters connect the API-owned runtime router to Hermes or a host-local, version-pinned Codex App Server. Device-code authentication and credentials remain on the runtime host behind replaceable auth/vault interfaces.

**Tech stack:** FastAPI, Pydantic, SQLite, versioned installation JSON, generated OpenAPI/TypeScript contracts, Electron, React, TypeScript, MCP, Hermes Gateway, and Codex App Server over local stdio.

---

## 1. Product outcome

A user can:

1. Open **Settings → Connected Agents** and see every agent available to the JobOS installation.
2. Keep multiple Hermes agents and one durable ChatGPT/Codex identity in V1.
3. Connect Codex with ChatGPT device-code authentication from the host Mac or an authorized MacBook.
4. Rename an agent, choose its avatar, inspect health, select a supported default model and reasoning effort, test it, disconnect it, and explicitly reconnect it.
5. Choose a different default agent for each JobOS profile.
6. Start a chat with the profile defaults or override agent, model, and reasoning effort before creation.
7. See which agent and model permanently own every chat.
8. Use the complete authorized JobOS context and toolset from either Hermes or Codex.
9. Read historical chats when their agent is disconnected or unavailable, with honest recovery actions and no silent reassignment.
10. Run Hermes and Codex chats concurrently without session, transcript, context, tool, cancellation, credential, or failure leakage.

A chat is not a transferable conversation container. It is a durable JobOS record bound to one Connected Agent, provider model, and reasoning effort for its complete lifetime.

## 2. Required reading and authority order

Implementers must read these sources in order. When wording conflicts, the later approved contract in this list wins.

1. Repository rules: [`AGENTS.md`](../../AGENTS.md).
2. Public boundaries: [`docs/public/architecture.md`](../public/architecture.md).
3. Wayfinder map and product contract: [#100](https://github.com/cobibean/job-os/issues/100), [#101 final contract](https://github.com/cobibean/job-os/issues/101#issuecomment-5389034765).
4. Repository seam research: [#102](https://github.com/cobibean/job-os/issues/102) and [research artifact at `cdd7f064`](https://github.com/cobibean/job-os/blob/cdd7f0648c88cf490c1d7338faedfaec3e66c73e/docs/research/2026-08-23-connected-agent-seams.md).
5. Codex research: [#103](https://github.com/cobibean/job-os/issues/103) and [official-source artifact at `ae46f915`](https://github.com/cobibean/job-os/blob/ae46f91501ab476855a0e40555848b01ad967dc2/docs/research/2026-08-23-codex-oauth-runtime-models.md).
6. Domain, persistence, and migration: [#104 final contract](https://github.com/cobibean/job-os/issues/104#issuecomment-5389798205).
7. Routing, capability, concurrency, and event contract: [#105 final contract](https://github.com/cobibean/job-os/issues/105#issuecomment-5390229997).
8. Accepted UX direction and prototype evidence: [#106](https://github.com/cobibean/job-os/issues/106), branch `prototype/connected-agents-106`, commit `64c6b51b4db46f3657766a8abf0bda62f8e7137d`.
9. OAuth, remote setup, vault, disconnect, and recovery: [#107 final contract](https://github.com/cobibean/job-os/issues/107#issuecomment-5390426483).
10. Acceptance matrix, golden path, and phase gates: [#108 proof contract](https://github.com/cobibean/job-os/issues/108#issuecomment-5390691592).
11. This file. It converts all approved decisions into an executable implementation sequence.

No implementation issue may reopen these decisions without a documented plan amendment approved on #109 or a successor planning ticket.

## 3. Verified current state and exact seams

This section records the implementation baseline at `ee542e9d7c19d5c5a16bb15cb9e1072121415ff1`. Re-verify anchors before editing because line numbers may move; module ownership does not.

### 3.1 Current profile and persistence control plane

- `services/api/jobos_api/installation_profiles.py`
  - `InstallationProfileRecord` is the installation-global profile record.
  - `InstallationProfileRegistryData` is versioned and currently owns active profile and profile-list state.
  - Managed profile paths and the atomic registry implementation already provide revision, conflict, and idempotency patterns.
- `services/api/jobos_api/app.py`
  - `/v1/installation-profiles` routes begin near line 1050.
  - Profile activation status is already a host-owned API workflow.
- Existing installation data remains JSON plus profile-owned SQLite. Do not add a speculative global database.

### 3.2 Current chat/runtime path

- `services/api/jobos_api/conversations.py`
  - `CreateConversationRequest` currently carries only selected job context.
  - `ConversationResponse` does not yet expose an immutable agent/model binding.
  - `ConversationService` owns creation, dispatch, recovery, and snapshots.
- `services/api/jobos_api/conversation_store.py`
  - Profile SQLite owns conversations, turns, events, transcripts, recovery, and the opaque stored provider session identifier.
- `services/api/jobos_api/state_store.py`
  - The current schema is version 31 and `MAX_ACTIVE_CONVERSATIONS = 5`.
  - The current position/count constraint is scoped by `owner_device_id`; V1 must migrate that authority to the profile database as a whole so the host and authorized MacBook share one five-chat budget.
  - The existing `career_profile_connected_agents` table is Career Profile authorization state. It is not the installation-global Connected Agent domain and must not be renamed or reused for it.
- `services/api/jobos_api/agent_gateway.py`
  - `AgentContext`, `GatewayEvent`, `AgentGateway`, and `AgentGatewayFactory` are the narrow existing abstraction seam.
- `services/api/jobos_api/conversation_manager.py`
  - The manager currently receives one gateway factory and creates a gateway per conversation.
- `services/api/jobos_api/hermes_adapter.py`
  - `HermesGatewayFactory` and Hermes create/resume/submit/recover/event handling are the control implementation that must remain behaviorally stable.
- `services/api/jobos_api/app.py` and `main.py`
  - Composition currently selects a single Hermes gateway from environment configuration. Replace this with a provider-neutral router without making Codex a startup requirement.

### 3.3 Current capability and turn-correlation path

- `services/mcp/jobos_mcp/server.py` owns MCP instructions and `jobos://capability-map`.
- The verified baseline exposes 44 MCP tools. The existing catalog/resource drift test remains authoritative as that count evolves.
- `services/mcp/jobos_mcp/jobs.py` already carries conversation-scoped context and appends `conversation_id` to API calls. Extend this boundary to carry and validate `turn_id`; do not create provider-specific capability catalogs.
- OpenAPI is the authority. Generated TypeScript contracts live under `packages/contracts/src/generated/` and are checked by `pnpm contracts:check`.

### 3.4 Current desktop path

- `apps/desktop/src/main/agents/agent.ts` normalizes API snapshots/events for Electron.
- `apps/desktop/src/main/agents/agentIpc.ts` validates conversation/turn IDs and owns agent IPC handlers.
- `apps/desktop/src/shared/contracts.ts` defines desktop agent state, snapshots, summaries, and bridge APIs.
- `apps/desktop/src/renderer/agents/chat/useAgentSessions.ts` and `useAgentConversation.ts` own multiple chat sessions and per-chat behavior.
- `apps/desktop/src/renderer/agents/chat/AgentSessionTabs.tsx` and `AgentPanel.tsx` own visible chat selection and presentation.
- `apps/desktop/src/renderer/app/settings/SettingsPanel.tsx` is the current Settings composition seam. Add a focused Connected Agents surface rather than growing one monolithic component.
- `apps/desktop/src/renderer/app/WorkbenchApp.tsx` composes Settings and Agent Chat.
- Existing `ConnectedCareerProfileAgent` renderer/API types are Career Profile access-control records. They are not Connected Agent connections and must remain a separate domain.

### 3.5 Current packaging path

- `apps/desktop/package.json` owns Electron Builder configuration and `extraResources`.
- The pinned Codex runtime, configuration template, license/notice material, and source receipt belong in that explicit packaged-resource path.
- Codex is not currently a package dependency or bundled resource.
- `README.md` currently describes embedded chat as offline from external MCP. Update that statement when the optional Codex runtime really consumes the configured JobOS MCP; do not change it in an earlier phase.

## 4. Non-negotiable constraints

1. **One chat, one agent, one model.** `connected_agent_id`, provider, model ID, and reasoning effort never change after chat creation.
2. **No mid-chat switching or handoff.** Switching agents means creating another chat.
3. **JobOS owns local history.** ChatGPT website history is neither imported nor synchronized.
4. **Existing Hermes is the compatibility control.** Migration and router work must preserve it before Codex is enabled.
5. **Full capability parity.** A provider with unavailable JobOS MCP capabilities cannot accept turns as a basic chatbot.
6. **No silent fallback.** Never substitute an agent, model, reasoning effort, credential store, tool mode, retry, or recovery outcome.
7. **Five active chats per profile.** The limit spans all providers/models. Locked active chats count; explicit archive frees a slot.
8. **Installation-global agents, profile-owned defaults.** Connecting an agent never silently updates profiles.
9. **Exactly one durable Codex identity in V1.** A disconnected identity still occupies the slot. Multiple Hermes identities are allowed.
10. **Global permissions.** Do not introduce per-agent trust/permission policy in V1.
11. **Credentials never leave the runtime host.** Raw auth material never enters installation JSON, profile SQLite, renderer IPC, logs, analytics, support bundles, chat context, MCP payloads, fixtures, receipts, or packaged resources.
12. **No plaintext fallback.** Local Alpha forces dedicated `CODEX_HOME` plus `cli_auth_credentials_store = "keyring"`. Keychain failure is visible and fail-closed.
13. **Codex App Server is private host infrastructure.** Supervise it over stdio; never expose it directly over Tailscale, LAN, or public internet.
14. **Tailscale is transport only.** It does not appear in Connected Agent domain or auth business logic.
15. **Codex remains optional.** Missing, incompatible, crashed, or unauthenticated Codex cannot prevent JobOS or Hermes startup.
16. **Remote control requires JobOS device authorization.** Network reachability alone grants nothing.
17. **No live migration or deployment in implementation phases without its explicit gate.** Installed acceptance may use approved local test/real data; public hosting and production deployment require fresh approval.

## 5. Canonical glossary

| Term | Meaning |
|---|---|
| **Connected Agent** | Durable installation-global JobOS identity representing one configured agent provider connection. |
| **Provider** | Immutable runtime family: `hermes` or `codex` in V1. |
| **Connection** | Removable non-secret runtime configuration plus opaque vault reference and derived health for a Connected Agent. |
| **Agent default** | Installation-global default provider model and reasoning effort stored on a Connected Agent. |
| **Profile default** | Nullable reference from one JobOS profile to one Connected Agent. Used only to prefill future chats. |
| **Chat binding** | Immutable profile-owned tuple of Connected Agent ID, provider, model ID, reasoning effort, and binding state. |
| **Provider session** | Opaque Hermes session or Codex thread owned by exactly one JobOS chat. |
| **Trusted turn envelope** | JobOS-minted profile, conversation, turn, binding, authorized context, and global permission state sent through an adapter. |
| **Runtime Router** | API-owned service that resolves a sealed binding and delegates to the correct adapter. |
| **AuthFlowBroker** | Provider-neutral application interface for starting, reading, and cancelling safe auth transactions. |
| **CredentialVault** | Host-owned interface that verifies/removes provider credentials without exposing raw material to JobOS clients or persistence. |
| **Model catalog** | Live, connection-scoped supported models/reasoning options reported by the provider. It is not a universal static list. |
| **Locked chat** | Readable local history that cannot accept turns until its exact binding becomes usable and safe recovery succeeds. |

Use these terms in API, contracts, errors, tests, UI, and documentation. Do not call Connected Agents “bots,” “personas,” or “models.”

## 6. Domain and persistence contract

### 6.1 Installation registry schema

Increment the installation registry schema from version 1 to version 2. Preserve atomic replacement, revision checks, and idempotency behavior.

Add:

```text
ConnectedAgentProvider = "hermes" | "codex"
ConnectedAgentLifecycle = "connected" | "disconnected"
ReasoningEffort = provider-supported opaque value; V1 UI recognizes "medium"

ConnectedAgentRecord {
  id: opaque stable ID (`jagent_` + 32 lowercase hex chars)
  provider: immutable ConnectedAgentProvider
  display_name: editable non-empty user-facing name
  avatar_id: editable validated AgentAvatar asset ID
  default_model_id: nullable provider model ID
  default_reasoning_effort: nullable provider-supported effort
  lifecycle: ConnectedAgentLifecycle
  connection_config: nullable provider-specific non-secret object
  credential_reference: nullable opaque non-secret vault reference
  account_summary: nullable bounded safe display object
  account_fingerprint: nullable one-way provider/account continuity identifier
  created_at, updated_at, disconnected_at: UTC timestamps
}
```

Extend each installation profile record with:

```text
default_connected_agent_id: nullable ConnectedAgent ID
```

Rules:

- Provider is immutable.
- Name/avatar updates change presentation for old chats because chats resolve current identity presentation by ID.
- Provider/model/effort in old chat bindings never change.
- `account_summary` is bounded display data only. Never persist email/token claims unless returned as officially safe provider display metadata and explicitly covered by redaction tests.
- `account_fingerprint` must not be reversible auth material.
- Build `account_fingerprint` only from an officially returned stable opaque provider account ID using `sha256("codex-account-v1\\0" + opaque_id)`. Never fingerprint an email/name alone. If Codex exposes no suitable opaque ID, persist `null`; reconnect then requires explicit account-replacement confirmation and old provider sessions cannot be claimed recoverable.
- Connection config contains only data needed to locate a Hermes endpoint/profile or a dedicated Codex runtime namespace. Never store raw credentials.
- Cross-store references are validated at API/service boundaries and fail closed.

### 6.2 Cardinality and transactions

- Multiple durable Hermes records are allowed.
- At most one `provider == "codex"` record may exist, regardless of lifecycle.
- Creation uses the registry’s expected revision and idempotency key.
- Profile default changes are revision-checked, idempotent mutations.
- Disconnect preserves the record and all profile references.
- Deleting a profile never deletes an agent.
- V1 exposes no hard-delete Connected Agent operation. A later purge feature requires separate planning.
- Remove `owner_device_id` from active-position uniqueness and limit ownership during the v31→v32 profile migration. Keep it only for historical actor/authorization attribution where existing contracts require it. Count every active conversation row in the profile database, regardless of which authorized device created it.

### 6.3 Profile SQLite conversation binding

Increment the profile database schema from version 31 to version 32 in Phase 1 and add immutable fields to each conversation:

```text
connected_agent_id: non-null after migration
provider: "hermes" | "codex"
model_id: nullable only while legacy_awaiting_resolution
reasoning_effort: nullable only while legacy_awaiting_resolution
binding_state: "sealed" | "legacy_awaiting_resolution"
provider_session_id: nullable opaque provider value
connection_account_fingerprint: nullable continuity check captured at session creation
creation_state: "provisioning" | "ready" | "locked"
lock_reason: nullable normalized JobOS code
```

Use the existing stored-session column as a migration source. Rename it only if the repository’s SQLite migration conventions prove a table rebuild is safe; otherwise retain the physical column and expose it through the domain as `provider_session_id`.

Persist provider/model/effort redundantly with the agent ID so a chat cannot silently reinterpret a changed/missing global record. The Connected Agent record supplies current presentation and connection health; the chat binding supplies immutable runtime identity.

### 6.4 Binding and lock rules

- `sealed` requires provider, model, and effort.
- `legacy_awaiting_resolution` is valid only for migrated Hermes history whose exact model/effort is unknown.
- A disconnected/missing agent, unavailable sealed model, mismatched provider, account replacement mismatch, unresolved migration, or unsafe provider session locks the chat.
- Locking preserves transcript, events, timestamps, job/document context, and archive behavior.
- Locked chats count toward the five active-chat cap until archived.
- A locked chat cannot be rebound or duplicated as continuity. The user may create a new chat with another agent.

### 6.5 Cross-store workflow and compensation

Do not simulate a transaction across installation JSON and profile SQLite. Keep workflows single-owner wherever possible:

- Agent creation/edit/disconnect and profile-default changes mutate only the revisioned installation registry.
- Chat creation reads an exact registry revision, then writes the complete immutable binding and slot reservation in one profile-SQLite transaction; it does not mutate the registry.
- Disconnect does not fan out SQLite rewrites. Chats derive their lock from the preserved binding plus current global lifecycle, while explicit recovery outcomes remain profile-owned.
- Every mutation carries one idempotency key and its owner-store expected revision. Replaying an identical request returns the original result; a changed payload with the same key fails closed.
- Startup reconciliation validates all profile-default and chat references, reports stable lock/error states, and never creates/reassigns an agent heuristically.

Provider-session creation uses a deterministic compensation rule after the binding is committed as `provisioning`:

1. A definitive failure before the provider accepts/creates a session removes the provisional row and releases the slot in one SQLite transaction, then returns the typed creation failure.
2. A confirmed provider session stores its opaque reference and moves the chat to `ready`.
3. An accepted-or-ambiguous external delivery retains the bound row as `locked` with `RECOVERY_REQUIRED`; it keeps the slot and must be reconciled by the adapter before any retry. Never create a second session blindly.

The legacy v1→v2 registry plus v31→v32 SQLite migration is the only multi-store upgrade workflow. Give it a deterministic migration ID and registry journal with per-profile states (`pending`, `sqlite_complete`, `complete`). Each profile DB stores the same migration ID. Startup resumes incomplete profiles idempotently and finalizes the registry only after every in-scope profile reports the exact completed migration.

## 7. Provider-neutral application architecture

### 7.1 Runtime Router

Replace the single global gateway-factory assumption with an API-owned `AgentRuntimeRouter`. The renderer continues to call provider-neutral JobOS endpoints.

```python
class AgentRuntimeAdapter(Protocol):
    provider: ConnectedAgentProvider

    async def inspect_connection(agent, envelope) -> ConnectionHealth: ...
    async def list_models(agent, envelope) -> ModelCatalog: ...
    async def create_session(binding, envelope) -> ProviderSessionRef: ...
    async def resume_session(binding, session_ref, envelope) -> ProviderSession: ...
    async def submit_turn(session, trusted_turn) -> AsyncIterator[NormalizedAgentEvent]: ...
    async def interrupt(session, turn_id) -> None: ...
    async def recover(binding, session_ref, persisted_state) -> RecoveryDecision: ...
    async def close(session_ref) -> None: ...
```

`AgentRuntimeRouter`:

1. loads the profile-owned chat;
2. verifies the active profile/device authorization;
3. validates the immutable binding against the global Connected Agent record;
4. verifies lifecycle, account continuity, model support where relevant, MCP readiness, and provider health;
5. chooses the adapter by the sealed `provider` field—not renderer input;
6. creates/resumes exactly one provider session for the chat;
7. emits only normalized JobOS events;
8. persists terminal/recovery outcomes through existing conversation services.

Hermes must be moved behind this interface before Codex is connected. A deterministic fake adapter proves the interface and failure modes.

### 7.2 Trusted turn envelope

Extend `AgentContext` or replace it with a typed superset carrying:

```text
profile_id
conversation_id
turn_id
connected_agent_id
provider
model_id
reasoning_effort
authorized JobOS context references
global permission/trust snapshot
MCP endpoint + short-lived turn correlation, never provider credentials
```

JobOS mints every value. Providers and the renderer cannot override it.

MCP requests must carry `conversation_id` and `turn_id`. JobOS accepts activity/mutations only when both match the active trusted turn for the authenticated profile. Late, duplicate, replayed, cross-chat, and cross-profile calls fail closed or return the prior idempotent result.

### 7.3 Normalized event contract

Both adapters emit one ordered JobOS event union:

- `turn_started`
- `assistant_text_delta`
- `reasoning_activity`
- `tool_started`
- `tool_progress`
- `tool_review_required`
- `tool_completed`
- `turn_completed`
- `turn_cancelled`
- `turn_failed`
- `connection_changed`
- `recovery_required`

Every event includes `profile_id`, `conversation_id`, `turn_id`, monotonic provider-independent sequence, timestamp, and bounded normalized payload. Exactly one terminal outcome is persisted per turn. Raw Hermes/Codex events never cross API/desktop contract boundaries.

### 7.4 Concurrency and recovery

- Different chats can stream concurrently.
- One chat accepts one active turn. A second send returns a stable conflict; it is not queued.
- Cancellation targets `(conversation_id, turn_id)` and cannot terminate other sessions or shared provider infrastructure.
- Retry reuses the JobOS `turn_id` and idempotency correlation.
- Unknown provider delivery becomes `Recovery required`; never blindly submit a fresh provider turn.
- Restart rebuilds UI from JobOS persistence before reconnecting provider events.
- Every interrupted turn resolves to completed, cancelled, recoverable, failed, or recovery required. None remain permanently working.

## 8. Provider adapters

### 8.1 Hermes adapter

Refactor `HermesGatewayFactory` and current session/event logic into `HermesRuntimeAdapter` without changing public behavior.

- Existing endpoint/token/profile configuration migrates into one durable Hermes Connected Agent.
- Preserve current session create/resume, streaming, activity, MCP, cancellation, and recovery behavior.
- Use Hermes’s authoritative historical session metadata to reconcile `legacy_awaiting_resolution` chats when available.
- Hermes health or failure is isolated from Codex.

### 8.2 Codex runtime distribution

V1 distribution strategy is to bundle a tested pinned Codex runtime as an Electron `extraResource`, not depend on a user’s standalone installation.

- Protocol research baseline: OpenAI Codex source commit `479c8c8924eaafdeb56e86154cd19ff0805839e4`.
- Phase 0 records the exact binary/package version, platform, SHA-256, build/source commit, license, notices, and generated protocol schema used by the implementation.
- The binary and source receipt must be reproducibly inspected before merge.
- If redistribution/license verification fails, stop Phase 4 and amend this plan explicitly. Do not silently switch to PATH discovery or an arbitrary download.
- A missing/incompatible bundled runtime marks Codex unavailable while JobOS and Hermes start normally.

Supervise `codex app-server` over JSONL stdio:

1. launch under a canonical JobOS-owned `CODEX_HOME`;
2. force `cli_auth_credentials_store = "keyring"`;
3. send `initialize`, then `initialized`;
4. verify the generated protocol version/schema expected by JobOS;
5. keep process handles and raw protocol inside the adapter/runtime module;
6. use `thread/start`, `thread/resume`, `turn/start`, and `turn/interrupt` for chat lifecycle;
7. translate notifications to normalized JobOS events;
8. use `model/list` as the model authority;
9. use supported account RPCs for login/read/refresh/logout;
10. never expose App Server sockets or stdio to renderer/remote devices.

### 8.3 Codex model rules

- User-facing preferred label: **GPT 5.6 Sol**.
- Preferred provider model ID: `gpt-5.6-sol`.
- Preferred reasoning effort: `medium`.
- Apply those defaults only when the live selected connection reports them supported.
- Persist the exact provider model ID and effort returned/accepted by the provider.
- Never persist one universal Codex model list. A bounded short-lived health/catalog cache may exist only to reduce repeated reads and must be invalidated on runtime/auth changes.
- A disappeared model locks existing sealed chats and prevents new creation until the user explicitly selects a currently supported option.

## 9. Authentication, credentials, and remote setup

### 9.1 Interfaces

Application/domain code depends on:

```python
class AuthFlowBroker(Protocol):
    async def start_device_code(agent_id, mode, expected_account_fingerprint) -> SafeAuthTransaction: ...
    async def read(transaction_id) -> SafeAuthStatus: ...
    async def cancel(transaction_id) -> None: ...

class CredentialVault(Protocol):
    async def inspect(vault_ref) -> VaultStatus: ...
    async def verify_isolation(vault_ref) -> IsolationProof: ...
    async def remove(vault_ref) -> RemovalProof: ...
```

The local implementations wrap the dedicated Codex home and macOS Keychain behavior. Future hosted implementations may use a managed secret vault and a separately approved OpenAI auth contract without changing Connected Agent domain/UI contracts.

### 9.2 Device-code transaction

Canonical flow:

1. Authorized client calls the JobOS API for an agent’s auth transaction.
2. Host asks Codex App Server to start `chatgptDeviceCode` login.
3. API returns only transaction ID, verification URL, one-time code, expiry, and normalized status.
4. User completes authorization in any browser/device.
5. Host polls supported Codex account status and verifies the dedicated vault/isolation.
6. Registry stores only safe account summary/fingerprint, opaque vault reference, lifecycle, and timestamps.
7. The UI becomes `Connected` only after runtime, account, vault isolation, live model catalog, and MCP readiness checks pass.

Host-local browser callback may be offered only when device code is officially unavailable. It executes on the runtime host and never asks the user to copy `auth.json`, OAuth tokens, or Keychain contents.

Device-code proof uses a dedicated, explicitly approved ChatGPT test account/workspace with device-code login enabled. Phase 0 records this prerequisite without storing an account identifier or secret. Automated negative coverage must simulate device-code disabled/unavailable and prove that JobOS offers only the supported host-local callback fallback; a remote client must never receive a callback credential, copied file, or token. Any real authentication run requires fresh approval and secret-free evidence.

### 9.3 Disconnect, reconnect, and account replacement

Disconnect:

1. show affected profile defaults and active/locked chats;
2. call supported Codex logout;
3. verify removal of only the JobOS-specific vault entry;
4. clear removable connection metadata/credential reference;
5. preserve durable identity, account fingerprint for continuity comparison where safe, profile references, and local chats;
6. mark affected chats readable/locked;
7. if cleanup cannot be verified, show `Cleanup required`, not `Disconnected`.

Reconnect:

- starts device-code auth on the preserved Codex card/ID;
- same-account fingerprint permits provider-session recovery checks;
- a different account requires explicit **Replace account** confirmation;
- account replacement never claims old provider sessions are continuous;
- unrecoverable old chats remain locked as `Original account unavailable`;
- disconnect/retry/cleanup operations are idempotent.

Moving JobOS to another Mac requires a fresh device-code login. Supported JobOS backup can move local profile/chat data, but credentials and provider sessions are recovered separately.

### 9.4 Remote MacBook behavior

An authorized MacBook uses the normal authenticated JobOS API over the existing secure route. It can initiate/read/cancel safe auth transactions and manage agents. It never receives provider credentials or talks to Codex App Server.

When the host is unreachable:

- display `JobOS host unavailable`;
- do not queue connection/chat mutations;
- do not launch a local fallback Codex runtime;
- do not copy credentials;
- recover by re-reading host-owned state when connectivity returns.

## 10. Provider-neutral API contract

Add focused Connected Agent/auth service and route modules; keep `app.py` composition thin. Exact V1 endpoints:

| Method and path | Purpose |
|---|---|
| `GET /v1/connected-agents` | List installation-global identities, safe status, default model/effort, profile impact summary. |
| `POST /v1/connected-agents` | Create a Hermes identity or the single durable Codex identity with expected registry revision/idempotency. No credentials. |
| `GET /v1/connected-agents/{agent_id}` | Read one agent, safe connection health, affected profiles/chats. |
| `PATCH /v1/connected-agents/{agent_id}` | Rename, change avatar, or update validated non-secret connection config/defaults. Provider immutable. |
| `POST /v1/connected-agents/{agent_id}/test` | Run bounded provider/model/MCP readiness and return normalized health. |
| `GET /v1/connected-agents/{agent_id}/models` | Return live supported model/effort catalog and preferred-option availability. |
| `GET /v1/connected-agents/{agent_id}/disconnect-impact` | Read affected profile defaults and active/locked chat counts. |
| `POST /v1/connected-agents/{agent_id}/disconnect` | Idempotently log out/remove removable connection state after explicit confirmation token. |
| `POST /v1/connected-agents/{agent_id}/auth/device-code` | Start connect/reconnect/replace transaction; request declares mode and expected account fingerprint. |
| `GET /v1/connected-agent-auth/{transaction_id}` | Read safe normalized auth status. |
| `DELETE /v1/connected-agent-auth/{transaction_id}` | Cancel an outstanding transaction. |
| `PUT /v1/installation-profiles/{profile_id}/default-agent` | Set/clear profile default with profile + registry revision and idempotency. |

Extend `POST /v1/conversations`:

```json
{
  "selected_job_id": "optional existing value",
  "connected_agent_id": "optional explicit override",
  "model_id": "optional explicit override",
  "reasoning_effort": "optional explicit override",
  "idempotency_key": "required",
  "expected_profile_revision": 1,
  "expected_agent_registry_revision": 1
}
```

Resolution rules:

- Missing `connected_agent_id` resolves the current profile default.
- Missing model/effort resolves the selected agent defaults.
- Unconfigured, disconnected, unavailable, unsupported, stale, or incomplete defaults produce stable typed errors and no chat.
- Validate selected values against live connection health/catalog.
- Transactionally reserve the per-profile active-chat slot and persist immutable binding before provider session creation.
- If provider session creation fails, keep an honestly locked/recoverable bound chat or atomically roll back according to recorded delivery certainty; never expose a usable half-bound chat.
- Response includes binding, current agent presentation, normalized availability/lock state, and provider-neutral recovery actions.

Stable error/status codes include:

- `AGENT_NOT_CONFIGURED`
- `AGENT_DISCONNECTED`
- `AGENT_PROVIDER_UNAVAILABLE`
- `AGENT_TOOLS_UNAVAILABLE`
- `AGENT_CARDINALITY_CONFLICT`
- `MODEL_SELECTION_REQUIRED`
- `MODEL_UNAVAILABLE`
- `CHAT_LIMIT_REACHED`
- `CHAT_BINDING_CONFLICT`
- `LEGACY_BINDING_UNRESOLVED`
- `AUTH_LOGIN_PENDING`
- `AUTH_SIGN_IN_REQUIRED`
- `AUTH_VAULT_UNAVAILABLE`
- `AUTH_CLEANUP_REQUIRED`
- `AUTH_ACCOUNT_REPLACEMENT_REQUIRED`
- `HOST_UNAVAILABLE`
- `RATE_LIMITED`
- `RECOVERY_REQUIRED`

HTTP status and retry metadata must be consistent across API, generated clients, desktop, tests, and support diagnostics.

## 11. Legacy Hermes migration

Run a two-stage, offline-safe, idempotent migration.

### Stage A — structural migration, no provider required

Within one recoverable migration workflow:

1. Read/validate installation registry v1 and every existing profile database.
2. Derive one stable migrated Hermes agent ID from installation identity plus a fixed migration namespace; never randomize on retry.
3. Create the migrated Hermes record using existing non-secret Hermes configuration and current presentation/defaults.
4. Set existing profiles’ default agent only where legacy behavior establishes Hermes as their current default.
5. Add `connected_agent_id = migrated Hermes`, `provider = hermes`, and provider session reference to every historical chat.
6. Seal exact model/effort only when authoritative persisted history already contains them.
7. Otherwise set `binding_state = legacy_awaiting_resolution`, leave model/effort null, preserve readability, and lock new turns.
8. Preserve IDs, transcript, events, timestamps, selected job/document context, archive state, recovery data, and owner profile.
9. Commit/journal so interruption and restart resume without duplicate agent/chat binding.

Do not require Hermes or Codex availability for Stage A.

### Stage B — authoritative reconciliation

When the exact migrated Hermes connection is available:

1. Inspect the opaque historical session through Hermes-supported APIs.
2. Read exact model and effort from authoritative session metadata.
3. Compare provider/session ownership and reject mismatches.
4. Atomically update only unresolved fields and set `sealed`.
5. If unavailable or ambiguous, preserve readable locked history and a clear recovery state.

### Migration proof

Fixtures must cover:

- fresh installation;
- real pre-feature registry/profile schemas;
- known and unknown model history;
- no provider/network;
- interrupted registry write;
- interrupted SQLite migration;
- repeated startup;
- already migrated state;
- missing global agent reference;
- conflicting/manual corrupt data;
- rollback/restart behavior;
- five active legacy chats and locked-chat counting.

Assert exact before/after values and byte-level preservation where applicable. Never test only row counts.

## 12. Capability parity and useful work

Codex receives the same authorized JobOS capability contract as Hermes:

- live MCP tool catalog and schemas;
- canonical `jobos://capability-map`;
- JobOS instructions;
- selected profile/job/Career Profile/document/browser context;
- global permissions, trust mode, and review gates;
- conversation/turn correlation;
- normalized tool/activity persistence.

Use the existing JobOS MCP server as authority. Configure it in the dedicated Codex home/runtime. Do not duplicate tools as Codex-specific dynamic tools.

Before a turn, adapter readiness proves MCP initialization and required capability-map access. Failure produces `JobOS tools unavailable` and rejects the turn.

The parity suite enumerates every exposed capability category and runs shared positive/negative cases against Hermes and Codex. V1 acceptance requires Codex to create a real review-gated document from authorized JobOS context, receive user approval, and produce exactly one persisted artifact tied to the correct profile/conversation/turn/agent.

## 13. Desktop experience

Implement the accepted **Variant A** direction: a scannable Connected Agent roster with focused inspector, plus Variant B’s warmer onboarding and empty states.

### 13.1 Settings → Connected Agents

Create focused components/hooks beneath Settings rather than embedding provider logic in `SettingsPanel.tsx`:

- `ConnectedAgentsSettings`
- `ConnectedAgentRoster`
- `ConnectedAgentInspector`
- `ConnectCodexFlow`
- `ConnectedAgentModelPicker`
- `ConnectedAgentImpactDialog`
- `useConnectedAgents`
- `useConnectedAgentAuth`

The renderer consumes generated provider-neutral API types only.

Roster/inspector supports:

- add multiple Hermes agents;
- create/connect the single Codex identity;
- rename and change avatar;
- inspect safe connection/tool/model/host status;
- select supported default model/effort;
- test;
- set current profile default;
- inspect affected profiles/chats;
- disconnect, reconnect, cancel login, and explicitly replace account.

Clearly distinguish installation-global identity from the current profile’s default.

### 13.2 New Chat

- Prefill profile default agent and selected agent’s default model/effort.
- Place agent, model, and effort controls together before creation.
- Show only models/efforts reported supported by the selected live connection.
- If a default is unavailable, clear the invalid choice, explain it, and disable creation until explicit selection.
- At five active chats, block creation and provide an archive path. Never evict silently.
- After creation, controls become non-editable ownership display.

### 13.3 Chat identity and historical states

- Tabs/header display current Connected Agent name/avatar plus sealed model.
- Renaming/avatar changes update old chat presentation.
- Provider/model/effort remain unchanged.
- Disconnected, unresolved migrated, unavailable model, account-replacement, provider/tool failure, rate-limit, recovery, and host-offline states preserve readable history and show only valid actions.

Required distinguishable labels:

- `Not connected`
- `Login pending`
- `Connected`
- `Sign-in required`
- `Provider unavailable`
- `Model unavailable`
- `JobOS tools unavailable`
- `Rate limited`
- `Recovery required`
- `Host unavailable`
- `Cleanup required`

### 13.4 Accessibility and visual acceptance

- Complete flows work keyboard-only with logical focus and visible focus rings.
- Controls/status have accessible names and state is not color-only.
- Auth/connection transitions announce through an appropriate live region.
- Dialogs trap/restore focus correctly.
- Reduced motion, zoom, and text scaling remain usable.
- Verify at 1440×900 and 1280×800 plus the installed MacBook viewport.
- Capture normal, loading, empty, error, login, unavailable default, locked historical, five-chat, disconnect impact, recovery, and host-offline states.
- Human visual acceptance is mandatory before final packaging acceptance.

## 14. Diagnostics, privacy, and security

Diagnostics contain only:

- normalized provider/connection state;
- Connected Agent ID;
- profile/conversation/turn correlation IDs;
- runtime/package version and source receipt ID;
- bounded redacted error code/message;
- timestamps and safe retry timing;
- vault operation outcome, never vault content.

Add secret canaries to automated tests and scan:

- installation registry;
- every profile SQLite DB and journals;
- renderer/main IPC payload captures;
- API/MCP request/response captures;
- logs, crash output, analytics, diagnostics, and support bundles;
- fixtures and screenshots/OCR text;
- packaged resources, updater, receipts, and archives.

Do not log device codes beyond the user-facing auth response/session necessary for the active transaction. Treat one-time codes as sensitive ephemeral UI data and exclude them from support output.

Authorize every agent/auth operation with existing JobOS device identity rules. Add negatives proving a reachable but unauthorized client cannot list safe account details, initiate/cancel auth, change defaults, disconnect, test, or create chats.

Rate-limit state stays scoped to affected Codex chats, includes safe retry timing when available, and never changes provider/model.

## 15. Ordered implementation plan

There are **nine gated phases: Phase 0 plus Phases 1–8**. Phase 0 is a real implementation/proof issue, not administrative prework.

1. **Phase 0 — Baseline and acceptance harness:** [#111](https://github.com/cobibean/job-os/issues/111)
2. **Phase 1 — Domain, persistence, and migration:** [#112](https://github.com/cobibean/job-os/issues/112), blocked by Phase 0.
3. **Phase 2 — Provider-neutral runtime and events:** [#113](https://github.com/cobibean/job-os/issues/113), blocked by Phase 1.
4. **Phase 3 — Connected Agents API and orchestration:** [#114](https://github.com/cobibean/job-os/issues/114), blocked by Phases 1–2.
5. **Phase 4 — Codex runtime, auth, vault, and models:** [#115](https://github.com/cobibean/job-os/issues/115), blocked by Phases 2–3.
6. **Phase 5 — Codex adapter and JobOS capability parity:** [#116](https://github.com/cobibean/job-os/issues/116), blocked by Phases 2–4.
7. **Phase 6 — Connected Agents and New Chat UX:** [#117](https://github.com/cobibean/job-os/issues/117), blocked by Phases 3–5.
8. **Phase 7 — Concurrency, recovery, privacy, and remote hardening:** [#118](https://github.com/cobibean/job-os/issues/118), blocked by Phases 4–6.
9. **Phase 8 — Package, upgrade, installed proof, and human acceptance:** [#119](https://github.com/cobibean/job-os/issues/119), blocked by every prior phase and a fresh acceptance-run approval.

Each issue owns one reviewable gate. Do not hide migration, auth, parity, UX, resilience, or installed acceptance inside another phase’s closeout.

## 16. Acceptance-to-phase map

| Phase | Required acceptance IDs |
|---|---|
| 0 | `REG-01`; proof infrastructure for every later ID |
| 1 | `DOM-01`–`DOM-05`, `MIG-01`–`MIG-04`, `SEC-01` persistence assertions |
| 2 | `API-01`, `RTR-01`–`RTR-03`, `EVT-01`–`EVT-03`, `CON-02`, `ISO-02`, `REC-01`, `REC-02` |
| 3 | `API-01`–`API-04`, `DOM-03`–`DOM-05`, `MOD-02`, UX state contracts used by `UX-02`–`UX-08` |
| 4 | `CDX-01`, `AUTH-01`–`AUTH-05`, `MOD-01`, `MOD-02`, `HOST-01`, `SEC-01`, `SEC-02`, `PKG-01`, `PKG-04` |
| 5 | `CAP-01`–`CAP-05`, `RTR-01`–`RTR-03`, `EVT-01`–`EVT-03`, `SEC-01` |
| 6 | `UX-01`–`UX-08`, `A11Y-01`, initial `VIS-01` |
| 7 | `CON-01`, `CON-02`, `ISO-01`, `ISO-02`, `REC-01`–`REC-03`, `SEC-01`–`SEC-03`, `RATE-01`, `HOST-01` |
| 8 | `PKG-01`–`PKG-05`, `INST-01`, `INST-02`, `REG-01`, final `VIS-01`, every unresolved matrix ID |

The authoritative definitions and required proof for each ID are in [#108](https://github.com/cobibean/job-os/issues/108#issuecomment-5390691592). Issue bodies must repeat their owned definitions/expected evidence so implementation does not depend on hidden chat context.

### 16.1 Complete acceptance ID inventory

The final evidence index must contain every one of these 62 IDs exactly once as an owning proof result (an ID may be exercised by more than one phase, but has one final owner):

`DOM-01`, `DOM-02`, `DOM-03`, `DOM-04`, `DOM-05`; `MIG-01`, `MIG-02`, `MIG-03`, `MIG-04`; `API-01`, `API-02`, `API-03`, `API-04`; `RTR-01`, `RTR-02`, `RTR-03`; `EVT-01`, `EVT-02`, `EVT-03`; `CDX-01`; `AUTH-01`, `AUTH-02`, `AUTH-03`, `AUTH-04`, `AUTH-05`; `MOD-01`, `MOD-02`; `HOST-01`; `CAP-01`, `CAP-02`, `CAP-03`, `CAP-04`, `CAP-05`; `UX-01`, `UX-02`, `UX-03`, `UX-04`, `UX-05`, `UX-06`, `UX-07`, `UX-08`; `A11Y-01`; `VIS-01`; `CON-01`, `CON-02`; `ISO-01`, `ISO-02`; `REC-01`, `REC-02`, `REC-03`; `SEC-01`, `SEC-02`, `SEC-03`; `RATE-01`; `PKG-01`, `PKG-02`, `PKG-03`, `PKG-04`, `PKG-05`; `INST-01`, `INST-02`; `REG-01`.

## 17. Verification contract and commands

### 17.1 Every implementation issue

- Add focused failing tests before or with the smallest coherent behavior.
- Test authenticated public/service boundaries; do not prove behavior through direct DB shortcuts alone.
- Read back mutated external/persisted state.
- Use synthetic committed fixtures marked `(FAKE)`.
- Preserve unrelated changes and never use real credentials in fixtures.
- Regenerate OpenAPI/TypeScript contracts when schemas change.
- Run focused API/MCP/desktop tests for the owned surface.
- Run the repository gates before integration:

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm check
pnpm contracts:check
pnpm public:smoke-clean-clone
```

Use the repository’s package-specific scripts rather than inventing alternate build paths. Record exact command, exit code, test counts, skipped tests, and environment/runtime version.

### 17.2 Phase-specific proof assets

- Phase 0: legacy registry/SQLite fixtures, secret canaries, fake provider/vault, normalized event trace, fault injector, packaged-host runner, authorized/unauthorized remote-device fixture.
- Phase 1: migration fixtures and exact before/after snapshot tool.
- Phase 2: shared adapter conformance suite and deterministic event ordering/replay harness.
- Phase 3: cross-store transaction/fault tests and five-chat contention tests.
- Phase 4: pinned-runtime schema compatibility, Keychain integration, process/network inspection, standalone-Codex noninterference.
- Phase 5: MCP catalog/schema parity matrix and real document attribution/readback.
- Phase 6: component/integration/a11y tests and critical-state screenshots.
- Phase 7: concurrent provider, crash, expiry, model/tool outage, rate-limit, cancellation, ambiguous delivery, restart, host-loss, and unauthorized-device tests.
- Phase 8: clean/upgrade packages, signature/integrity/source receipt/privacy scans, installed host and MacBook evidence bundle.

### 17.3 Mandatory 15-step installed golden path

Acceptance requires one continuous evidence-backed run:

1. Begin with an existing installation containing a real profile, Hermes connection, historical Hermes chats, jobs, Career Profile, and artifacts.
2. Upgrade the packaged app; verify all data and Hermes behavior.
3. Open **Settings → Connected Agents** and verify the accepted roster/inspector.
4. From the authorized MacBook, initiate **Connect ChatGPT / Codex**.
5. Complete device-code authorization without copying tokens or touching host credential files.
6. Verify only safe host metadata is persisted and standalone Codex authentication remains independent.
7. Read the live model catalog; select `gpt-5.6-sol`/`medium` only if reported supported.
8. Set Codex as one profile’s default; prove another profile is unchanged.
9. Create a Codex chat; verify visible and persisted sealed agent/model/effort and pre-creation override capability.
10. Use real authorized JobOS context/tools to create and approve a review-gated document; read back exactly one artifact attributed to the correct turn.
11. Run a separate existing Hermes chat concurrently; prove independent streaming, tools, cancellation, transcript, and context.
12. Restart JobOS/API during or after activity; prove both chats restore or resolve explicitly with no duplicate output or stuck working state.
13. Verify historical Hermes chats remain readable/correctly bound and unknown model history was not guessed.
14. Disconnect Codex; verify its chats become readable/locked and Hermes remains usable. Reconnect the same account via device code and recover eligible chats.
15. From the MacBook, exercise host-offline and restored-host behavior without queued turns or credential movement.

Evidence includes sanitized normalized logs/correlation IDs, API/DB readback, screenshots, package/version/source receipt, privacy-scan output, and human confirmation for visual/remote portions. No secret may appear.

## 18. Risks and fixed mitigations

| Risk | Required mitigation |
|---|---|
| Registry/SQLite partial migration | Journal/idempotent two-stage migration, exact fixtures, interruption/restart proof, fail-closed cross-store checks. |
| Provider abstraction changes Hermes | Move Hermes first; conformance/control tests and installed Hermes smoke gate every foundational phase. |
| Codex protocol changes | Bundle/version-pin exact runtime, commit generated schema/receipt, compatibility smoke, explicit upgrade gate. |
| Device-code beta/unavailable | Device code remains primary; only supported host-local callback is fallback. Never credential copy. |
| Keychain unavailable | Fail setup closed with recovery guidance; never `auto`, plaintext, env, or DB fallback. |
| Different account on reconnect | Safe fingerprint comparison, explicit replacement confirmation, old sessions locked if continuity cannot be proven. |
| MCP parity drift | One authoritative catalog/capability map plus shared parity suite; reject turns when tools unavailable. |
| Duplicate/late tool effects | Trusted conversation+turn correlation, idempotency, one terminal outcome, replay negatives. |
| Cross-chat/provider leakage | One chat/session, per-chat turn lock/cancel, deterministic concurrent fault tests. |
| MacBook trust confusion | Existing JobOS device authorization on every operation; Tailscale reachability alone grants nothing. |
| Secret leakage in evidence/packages | Canary scans across all stores, IPC, logs, support bundles, fixtures, screenshots, package/updater/receipts. |
| Public hosted OAuth assumptions | Keep interfaces replaceable; public multi-tenant hosting remains a separately approved external-policy gate. |

## 19. Explicit non-goals

- Mid-chat agent, provider, model, or reasoning-effort switching.
- Agent-to-agent handoff or context transfer inside one chat.
- Import/synchronization of ChatGPT website conversation history.
- More than one durable Codex identity per installation in V1.
- Per-agent permissions/trust policies.
- Agent hard deletion or automatic history deletion on disconnect.
- Silent model/agent/tool/credential fallback.
- Direct renderer communication with Hermes, Codex, Keychain, or App Server.
- Direct Codex App Server exposure over Tailscale/LAN/public network.
- Copying or importing standalone `~/.codex/auth.json`.
- Generic arbitrary provider plugin marketplace.
- Managed agent subscription/provisioning.
- Multi-agent job-search orchestration.
- Public hosted/multi-tenant deployment or assuming ChatGPT subscription OAuth is approved for it.
- Production deployment, credential mutation, or live user-data migration as a consequence of approving this plan.

## 20. Approval gates

1. **Plan gate:** this file and ordered issues merge; no production feature code is included.
2. **Phase gates:** each issue closes only after its acceptance IDs and evidence pass. Later dependent phases do not merge around a failed gate.
3. **Codex distribution gate:** exact runtime redistribution/license/source receipt must pass before Phase 4 merges.
4. **Credential gate:** real device-code/Keychain testing uses a dedicated, explicitly approved ChatGPT test account/workspace with device-code login enabled and never records account identifiers or secrets. Negative proof covers the disabled/unavailable setting and host-local callback fallback without credential movement.
5. **Real-data upgrade gate:** Phase 8 must receive fresh approval immediately before exercising Cobi’s real installed JobOS data.
6. **Human acceptance gate:** visual quality, device-code UX, MacBook flow, and useful document workflow require explicit human confirmation.
7. **Deployment gate:** production deployment/public release is not authorized by plan or implementation completion; request fresh approval.
8. **Hosted gate:** confirm OpenAI’s supported third-party multi-tenant OAuth, tenant isolation, credential storage, and server runtime contract before any hosted launch.

## 21. Completion definition

Connected Agents V1 is implemented only when:

- existing Hermes installations migrate without loss or guessed model metadata;
- users can manage multiple Hermes identities and one durable Codex identity;
- each profile independently owns its default;
- every chat permanently seals one agent/model/effort and visibly communicates that ownership;
- Codex device-code auth and credentials stay on the runtime host in isolated Keychain storage;
- both providers use one trusted JobOS routing/event/capability contract;
- Codex completes a real review-gated JobOS document workflow;
- Hermes and Codex run concurrently without leakage;
- disconnect, restart, auth expiry, provider failure, model loss, host outage, and recovery remain explicit and isolated;
- clean and real-data upgrade packages pass host Mac and authorized MacBook acceptance;
- all acceptance IDs and the 15-step golden path have reviewable, secret-free evidence;
- human acceptance and any required fresh action approvals are recorded.

A working Codex text response is not completion. Passing unit tests without installed proof is not completion. A polished UI with weak migration, credentials, capability parity, or recovery is not completion.
