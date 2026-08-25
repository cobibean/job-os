# Connected Agents Phase 8 acceptance

Phase 8 packages the final Connected Agents candidate and records installed-product proof without turning synthetic fixtures into claims about Cobi's real installation.

## Candidate scope

- The arm64 desktop package bundles the exact pinned Codex `0.144.4` app-server, source receipt, LICENSE, and NOTICE.
- `package:mac` builds workspace contracts in a clean checkout before building the desktop package and then runs `verify-phase8-package.mjs` against the extracted `.app` and ZIP.
- The launchd runtime config can bind the installed package's app-server and Keychain helper to one JobOS-owned Codex home.
- The Codex-launched JobOS MCP process reads its device and MCP credentials from macOS Keychain at process launch. No credentials file, token argument, or token-bearing Codex app-server environment is used.
- `evidence-index.json` contains all 62 acceptance IDs exactly once and binds final human/install approval to this exact accepted candidate.

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

## Exact candidate and installed proof

The approval-gated installed run used exact source commit `5881d9d3adf07eadc9937ce80f6f43b55833dbb4` and package SHA-256 `7cf990529e0816832ceb8a87b6f0ec0961fbf14af1633009a9f8ff2ee7ee508a`.

Mechanically verified results:

- the installed app and stable runtime release came from that exact candidate, with strict app signature and packaged payload checks passing;
- rollback was created before replacement, the launchd API restarted healthy, and the diagnostic CDP endpoint was loopback-only and removed after the bounded run;
- all 148 pre-existing jobs remained available after installation;
- the dedicated JobOS Codex home remained authenticated through Keychain-backed credentials and returned the live runtime model catalog;
- visible, exact-turn approval was required separately for each supported MCP operation; approved requests were consumed and stale or contradictory decisions were not reused;
- a Codex chat read an existing synthetic-fixture editable cover letter and created a fresh manual snapshot without changing document content or job state; database readback confirmed the completed turn, unchanged document revision, new snapshot, matching revision, and persisted snapshot payloads;
- the Tailscale Serve health endpoint returned HTTP 200 and `ready`, proving the served host route;
- after reviewing the completed installed/package evidence and the remaining boundary, Cobi explicitly accepted the installed experience and approved private merge/redistribution of this exact candidate on 2026-08-25.

The attempted real resume render correctly remains **failed**, not acceptance evidence. The renderer produced a PDF but the existing Job Hunter PDF verifier rejected extractor-dependent text ordering. Phase 8 therefore uses the independently useful and durable editable-document snapshot workflow as its successful installed MCP mutation rather than weakening PDF verification or expanding this PR into Job Hunter renderer work.

## Human and release boundary

The human acceptance record covers the installed visual/accessibility and remote experience for this private Phase 8 delivery. It does not convert automated diagnostics into a claim that every assistive-technology combination was mechanically exercised. Fresh private redistribution approval was recorded separately from the historical Phase 0 receipt. Production/public release remains a separate approval gate and was not authorized.
