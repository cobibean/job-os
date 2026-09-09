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

## Acceptance

Verify the OAuth metadata and unauthenticated `401` first, then complete a real login and confirm MCP `tools/list`. Exercise job reads, a reversible Career Profile edit, and server-side generation/publication of a synthetic PDF/DOCX pair. Confirm registration/readback and hashes, rather than treating a successful HTTP connection as a successful file workflow. Keep fixtures out of the owner's real career facts.

A successful generic MCP/OAuth test is not a claim that the ChatGPT connection has been accepted; the final check is the owner's actual connection in ChatGPT.

## Sources

- [ChatGPT developer-mode MCP](https://developers.openai.com/api/docs/guides/developer-mode)
- [ChatGPT OAuth requirements](https://developers.openai.com/apps-sdk/build/auth)
- [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel)
