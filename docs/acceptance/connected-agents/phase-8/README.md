# Connected Agents Phase 8 acceptance

Phase 8 packages the final Connected Agents candidate and records installed-product proof without turning synthetic fixtures into claims about Cobi's real installation.

## Candidate scope

- The arm64 desktop package bundles the exact pinned Codex `0.144.4` app-server, source receipt, LICENSE, and NOTICE.
- `package:mac` builds workspace contracts in a clean checkout before building the desktop package and then runs `verify-phase8-package.mjs` against the extracted `.app` and ZIP.
- The launchd runtime config can bind the installed package's app-server and Keychain helper to one JobOS-owned Codex home.
- The Codex-launched JobOS MCP process reads its device and MCP credentials from macOS Keychain at process launch. No credentials file, token argument, or token-bearing Codex app-server environment is used.
- `evidence-index.json` contains all 62 acceptance IDs exactly once and keeps incomplete human/installed proof visibly unresolved.

## Automated proof

Run from an arm64 macOS checkout:

```bash
pnpm --filter @jobos/desktop package:mac
uv run python scripts/verify_license_inventory.py \
  --packaged-resources release/desktop/mac-arm64/JobOS.app/Contents/Resources
uv run pytest -q \
  services/api/tests/test_macos_runtime.py \
  tests/public-release/test_mcp_runtime_launcher.py \
  tests/connected_agents/test_evidence_index.py
```

The package verifier checks:

- arm64 identity and strict code-signature validity for JobOS and Codex;
- exact pinned Codex app-server SHA-256 and semantically identical receipt;
- packaged Codex and JobOS legal resources;
- extracted generated application-payload privacy checks and exact hashes for large pinned binaries;
- a truthful status split: package/integrity checks may pass while installed and real-data runs remain approval-gated.

## Fresh approval boundary

The following proof is intentionally **not** claimed by automated or synthetic results:

1. replacing or upgrading Cobi's real installed JobOS app;
2. reading or migrating Cobi's installed profiles, chats, jobs, Career Profile, or artifacts;
3. restarting the live Mac host API;
4. performing live ChatGPT device-code or Keychain authentication;
5. operating the authorized MacBook/Tailscale path;
6. creating or approving a real useful document;
7. final human visual, keyboard, screen-reader, reduced-motion, zoom, remote, and recovery acceptance.

Those actions require fresh approval immediately before the run. Production/public release remains a separate approval gate.
