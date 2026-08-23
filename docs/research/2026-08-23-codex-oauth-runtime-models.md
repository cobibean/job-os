# Codex OAuth, runtime, models, and MCP research

**Date:** 2026-08-23

**Ticket:** [#103 — Research supported Codex OAuth, runtime, models, and MCP](https://github.com/cobibean/job-os/issues/103)

**Scope:** Current first-party OpenAI documentation and the open-source `openai/codex` repository at [`479c8c8924eaafdeb56e86154cd19ff0805839e4`](https://github.com/openai/codex/tree/479c8c8924eaafdeb56e86154cd19ff0805839e4). No credentials or tokens were read during this research.

## Executive decision

JobOS can satisfy the requested local ChatGPT/Codex integration without extracting OAuth tokens by running a version-pinned Codex runtime and speaking the documented `codex app-server` protocol over its default stdio transport. App Server is the only documented Codex surface that combines managed ChatGPT login, account status, local persisted threads, create/resume, streamed turn and tool events, interruption, model discovery, reasoning choices, approvals, MCP state, and ChatGPT rate-limit state in one bidirectional interface.[^app-server] Codex, not JobOS, should own the OAuth access and refresh tokens.

There are important boundaries:

1. OpenAI's dedicated App Server page tells product builders to use it for deep integrations, but the CLI reference still says the `codex app-server` command is primarily for development/debugging and may change without notice.[^app-server][^cli-reference] JobOS should therefore pin a tested Codex version, generate protocol schemas from that exact binary, put all calls behind an adapter, and treat upgrades as explicit compatibility work.
2. JSONL over stdio is supported; TCP WebSocket is explicitly experimental/unsupported and must not be the production dependency. Unix-socket control is documented, but stdio gives JobOS the narrowest local process boundary.[^app-source-protocol]
3. The stable protocol can expose configured MCP servers and their tool calls. Client-supplied `dynamicTools` are experimental and cannot carry a V1 capability-parity promise.[^app-source-experimental-tools]
4. ChatGPT entitlement, workspace policy, region, plan, and rollout determine runtime availability. A documented model slug is not proof that the signed-in account can use it. JobOS must use `model/list` as the connection-specific authority rather than hard-coding availability.[^app-source-models]

## Supported embedding and integration surfaces

| Surface | Official purpose | Fit for JobOS |
| --- | --- | --- |
| **Codex App Server** | Deep product integration with authentication, history, approvals, and streamed agent events over a JSON-RPC-like protocol.[^app-server] | **Recommended V1 adapter.** It provides every core lifecycle needed by the ticket. Pin the runtime and generated schema. |
| **Codex SDK — TypeScript** | Server-side application integration; Node.js 18+; starts and resumes coding-focused threads.[^sdk] | Viable wrapper, but it adds a Node SDK/runtime dependency and exposes less protocol detail than JobOS needs for a full native chat UI. |
| **Codex SDK — Python** | Python 3.10+ wrapper over local App Server; published builds include a pinned Codex CLI runtime.[^sdk] | Useful precedent for runtime pinning, not a natural dependency for the Electron/TypeScript JobOS process. |
| **Codex as an MCP server** | Lets another MCP client invoke `codex` (new thread) and `codex-reply` (continue by thread ID).[^codex-mcp-server] | Too narrow for the primary JobOS chat UI: it does not expose the complete account/model/event/approval surface. Useful only if Codex is a specialist behind another orchestrator. |
| **`codex exec --json`** | Non-interactive scripts and CI, with JSONL events.[^noninteractive] | Good for bounded jobs, not an interactive connected-agent session manager. |
| **Direct Responses API** | General OpenAI API integration using API keys and API billing. | Not a substitute for ChatGPT subscription OAuth. It would change billing, entitlements, thread storage, and Codex runtime semantics. |
| **Reading `auth.json`, copying bearer tokens into JobOS, or reproducing private CLI OAuth endpoints** | Not a public product-integration contract. The auth cache is a secret owned by Codex.[^auth] | **Forbidden design dependency.** It is unnecessary because App Server exposes managed login and account RPCs. |

## Recommended JobOS runtime contract

### Process and version boundary

- Install or bundle a tested Codex release and launch `codex app-server` as a supervised child process using default newline-delimited JSON over stdin/stdout.[^cli][^app-source-protocol]
- Pin the exact Codex version accepted by JobOS. At build/upgrade time, generate TypeScript or JSON Schema with `codex app-server generate-ts` or `generate-json-schema`; OpenAI states the output matches the binary version that generated it.[^app-source-protocol]
- Send exactly one `initialize` request and then `initialized` before all other calls. Record `clientInfo`; OpenAI asks enterprise-targeted integrations to register a known client name for Compliance Logs.[^app-source-init]
- Keep all protocol DTOs and process supervision behind an optional Codex adapter. Failure to install, authenticate, or start Codex must remain a capability error, not a JobOS startup failure.
- Use a JobOS-owned, per-connection Codex home or a deliberately selected shared Codex home. This choice controls auth, config, MCP config, and persisted Codex threads. It must be explicit; the CLI and IDE otherwise share the default cache.[^auth]

### Installation and platform prerequisites

- OpenAI documents a standalone macOS/Linux installer (`curl -fsSL https://chatgpt.com/codex/install.sh | sh`); npm and Homebrew installation paths are also exposed by the CLI docs.[^cli]
- App Server itself is a Codex executable mode. Node.js is required only if JobOS chooses the TypeScript SDK (Node.js 18+), and Python is required only for the Python SDK (Python 3.10+). Published Python SDK builds pin their Codex runtime.[^sdk]
- macOS sandboxing uses built-in Seatbelt and works without a separate sandbox package. Linux and WSL2 should install `bubblewrap`; native Windows uses the Windows sandbox and WSL2 uses the Linux implementation.[^sandbox]
- The runtime also needs network access to OpenAI/ChatGPT authentication and inference services. Corporate TLS interception may require `CODEX_CA_CERTIFICATE` (or `SSL_CERT_FILE`).[^auth]

## Authentication and credential lifecycle

### Managed browser login

Use the stable App Server account surface:

1. Read state with `account/read` and do not request or expose raw tokens.
2. Start browser login with `account/login/start { type: "chatgpt" }`.
3. Open the returned `authUrl`; App Server owns the localhost callback.
4. Correlate `account/login/completed` by `loginId`, then consume `account/updated` for the final `authMode` and plan metadata.
5. Allow cancellation with `account/login/cancel`.[^app-source-auth]

The public Codex authentication guide confirms that ChatGPT sign-in opens a browser and returns credentials to Codex. ChatGPT account controls, workspace permissions, retention, and residency apply to this sign-in mode.[^auth]

### Multi-device/headless initiation

For a browser on another device, call `account/login/start { type: "chatgptDeviceCode" }`, display only the returned `verificationUrl` and one-time `userCode`, and await the same completion/update notifications.[^app-source-auth] OpenAI labels device-code authentication **beta** and requires the user or workspace admin to enable it in ChatGPT security/workspace settings.[^auth]

This is the supported multi-device *initiation* flow. It does not imply that JobOS may copy a refresh token between devices. OpenAI documents copying `auth.json` only as a trusted headless fallback and explicitly says the file contains access tokens and must be treated like a password; that fallback is inappropriate for JobOS's normal product flow.[^auth]

### Refresh, logout, and revocation

- Codex-managed ChatGPT auth owns refresh tokens, persists them, and refreshes them automatically. `account/read { refreshToken: true }` can request an immediate refresh without returning the credential.[^app-source-auth]
- `account/logout` is the supported sign-out RPC; success returns `{}` and emits `account/updated` with no active auth mode.[^app-source-auth]
- The public auth and CLI references promise that logout clears/removes stored credentials.[^auth][^cli-reference]
- **Observed implementation, not a contract to reproduce:** at the pinned source revision, App Server routes logout through `logout_with_revoke`; managed ChatGPT auth attempts to revoke a refresh token (or access token fallback) at the OAuth revocation route and then clears all local stores. Revocation failure is logged while local clearing still proceeds.[^app-source-logout-handler][^app-source-revoke][^app-source-logout-manager] JobOS should call `account/logout` and report its result, not call the private revocation route itself or claim stronger server-side revocation semantics than the public RPC guarantees.

### Credential containment requirements

- Configure `cli_auth_credentials_store = "keyring"` where an OS keychain is available. `auto` uses a keyring if possible and otherwise falls back to file storage; `file` writes `$CODEX_HOME/auth.json`.[^auth]
- Treat all of `$CODEX_HOME/auth.json` as a password. Never ingest it into SQLite, logs, support bundles, analytics, IPC payloads, issue reports, or chat context. Never expose a "show token" UI.[^auth]
- Persist only non-secret JobOS connection metadata: adapter ID, Codex-home identity/path policy, observed auth mode, plan/workspace display metadata returned by stable RPCs, selected provider model ID, reasoning setting, health, and timestamps.
- Redact child-process stderr and login diagnostics before support export. Do not assume logs are token-free.
- Use `account/read`, `account/updated`, and explicit semantic errors as the source of auth state. Do not infer state from file existence.

## Thread lifecycle, streaming, and cancellation

The stable App Server lifecycle is sufficient for immutable JobOS chat ownership while retaining the provider thread ID as adapter state:

1. `thread/start` creates a new Codex thread and auto-subscribes the connection to its events. Persist the returned `thread.id` against the immutable JobOS chat owner.
2. `thread/resume { threadId }` reopens the stored thread so later `turn/start` calls append to it. It returns reconstructed turns by default; JobOS can also use stable `thread/read`/`thread/list` for recovery. Experimental paginated history APIs should not be a V1 dependency.
3. `turn/start` accepts input and optional model, reasoning effort, working directory, sandbox, and approval overrides. It immediately returns a turn object.
4. Consume `turn/started`, `item/started`, item deltas, `item/completed`, and finally `turn/completed`. `item/completed` is authoritative for each item; the final turn status is `completed`, `interrupted`, or `failed`.[^app-source-lifecycle][^app-source-events]
5. Cancel with `turn/interrupt { threadId, turnId }`. A successful request only acknowledges cancellation; completion is proven by the later `turn/completed` event with `status: "interrupted"`. Interruption does not terminate background terminals.[^app-source-interrupt]

A process crash or disconnect should be recovered by restarting the same pinned runtime/Codex home, reinitializing, reading the JobOS-owned mapping, and calling `thread/resume`. JobOS must not equate transport disconnect with turn failure until it reconciles thread/turn state.

## Models and reasoning

### Literal desired default and provider identifier

Keep the user-facing default label exactly as requested:

- **Display value:** `GPT 5.6 Sol`
- **Desired provider model ID:** `gpt-5.6-sol`
- **Desired reasoning effort:** `medium`

This is not an invented mapping. OpenAI's current Codex model guide states that the default Power setting is `gpt-5.6-sol` with medium reasoning, and the API model page identifies `gpt-5.6-sol` as GPT-5.6 Sol with `medium` as the default effort.[^codex-models][^sol-model] The official provider display spelling is `GPT-5.6 Sol`; JobOS may preserve its locked `GPT 5.6 Sol` presentation separately from the provider ID.

The family alias `gpt-5.6` currently routes to Sol, but JobOS should store the explicit `gpt-5.6-sol` slug when the product means Sol. An alias can change routing and is not equivalent to an exact selected ID.[^sol-model]

### Discovery and availability behavior

Call `model/list` after authentication and whenever account/provider/workspace state changes. Its stable result supplies picker-visible models, model IDs, display names, `hidden`, `isDefault`, `supportedReasoningEfforts`, `defaultReasoningEffort`, and input modalities. Preserve the server's reasoning-effort array order.[^app-source-models]

Selection algorithm:

1. Find exact ID `gpt-5.6-sol` in the authenticated connection's visible `model/list` result.
2. Verify that its `supportedReasoningEfforts` contains exact value `medium`.
3. If both are present, select that pair and display `GPT 5.6 Sol`.
4. If the model is absent, hidden, disabled by policy, or `medium` is unsupported, mark the desired default **unavailable**. Do not send a guessed slug, silently switch aliases, or enable experimental provider fallback.
5. Offer the server-advertised `isDefault` model and its `defaultReasoningEffort` as a clearly labeled fallback, or require the user to choose from the returned catalog. Persist both the desired preference and the effective selection so later availability can restore the preference.
6. If a previously selected model disappears, stop before the next new chat/turn or obtain an explicit fallback according to the product contract. A backend reroute during a turn is separately observable through `model/rerouted` and should be shown in diagnostics.[^app-source-events]

ChatGPT-plan documentation includes the GPT-5.6 family, but exact availability still depends on plan, workspace/admin controls, and rollout. API-key mode is different: model availability follows the models available to that API key.[^pricing]

## MCP and delivery of JobOS capabilities

There are two opposite MCP directions; they must not be conflated:

1. **Codex consumes MCP tools (recommended for JobOS capabilities).** Configure a JobOS MCP server on the Codex host. Codex supports local stdio and remote Streamable HTTP MCP servers, including bearer/OAuth authentication, tool allow/deny lists, timeouts, and required-server startup behavior.[^mcp] The App Server protocol exposes MCP startup status, inventory/auth state, OAuth initiation, direct resource reads/tool calls, approvals/elicitations, and streamed `mcpToolCall` items with arguments, result/error, and status.[^app-source-mcp]
2. **Codex is itself an MCP server.** `codex mcp-server` exposes only `codex` and `codex-reply` to an outer orchestrator.[^codex-mcp-server] This is not how a JobOS-owned chat should obtain the rest of JobOS's tools.

For full JobOS capability access in V1:

- expose a bounded, versioned JobOS MCP server with capability-based tool errors;
- configure it in the dedicated Codex home or controlled config layer;
- set it `required = true` only if the product contract says a chat must fail closed when JobOS tools cannot initialize;
- honor App Server's command/file/MCP approval requests in the JobOS UI and treat `item/completed` as authoritative;
- use stable configured MCP integration, not experimental `dynamicTools`;
- keep dangerous or external-write capabilities behind explicit approvals and existing JobOS authorization boundaries.

MCP OAuth credentials are a separate credential domain from ChatGPT login. Codex can store them in file/keyring, emits `reauthenticationRequired` when refresh cannot recover, and exposes an MCP-specific login completion event. JobOS must not mistake MCP reconnect for ChatGPT reconnect.[^mcp][^app-source-auth]

## Rate limits and error behavior

### ChatGPT-authenticated Codex

Use `account/rateLimits/read` for a snapshot and merge sparse `account/rateLimits/updated` notifications. The stable fields include current used percentage, quota-window duration, reset timestamp, backend-classified reached type, optional monthly/credit limits, spend-control state, and optional earned reset credits. Refetch after consuming a reset; do not infer totals from capped detail rows.[^app-source-auth]

Turn failures arrive as an `error` event and terminal `turn/completed { status: "failed" }`. Stable `codexErrorInfo` classifications include context-window exceeded, session-budget exceeded, usage-limit exceeded, HTTP/stream connection failures, bad request, unauthorized, sandbox error, internal error, and other. When known, upstream HTTP status is attached.[^app-source-errors]

Recommended handling:

- `UsageLimitExceeded`, session budget, spend control, unauthorized, policy, and bad-request errors: do not loop-retry; show a durable actionable state and relevant reset/reconnect/configuration action.
- transport disconnect or response-stream disconnect: reconcile thread state before bounded retry/resume.
- App Server ingress overload (`-32001`, `Server overloaded; retry later.`): bounded exponential backoff with jitter.[^app-source-protocol]
- model unavailable: refresh `model/list` and require the availability/fallback policy above.
- MCP startup failure with a required server: fail chat start/resume with the named capability error rather than continuing with partial capability.

### API-key mode distinction

OpenAI API rate limits are organization/project/model based. Temporary `429` responses may include `Retry-After`; custom clients should wait at least that long and otherwise use bounded exponential backoff with jitter. Quota, billing, and other user-action errors are not retryable merely because they are rate related.[^api-rate-limits] These raw HTTP rules are relevant to API-key/direct API integrations; JobOS should prefer the higher-level App Server error and account surfaces for ChatGPT-authenticated Codex and not promise that ChatGPT subscription limits equal API RPM/TPM limits.

## Public contract versus observed/private behavior

| Treat as supported contract | Treat as versioned/experimental/private observation |
| --- | --- |
| Managed `chatgpt` and `chatgptDeviceCode` login through App Server | Reading `auth.json`, extracting bearer/refresh tokens, reproducing OAuth client IDs/endpoints |
| `account/read`, login start/cancel, logout, auth notifications, rate-limit read/update | Exact token file schema and refresh timing |
| `thread/start`, `thread/resume`, `turn/start`, event stream, `turn/interrupt` | Experimental paginated thread history and dynamic tools |
| `model/list` and server-advertised reasoning efforts | Bundled static model catalogs as proof of account entitlement |
| Configured MCP servers, MCP status/events/tool items/approvals | Assuming every hosted ChatGPT plugin/app is available to every local plan/surface |
| Default stdio transport and per-version generated schemas | TCP WebSocket transport, explicitly experimental/unsupported |
| `account/logout` as the supported user action | Direct use of the observed `/oauth/revoke` implementation route |

## Implementation implications for the Wayfinder plan

1. Create a `CodexAppServerAdapter` around a supervised, version-pinned local Codex process and generated stable schemas.
2. Keep the adapter optional and capability-gated so JobOS remains local-first and starts without Codex.
3. Let Codex own OAuth and secrets; JobOS stores only non-secret connection and thread mappings.
4. Use managed browser/device-code RPCs, `account/read`, and `account/logout`; never parse the auth cache.
5. Discover model/effort choices per authenticated connection. Preserve `GPT 5.6 Sol` → `gpt-5.6-sol` + `medium` as a desired preference, not an unconditional availability assertion.
6. Deliver JobOS tools through a stable configured MCP server and implement every approval/elicitation callback required by the enabled tool set.
7. Persist immutable JobOS-chat-to-Codex-thread ownership and recover with `thread/resume` after process restart.
8. Build explicit state machines for login, turn streaming, interruption, model unavailability, MCP reauthentication, usage exhaustion, and transport recovery.
9. Add installed-app acceptance against the exact packaged Codex runtime version, including login on a test account, model discovery, tool approval, cancellation, resume after restart, limit/error presentation, logout, and verification that no secrets enter JobOS storage/logs/support artifacts.
10. Treat a Codex runtime upgrade as a compatibility release: regenerate schemas, rerun protocol/installed-app acceptance, and review newly stable/experimental fields.

## Sources

[^app-server]: OpenAI, [Codex App Server](https://developers.openai.com/codex/app-server) — documented product embedding purpose and protocol overview.
[^cli-reference]: OpenAI, [Codex developer commands / CLI reference](https://developers.openai.com/codex/cli/reference) — command maturity, App Server caveat, login/logout, MCP, resume, and execution commands.
[^cli]: OpenAI, [Codex CLI](https://developers.openai.com/codex/cli) — installation and current local CLI surface.
[^sdk]: OpenAI, [Codex SDK](https://developers.openai.com/codex/sdk) — TypeScript/Python integration scope, runtime requirements, and start/resume examples.
[^codex-mcp-server]: OpenAI, [Use Codex with the Agents SDK / Codex MCP server](https://developers.openai.com/codex/guides/agents-sdk) — `codex` and `codex-reply` MCP tools.
[^noninteractive]: OpenAI, [Non-interactive mode](https://developers.openai.com/codex/noninteractive) — `codex exec`, JSONL, and CI use.
[^auth]: OpenAI, [Authentication](https://developers.openai.com/codex/auth) — supported sign-in modes, device code, caching, automatic refresh, credential stores, logout, and secret-handling warning.
[^sandbox]: OpenAI, [Sandbox](https://developers.openai.com/codex/concepts/sandboxing) — macOS, Linux/WSL2, and Windows prerequisites.
[^codex-models]: OpenAI, [Codex models](https://developers.openai.com/codex/models) — Sol/Terra/Luna recommendations and default Power setting.
[^sol-model]: OpenAI, [GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol) — exact model ID, alias routing, reasoning efforts, and API feature support.
[^pricing]: OpenAI, [Codex pricing and feature availability](https://developers.openai.com/codex/pricing) — ChatGPT-plan family availability, variable usage, and API-key availability boundary.
[^mcp]: OpenAI, [Model Context Protocol](https://developers.openai.com/codex/mcp) — supported MCP transports, authentication, config, and client surfaces.
[^api-rate-limits]: OpenAI, [API rate limits](https://developers.openai.com/api/docs/guides/rate-limits) — API limit dimensions, `Retry-After`, and retry guidance.
[^app-source-protocol]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 20–59](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L20-L59) — transports, unsupported WebSocket, backpressure, and generated schemas.
[^app-source-lifecycle]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 66–87](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L66-L87) — thread/turn/item lifecycle and initialization.
[^app-source-init]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 85–139](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L85-L139) — client capabilities and enterprise client identification.
[^app-source-models]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 242–243](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L242-L243) — model discovery and ordered reasoning efforts.
[^app-source-interrupt]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 1184–1200](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L1184-L1200) — cancellation completion semantics and background-terminal boundary.
[^app-source-events]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 1561–1666](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L1561-L1666) — event stream, terminal statuses, items, MCP tool calls, reroutes, and deltas.
[^app-source-errors]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 1688–1710](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L1688-L1710) — stable turn error classifications.
[^app-source-experimental-tools]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 1825–1862](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L1825-L1862) — experimental dynamic-tool contract.
[^app-source-mcp]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 277–285](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L277-L285) and [lines 1610–1642](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L1610-L1642) — MCP OAuth/status/resource/tool RPCs and streamed items.
[^app-source-auth]: OpenAI Codex source at `479c8c8`, [`codex-rs/app-server/README.md` lines 2258–2449](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/README.md#L2258-L2449) — managed OAuth, refresh, browser/device flow, cancel, logout, MCP reauth, and ChatGPT rate limits.
[^app-source-logout-handler]: OpenAI Codex source at `479c8c8`, [`account_processor.rs` lines 897–910](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/app-server/src/request_processors/account_processor.rs#L897-L910) — App Server logout uses the revoking path.
[^app-source-revoke]: OpenAI Codex source at `479c8c8`, [`revoke.rs` lines 47–152](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/login/src/auth/revoke.rs#L47-L152) — observed managed-token revocation implementation.
[^app-source-logout-manager]: OpenAI Codex source at `479c8c8`, [`manager.rs` lines 2839–2856](https://github.com/openai/codex/blob/479c8c8924eaafdeb56e86154cd19ff0805839e4/codex-rs/login/src/auth/manager.rs#L2839-L2856) — observed best-effort revoke followed by local credential clearing.
