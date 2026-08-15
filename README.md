# JobOS

JobOS is a local-first desktop workbench for managing job-search workflows,
research, documents, and agent-assisted tasks in one place.

> [!IMPORTANT]
> **Open-source preparation is in progress.** This repository now has its public
> license and contribution surface, but the current application still contains
> operator-specific runtime assumptions. It is not yet the accepted clean-clone
> public alpha. Follow the [release process](docs/public/release-process.md) for
> the gates that must turn green before a public release.

## Release status

- **Maturity:** pre-release alpha; interfaces and storage may change.
- **Distribution:** source-first. There is no supported public JobOS binary yet.
- **Desktop:** macOS is the current desktop target. Linux runs backend and source
  quality checks in CI. Windows is not currently supported.
- **Privacy direction:** local-first. Public mode is being built to run without a
  private network, private data, or a second repository.

<!-- Public media placeholder: add only independently reviewed synthetic screenshots. -->

## What is available today

| Capability | Current source status | Public-alpha target |
|---|---|---|
| Desktop workbench | Runs from source on macOS | Clean first-run onboarding |
| Jobs and history | Built-in mutable SQLite repository; private installs may explicitly select JobHunter | Clean first-run initialization and synthetic demo data |
| Browser workspace | Electron-owned local capability | Local capability with truthful unavailable states |
| Documents | Local editor and retained-OOXML engine exist; some artifact flows remain privately coupled | Local create, edit, save, reopen, and export |
| Agent | Existing private deployments can connect an agent runtime | Clearly optional; offline/not-configured by default |
| MCP | Thin adapter over the JobOS API | Local API/MCP path with stable capability errors |

The table is intentionally conservative. A feature is not marked public-ready
until it passes the clean-home acceptance path.

## Prerequisites

Use the versions pinned by the repository:

- macOS for the desktop application
- Node.js `26.5.0` (`.node-version`)
- pnpm `10.33.1` (`packageManager` in `package.json`)
- Python `3.11` (the workspace requires `>=3.11,<3.12`)
- uv `0.11.18` (the CI-pinned version)
- Xcode Command Line Tools when building native macOS helpers

## Clean source-verification quickstart

This currently proves that a clean checkout installs and passes the complete
source gate. It does **not** yet prove public first-run product onboarding.

```bash
git clone https://github.com/cobibean/job-os.git
cd job-os
npm install --global pnpm@10.33.1
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm check
pnpm contracts:check
```

Expected result: lint, generated-contract checks, TypeScript checks, desktop and
Python tests, and the production source build complete successfully. The two
remaining Phase 0 public-boundary tests stay strict expected failures until
operator defaults and private tracked paths are removed.

### Developer commands

```bash
pnpm dev              # build helpers and launch the Electron developer app
pnpm check            # lint, typecheck, test, and build
pnpm contracts:check  # prove generated API contracts are current
```

`pnpm dev` currently exposes the existing source application. Canonical jobs use
the built-in local SQLite repository by default; agent features remain optional.
Do not treat a disconnected window as completed onboarding.

## Demo data

`jobos-init` initializes a fresh local profile with exactly one clearly labeled
synthetic demo job. Removing that job is persistent; JobOS will not silently
re-seed it. `jobos-init --reset-demo --confirm-reset-demo` is the separate,
explicitly confirmed reset path.

## Data and privacy

Run `uv run jobos-init` for the default platform data directory, or pass
`--data-dir` for an isolated source/test profile. The generated `config.json`
uses a loopback local service, separate state and jobs SQLite databases, local
artifacts, and an offline agent by default. Runtime files are not source
artifacts and must never be committed.

Before manipulating runtime data, stop JobOS and use diagnostics to open the
active data location. Back up the complete configured profile and verify every
database and artifact directory required by the enabled capabilities. Private
runtime modes may configure additional locations and remain responsible for
backing those up.
Credentials stored in macOS Keychain are not included in file backups and may
need to be configured again after a restore. Uninstalling the app/source does
not intentionally remove the separate runtime data. See
[data and privacy](docs/public/data-privacy.md) for the conservative move-aside
procedure and the future public contract.

## Architecture

JobOS is a pnpm and uv monorepo:

```text
apps/desktop/          Electron + React workbench
packages/contracts/    shared generated API contracts
packages/docx-engine/  retained-OOXML document engine
packages/docx-editor-core/ local document editor core
services/api/          FastAPI application core
services/mcp/          MCP adapter over the API
```

The public architecture keeps the application core local and puts integrations
behind explicit adapters. JobHunter remains a separate private adapter implementation;
public JobOS may define compatible interfaces but must not require or distribute
that private package. Read the [architecture overview](docs/public/architecture.md).

## Package visibility versus source licensing

Workspace packages remain marked `"private": true` to prevent accidental npm
publication. That field controls package-registry publishing only—it does not
make a Git repository private and does not replace the Apache-2.0 license.

## Documentation

- [Architecture](docs/public/architecture.md)
- [Data and privacy](docs/public/data-privacy.md)
- [Troubleshooting](docs/public/troubleshooting.md)
- [Release process](docs/public/release-process.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License and provenance

JobOS is licensed under the [Apache License 2.0](LICENSE). Attribution and
redistribution details are in [NOTICE](NOTICE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The two GenOffice-derived DOCX packages preserve their own `LICENSE`, `NOTICE`,
and `UPSTREAM.md` files, including the pinned upstream source and a summary of
JobOS modifications.
