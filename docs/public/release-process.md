# Release Process

This document defines the source-first public release gate. It does not authorize
publication, history replacement, repository visibility changes, or binary
distribution.

## Current release posture

JobOS is preparing an Apache-2.0 source-first public alpha. There is no supported
public binary. The repository must remain private until the current tree, clean
clone, and rewritten history all pass their release gates.

## Source-alpha gates

A candidate must have:

1. root Apache-2.0 `LICENSE`, `NOTICE`, complete third-party notices, and accurate
   package metadata;
2. no required JobHunter, Hermes, Tailscale, private-network, private path, or
   operator identity in public startup;
3. built-in mutable SQLite jobs and local artifact ownership;
4. idempotent initialization with exactly one labeled synthetic demo job;
5. truthful API, MCP, desktop, and capability/error contracts;
6. no tracked private memory, documents, databases, logs, credentials,
   `.DS_Store`, or other prohibited publication classes;
7. green lint, typecheck, tests, build, generated-contract, license, privacy, and
   fixture-manifest checks;
8. a clean-home macOS golden path, plus supported Linux backend/source checks;
9. independently reviewed synthetic screenshots/media;
10. a verified isolated history rewrite and restorable backup.

## Candidate verification

Run the locked source gates:

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm check
pnpm contracts:check
```

For a macOS unpacked application candidate, verify the legal files in the real
packaged resources directory rather than relying only on build configuration:

```bash
uv run python scripts/verify_license_inventory.py \
  --packaged-resources "release/desktop/mac-arm64/JobOS.app/Contents/Resources"
```

Use the actual electron-builder output path when it differs by architecture.

Release-specific privacy, license, artifact, clean-clone, and history scanners
are added in later readiness phases. Every finding must be adjudicated without
copying private values into public reports.

## History replacement approval

History rewriting changes commit IDs and invalidates old clones and references.
The rewrite must first run in an isolated mirror with:

- a complete checksummed backup of all refs;
- exact path-removal/content rules derived from the accepted final tree;
- secret/privacy scans over every rewritten ref and object;
- a fresh clone that reproduces the accepted tree and passes all gates;
- a documented rollback procedure.

Immediately before any force-push, remote replacement, branch/tag deletion, or
visibility change, the owner must give fresh explicit approval in the active
conversation. Earlier planning approval is not sufficient.

## Public-surface verification

After an approved publication, clone from the public GitHub URL into a fresh
directory and repeat the complete quickstart, tests, scans, media review, license
detection, issue templates, security settings, and repository ruleset checks.

## Public binaries

Signed and notarized macOS binaries are a separate later track. Do not publish or
advertise a public download until signing, notarization, entitlements, exact-byte
installation, update/rollback behavior, checksums, and installed-app acceptance
have independently passed.
