# Remote MCP connections

JobOS can expose its MCP tools to ChatGPT through an **optional authenticated Streamable HTTP server**. The local stdio integration remains unchanged. This does not require a new ChatGPT UI or a second JobOS workspace.

## Connection shape

ChatGPT → HTTPS forwarding → OAuth-protected JobOS MCP → local JobOS API.

The API and remote MCP run independently of the desktop window. Browser and native document-editing tools still need the desktop surfaces that implement those operations. Keep all registered tools exposed; runtime availability is reported by each tool.

## Configuration

Run `jobos-mcp-http --config /absolute/path/to/private-remote-config.json` (or `python -m jobos_mcp.remote_http`). Store the config outside the repository, readable only by its owner (`0600`). Its fields are:

- `public_url`: stable HTTPS origin, without `/mcp` or a trailing path.
- `port`: local loopback HTTP listener; default `8770`.
- `api_base_url`: local JobOS API; default `http://127.0.0.1:8766`.
- `api_token_file`: private file containing the distinct external MCP API credential. Configure the API's `JOBOS_EXTERNAL_MCP_TOKEN_FILE` with that same path. Never reuse the internal MCP credential here.
- `owner_secret`: a generated random connection password of at least 32 characters; used only to connect the owner's ChatGPT account.
- `artifact_root`: existing JobOS artifact directory on the MCP host.
- `agent_id` / `agent_token`: the existing Career Profile connected-agent identity and credential to use for these tools. These are server-side values, never sent to ChatGPT.

Forward the HTTPS origin to this listener, including `/mcp`, `/authorize`, `/token`, `/register`, `/revoke`, `/connect`, and `/.well-known/*`. Tailscale Funnel or another stable HTTPS forwarder can do this; an ordinary private Tailscale Serve URL is not directly reachable by ChatGPT's cloud servers. The MCP listener itself binds only to loopback. Do not expose the raw JobOS API as the ChatGPT endpoint.

Use the host's service manager to keep both processes running. OAuth state is persisted in `oauth.sqlite3` beside the config so restarting the MCP process does not require reconnecting. Keep this private directory out of source control and backups shared with others.

## ChatGPT form

- Name: **JobOS**
- Description: **Use my JobOS career profile, jobs, documents, and workspace.**
- MCP server URL: **`https://YOUR-HOST/mcp`**
- Authentication: **OAuth**
- OAuth Client ID / Client Secret: **leave blank** (dynamic registration).

When ChatGPT opens **Connect ChatGPT to JobOS**, enter the owner's connection password. This is account connection authentication, not an extra per-tool approval workflow. The server uses the MCP SDK's authorization-code/PKCE flow, access tokens, refresh rotation, and revocation.

## External context and files

External calls do not require an active internal JobOS agent turn. Context-dependent operations reuse a durable external conversation; internal agents retain their existing turn checks. This is the same API and document pipeline, not a parallel job database.

Remote document helpers accept text and generate a matched PDF/DOCX on the JobOS host, then publish through the existing document pipeline. File-transfer helpers cover bounded source/binary content; the caller does not need access to a Mac-local path. Generated documents use a simple layout rather than a new template/design system. Existing document-edit tools retain their own fidelity behavior.

## Updating tools on an existing connection

A server implementation fix that keeps the same tool name, description, schemas, and annotations is not a catalog update: deploy the fix and exercise the existing tool. Adding/removing tools or changing their descriptors or server instructions changes the catalog. An unchanged tool count alone does not establish an unchanged catalog.

For a **developer-mode connection**, deploy the server update, open the existing connection in ChatGPT settings, choose **Refresh**, and verify the changed tools in that connection. For **published apps**, metadata is a reviewed snapshot: scan the server, submit the updated version, and publish the approved version. A server deploy or developer Refresh does not itself replace the published snapshot. See [OpenAI's connection/update guide](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt).

JobOS provides bounded diagnostics for this distinction:

- `/healthz` includes `catalog_revision` (a SHA-256 fingerprint) and `tool_count`. The fingerprint covers every served tool descriptor field and the server instructions, with stable object-key/tool ordering. It does not fingerprint implementation code, credentials, or job data.
- MCP `initialize` returns `serverInfo.version` as `catalog-<catalog_revision>` for the startup catalog. Register tools before startup; live registry mutation is not a supported update workflow. This version is diagnostic metadata, **not a cache-invalidation command**.
- The HTTP entrypoint emits one `jobos_mcp.discovery` INFO event when a successful `tools/list` handler result reaches a completed ASGI response send. Fields are limited to method, HTTP status, the actual response catalog revision/count, and a generated `discovery_id`. The same ID is returned in `X-JobOS-Discovery-ID`. General access logs remain disabled; embedders must enable this logger explicitly.
- These events do not record request IDs, headers, tokens, URLs/querystrings, client/user identity, tool descriptors, arguments, or results. They do not buffer general tool results. Auth rejection, malformed requests, and failed/incomplete sends do not produce a completed-discovery event.

To accept a catalog update:

1. Record the endpoint's health revision/count. With existing OAuth credentials, run `initialize` → `notifications/initialized` → `tools/list`; compare initialization metadata and the full catalog (including any pagination). JobOS currently returns its complete catalog without a cursor.
2. Refresh the **existing** developer connection, or complete the published-version review workflow. Correlate the operation's time with discovery events and, where available, the returned discovery ID. A manual probe also creates an event; logs do not identify a caller as ChatGPT.
3. Verify that the existing ChatGPT connection exposes the added/changed tool and can invoke the intended safe workflow. Endpoint health and generic MCP tests are necessary but not sufficient acceptance.

If the endpoint serves the expected revision but the connection remains stale, preserve the nonsecret revision/count and event timing, and report the client-side visibility failure separately. No event means no completed discovery was observed by this logger, not proof that no request was attempted. An event proves only what the server sent to its ASGI transport, **not that ChatGPT received, accepted, or used it**. Recreating a connection can be a recovery step, but is not proof that in-place updates work. The stale-client cause remains unresolved; these diagnostics do not guarantee refresh or prevent recurrence. OAuth reconnect is an authentication operation, not a guaranteed catalog refresh.

The transport remains stateless Streamable HTTP; JobOS does not advertise tool-list-change notifications or introduce a cache/transport workaround.

## Acceptance

Verify the OAuth metadata and unauthenticated `401` first, then complete a real login and confirm MCP `tools/list`. Exercise job reads, a reversible Career Profile edit, and server-side generation/publication of a synthetic PDF/DOCX pair. Confirm registration/readback and hashes, rather than treating a successful HTTP connection as a successful file workflow. Keep fixtures out of the owner's real career facts.

A successful generic MCP/OAuth test is not a claim that the ChatGPT connection has been accepted; the final check is the owner's actual connection in ChatGPT.

## Sources

- [ChatGPT developer-mode MCP](https://developers.openai.com/api/docs/guides/developer-mode)
- [ChatGPT OAuth requirements](https://developers.openai.com/apps-sdk/build/auth)
- [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel)
