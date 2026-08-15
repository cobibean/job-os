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
| Jobs and history | Still coupled to an optional private JobHunter adapter | Built-in mutable SQLite jobs |
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
Python tests, and the production source build complete successfully. The three
Phase 0 public-boundary tests remain strict expected failures until the private
adapter, operator defaults, and private tracked paths are removed.

### Developer commands

```bash
pnpm dev              # build helpers and launch the Electron developer app
pnpm check            # lint, typecheck, test, and build
pnpm contracts:check  # prove generated API contracts are current
```

`pnpm dev` currently exposes the existing source application. Job data and agent
features still require configuration that will become optional in later public
readiness phases; do not treat a disconnected window as completed onboarding.

## Demo data

There is no accepted public demo dataset yet. The public alpha will initialize a
fresh local profile with exactly one clearly labeled synthetic demo job. Removing
that job will be persistent; JobOS will not silently re-seed it. Until that path
lands, do not use private or historical operator data for screenshots, tests, or
documentation.

## Data and privacy

Source development currently defaults workbench state to `data/jobos.db` when
explicit configuration is absent. Installed macOS runtime files live under the
user's JobOS Application Support directory. These are runtime files—not source
artifacts—and must never be committed.

Before manipulating current runtime data, stop JobOS and copy `data/jobos.db`
for source development. For an installed private build, copy the entire JobOS
Application Support directory, then inspect `service/runtime.json` and also copy
every configured `state_db_path`, `job_hunter_db_path`, and `artifact_roots`
location; those paths may live elsewhere. Verify each backup is non-empty.
Credentials stored in macOS Keychain are not included in file backups and may
need to be configured again after a restore. There is no supported public reset
command yet, and uninstalling the app/source does not intentionally remove the
separate runtime data. See
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
