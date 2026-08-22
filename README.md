# JobOS

**The job hunt, in one window.** JobOS is an open-source, local-first desktop workspace that keeps job discovery, a persistent browser, document editing, and an AI agent working side-by-side — so you stop losing context across tabs, spreadsheets, and chat windows.

<p>
  <a href="https://github.com/cobibean/job-os/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/cobibean/job-os?style=flat"></a>
  <a href="LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache%202.0-blue"></a>
  <img alt="Platform: macOS" src="https://img.shields.io/badge/platform-macOS-black">
  <img alt="Status: alpha" src="https://img.shields.io/badge/status-alpha-orange">
</p>

![JobOS Review workbench with the fictional Northstar Kites Demo job selected](docs/media/screenshots/jobos-hero-1440x1024.png)

<details>
<summary>▶ Watch the 10-second walkthrough</summary>

![Silent JobOS walkthrough moving from Review to Browse, back to Review, and into the saved fake cover-letter editor](docs/media/jobos-demo.gif)

More views: [Browse list & job detail](docs/media/screenshots/jobos-browse-detail-1440x1024.png) · [Document editor](docs/media/screenshots/jobos-ooxml-editor-saved-1440x1024.png) · [Capture provenance](docs/media/README.md)

</details>

---

## Why JobOS

A months-long job search gets scattered: job boards in one tab, a tracking spreadsheet in another, your resume in a third, an AI chat in a fourth. Every switch loses context.

AI agents are great at researching companies and tailoring applications — but a chat window is a bad place to *run* a job search. You can't see what the agent sees, inspect what it changed, or come back to the same working context tomorrow.

**JobOS makes the workbench the interface.** Jobs, live web pages, documents, conversation, and agent activity all stay visible and connected in one place. You guide the work, review the real result, and stay in control the whole time.

## What a session looks like

1. **Browse** — sign in to job sites in the built-in browser; your tabs and sessions persist between visits.
2. **Save** — spot a promising listing and save it to your workspace without leaving the page.
3. **Ask** — have a connected agent inspect the role and tailor a resume or cover letter.
4. **Review** — watch the agent work, read the real document next to the listing, tweak it in the built-in DOCX editor.
5. **Export** — send the approved DOCX/PDF to your computer.
6. **Apply** — return to the same browser session, upload your file, and submit — manually, on your terms.
7. **Return** — reopen JobOS later with your jobs, tabs, documents, conversation, and layout exactly where you left them.

> The agent is optional. Browsing, job tracking, and document editing all work on their own when no agent is configured.

## What makes it different

- **One continuous workflow** — search, save, tailor, edit, and apply without hopping between disconnected tools.
- **Human and agent share one state** — you both work on the same jobs, browser, documents, and history. No parallel copies to reconcile.
- **You stay in control** — agent activity is visible, sensitive actions can require approval, and JobOS *never* auto-submits an application.
- **Local-first and private** — the default setup uses local storage and loopback services. No hosted backend, no account, no private network required.
- **Built to persist** — jobs, browser tabs, document state, conversation, and layout all survive a restart.

## Quick start

> **Heads up:** JobOS is pre-release **alpha** and runs **from source on macOS**. There's no downloadable app yet. Interfaces and storage may still change.

**Prerequisites** (use the pinned versions):

- macOS
- Node.js `26.5.0` · pnpm `10.33.1` · Python `3.11` (`>=3.11,<3.12`) · uv `0.11.18`
- Xcode Command Line Tools (for native macOS helpers)

**Install and launch:**

```bash
git clone https://github.com/cobibean/job-os.git
cd job-os
npm install --global pnpm@10.33.1
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm dev
```

**On first launch:**

1. Click **Set up JobOS**. It creates a private local profile, a loopback API, SQLite databases, a local artifact folder, and two independent local credentials.
2. Click **Restart JobOS** when setup finishes. (If the dev command exits during restart, just run `pnpm dev` again.)
3. Keep JobOS open while you use it — the desktop owns the local API, and live browser tools need that connection.

**You'll land in the workbench** with one clearly fictional **Northstar Kites (Fictional Demo)** job and a `(FAKE)` starter resume. The embedded chat will say its agent isn't configured — that's the honest default. You can connect one (see below).

---

## For developers

<details>
<summary><b>Connect an external agent through MCP</b></summary>

JobOS exposes a local stdio MCP server so any MCP-capable agent can operate your JobOS jobs, workspace, documents, activity, and validated browser tools. This does **not** power the embedded JobOS chat — that stays offline in the public source config.

Finish first-run setup above and keep JobOS open. Then add a stdio MCP server to your agent. Clients differ, but most accept this `command`/`args` shape:

```json
{
  "mcpServers": {
    "jobos": {
      "command": "/bin/zsh",
      "args": [
        "-c",
        "set -euo pipefail; JOBOS_REPO='/absolute/path/to/job-os'; JOBOS_CONFIG=\"$HOME/Library/Application Support/JobOS/config.json\"; JOBOS_DEVICE_ID=$(/usr/bin/plutil -extract deviceId raw \"$JOBOS_CONFIG\"); export JOBOS_CONFIG_PATH=\"$JOBOS_CONFIG\" JOBOS_DEVICE_ID; export JOBOS_DEVICE_TOKEN=\"$(\"$JOBOS_REPO/apps/desktop/build/jobos-keychain\" get com.cobibean.jobos.device-token \"$JOBOS_DEVICE_ID\")\"; export JOBOS_MCP_TOKEN=\"$(\"$JOBOS_REPO/apps/desktop/build/jobos-keychain\" get com.cobibean.jobos.mcp-token \"$JOBOS_DEVICE_ID\")\"; exec \"$JOBOS_REPO/.venv/bin/jobos-mcp\""
      ]
    }
  }
}
```

Replace `/absolute/path/to/job-os` with your clone's absolute path. This launcher reads the generated device ID from `~/Library/Application Support/JobOS/config.json`, pulls both local credentials from macOS Keychain at process start, and hands them to the MCP server through the environment. It never writes credential values into the repo or your MCP config.

The JobOS server contract itself:

| Setting | Value |
|---|---|
| Transport | stdio |
| Executable (after workspace sync) | `/absolute/path/to/job-os/.venv/bin/jobos-mcp` |
| API | `http://127.0.0.1:8766` |
| Required env | `JOBOS_DEVICE_TOKEN` and `JOBOS_MCP_TOKEN` |
| Credential provider | macOS Keychain, keyed by the generated `deviceId` |

> ⚠️ **Never** print, paste, commit, screenshot, or log either credential. If your `config.json` doesn't report `"provider": "keychain"`, this launcher doesn't match your profile — don't substitute raw credential values.

**Verify the connection** with this read-only prompt:

> Use the JobOS `job_list` tool. Don't modify anything. Report the company, title, and whether the returned job is synthetic.

A fresh profile returns **Northstar Kites (Fictional Demo)**, **Imaginary Kite Systems Tuner — Demo Role**, and `synthetic_demo: true`. If the tool is missing, confirm JobOS is open, setup finished, the repo path is absolute, and the MCP process starts from this clone. See [troubleshooting](docs/public/troubleshooting.md).

</details>

<details>
<summary><b>Contributor checks</b></summary>

Run these before contributing or when validating an exact source revision:

```bash
pnpm check            # lint, typecheck, test, and build
pnpm contracts:check  # prove generated API contracts are current
```

Everything — public-tree and fixture checks, lint, generated contracts, TypeScript, desktop and Python tests, and the production source build — should pass. (This verifies the *source*; it does not prove a signed or notarized binary.)

</details>

<details>
<summary><b>Demo data, storage & privacy</b></summary>

**Demo data.** First-run calls `jobos-init`, which seeds exactly one clearly labeled synthetic demo job and one `(FAKE)`, do-not-apply starter resume. Delete the demo and it's gone for good — JobOS won't silently re-seed it. `jobos-init --reset-demo --confirm-reset-demo` is the separate, explicitly confirmed reset path.

**Storage.** Run `uv run jobos-init` for the default platform data directory, or pass `--data-dir` for an isolated source/test profile. The generated `config.json` uses a loopback local service, separate state and jobs SQLite databases, local artifacts, and an offline agent by default. Runtime files are not source artifacts and must never be committed.

**Backups.** Before touching runtime data, stop JobOS and use diagnostics to open the active data location. Back up the full configured profile — every database and artifact directory your enabled capabilities use. Credentials in macOS Keychain aren't part of file backups and may need reconfiguring after a restore. Uninstalling the app/source does not remove your runtime data. See [data and privacy](docs/public/data-privacy.md).

</details>

<details>
<summary><b>Architecture</b></summary>

JobOS is a pnpm + uv monorepo:

```text
apps/desktop/               Electron + React workbench
packages/contracts/         shared generated API contracts
packages/docx-engine/       retained-OOXML document engine
packages/docx-editor-core/  local document editor core
services/api/               FastAPI application core
services/mcp/               MCP adapter over the API
```

The application core stays local; integrations sit behind explicit adapters. JobHunter is a separate private adapter — public JobOS may define compatible interfaces but must not require or distribute that private package. See the [architecture overview](docs/public/architecture.md).

**Package visibility vs. licensing.** Workspace packages are marked `"private": true` to prevent accidental npm publication. That controls package-registry publishing only — it doesn't make the Git repo private and doesn't replace the Apache-2.0 license.

</details>

## Project status

Pre-release **alpha**, honest about where it is:

| Area | Where it stands |
|---|---|
| **Maturity** | Alpha — interfaces and storage may change |
| **Distribution** | Source-first; no supported public binary yet |
| **Desktop** | macOS is the target. Linux runs backend + source checks in CI. Windows unsupported. |
| **Privacy** | Local-first defaults — no private network, private data, or second repo needed |

<details>
<summary>Detailed capability breakdown</summary>

| Capability | Current source status | Public-alpha target |
|---|---|---|
| Desktop workbench | Runs from source on macOS with accepted clean first-run init | Installed-app distribution is a separate gate |
| Jobs & history | Built-in mutable SQLite repo with labeled synthetic demo data; private installs may select JobHunter | Public distribution review |
| Browser workspace | Electron-owned local capability | Local capability with truthful unavailable states |
| Documents | SQLite/local mode: create, save, snapshots, DOCX import, paired DOCX/PDF publish, restart, download; JobHunter render/refresh optional | Installed-app acceptance with representative DOCX files |
| Embedded chat agent | Offline/not-configured by default; private deployments can connect a runtime | Remains optional |
| External agent (MCP) | Authenticated stdio adapter over the local JobOS API (setup above) | Stable local capability errors |

This table is intentionally conservative: clean-home source acceptance does not imply packaging, signing, notarization, publication, or deployment passed.

</details>

## Documentation

- [Architecture](docs/public/architecture.md) · [Product contract](docs/public/product-contract.md) · [Capability parity](docs/public/capability-parity.md)
- [Data and privacy](docs/public/data-privacy.md) · [Troubleshooting](docs/public/troubleshooting.md) · [Release process](docs/public/release-process.md)
- [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

JobOS is licensed under the [Apache License 2.0](LICENSE). Attribution and redistribution details are in [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The two GenOffice-derived DOCX packages keep their own `LICENSE`, `NOTICE`, and `UPSTREAM.md` files, including the pinned upstream source and a summary of JobOS modifications.

---

<sub>The preview above uses only fictional starter data and a checksum-pinned `(FAKE)` DOCX fixture. Optional agent and artifact-refresh integrations stay unavailable unless you explicitly configure them.</sub>
