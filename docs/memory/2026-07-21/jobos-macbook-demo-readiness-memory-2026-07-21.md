# JobOS MacBook Demo Readiness Memory - 2026-07-21

## Status

- **Demo-ready for Cobi's solo use; not shipment-ready.**
- The Mac Mini now owns the authoritative JobOS API through a per-user `launchd` service.
- A packaged Apple-silicon MacBook client connects to the Mini through a tailnet-only HTTPS route.
- A personalized, tailnet-only download bundle installs the unsigned app into `~/Applications`, writes remote runtime configuration, stores the MacBook credential in Keychain, and opens JobOS.

## Product boundary accepted for this demo

- One operator, two trusted Macs, private Tailscale network.
- No public exposure or Tailscale Funnel.
- The API remains bound only to `127.0.0.1:8766` on the Mini.
- Apple signing/notarization, universal binaries, automatic updates, and polished credential bootstrap remain out of scope.

## Implementation and runtime decisions

- Added native macOS Keychain support, runtime configuration, local lifecycle handling, remote-client mode, and the Tailscale deployment adapter from the Phase 9 worktree.
- Allowed the Mini runtime configuration to use the real loopback Hermes WebSocket endpoint (`ws://.../api/ws`) instead of only HTTP URLs.
- The Mini login Keychain was locked during activation. For this demo only, the launchd service reads device and Hermes credentials from one private mode-0600 file under JobOS Application Support. The path is non-secret; credential values are never tracked or written here.
- The MacBook still stores its own per-device credential in macOS Keychain through the packaged native helper.
- The personalized download ZIP contains a revocable MacBook bootstrap credential because there is no signed installer or one-time enrollment flow yet. It is served only over a private tailnet HTTPS route. Cobi should trash the downloaded ZIP/folder after installation.

## Live proof

- Full repository gate passed after the Phase 9 changes: lint, type checks, 133 desktop tests, 311 API tests with one expected skip, production build, and packaged-renderer verification.
- Final focused runtime/security suite passed: 32 tests with one expected skip; Ruff passed.
- Unsigned arm64 package rebuilt successfully with the native Keychain helper.
- `launchd` reports `com.cobibean.jobos.api` running.
- `lsof` shows only `127.0.0.1:8766` for the JobOS API.
- Tailscale Serve exposes the API through a tailnet-only HTTPS route and forwards to loopback.
- Both Mini and MacBook device credentials returned HTTP 200 for health, authenticated device session, and jobs requests. Health reported `ready`; Hermes agent connectivity reported `online`.
- The packaged app was launched in `remote-client` mode through the private HTTPS route. Visual inspection showed the real three-pane UI, the authoritative job list, selected-job context, and the green **Mac Mini connected** state.
- The physical MacBook appeared online in Tailscale status but did not answer a direct tailnet ping during the final run. Installation and first open therefore remain the one operator action still to perform.

## Download artifact

- Personalized bundle: `JobOS-MacBook-Demo-2026-07-21.zip`.
- Size: `144,118,016` bytes.
- SHA-256: `c25edbe25277220d6b61cc480d3045c508cb5f896e82b01536e0bb4c002fe99d`.
- The same artifact was downloaded back through its private HTTPS route, matched the expected size/hash, and passed ZIP integrity validation.
- Installer shell syntax, nested app extraction, arm64 executable, bundle identifier, version, and packaged Keychain helper were verified.

## Demo limitations and recovery

- The app and installer are unsigned. If macOS blocks the installer, Cobi must right-click **Install JobOS.command**, choose **Open**, then confirm **Open**.
- Both Macs must have Tailscale running.
- The Mini must be awake and logged into Cobi's user session so its user LaunchAgents are available.
- If the app cannot connect, first confirm Tailscale is running on the MacBook; then inspect the Mini's JobOS LaunchAgent and private route.
- Reopen hardening when the app becomes multi-user, leaves the tailnet, needs public distribution, or needs a polished credential rotation/update path.

## Operator next action

Download the personalized bundle from the private link, unzip it, and run **Install JobOS.command**. After JobOS opens and shows **Mac Mini connected**, trash the downloaded ZIP and extracted installer folder.

## 2026-07-21 packaging correction

The first personalized bundle produced macOS's “JobOS is damaged” message. Direct verification found that Electron Builder had skipped top-level signing while nested Electron resources retained signatures, leaving an internally inconsistent app bundle. Removing quarantine could not repair that invalid signature state.

The packaging flow now ad-hoc signs the complete app bundle, verifies the deep signature, and creates the ZIP only after verification. The personalized installer also repeats ad-hoc signing and verification after copying the app as a defensive check, then removes quarantine.

Corrected private bundle proof:

- size: `143,109,993` bytes;
- SHA-256: `3c9431978d519433c34f49fa1ad97b986a961d81814c331ca0051db3909f71be`;
- nested app deep-signature verification: passed;
- installer shell validation: passed;
- private-route download and ZIP integrity: passed.

## 2026-07-21 MacBook connectivity correction

The first installed MacBook app displayed **JobOS API offline** even though the Mini API, Tailscale peer, and private Serve route were healthy. The installer wrote `runtime.json` to the intended stable path under `Application Support/JobOS`, but the packaged Electron app read `app.getPath('userData')`. Because the package name is `@jobos/desktop`, that resolved to a different directory on the MacBook, so the app never loaded its remote API URL.

The desktop now derives its runtime configuration from `app.getPath('appData')/JobOS/runtime.json`, matching the installer and runtime architecture. A focused regression test covers this stable path. A tiny private repair bundle was also provided for the already-installed app; it copies the existing runtime config into the legacy package-derived directory and reopens JobOS, avoiding another full download.

## Final MacBook activation outcome

Cobi confirmed that JobOS successfully opened and connected to the Mac Mini API over Tailscale after running the personalized **Start JobOS Connected** launcher.

The successful launcher performed the complete setup explicitly rather than relying on implicit installer behavior:

1. located JobOS in either `/Applications` or `~/Applications`;
2. called the Mini's private Tailscale endpoint and required an authenticated HTTP `200` before continuing;
3. wrote `runtime.json` to both the canonical `Application Support/JobOS` path and the legacy package-derived path used by the already-installed build;
4. stored the paired MacBook device credential through JobOS's Keychain helper;
5. launched JobOS with explicit remote-client environment values;
6. verified that the JobOS process stayed running.

This proved the intended architecture—**MacBook desktop client → Tailscale → Mac Mini proxy → loopback JobOS API**—works end to end. The remaining problem is installation/onboarding quality, not the core product topology.

## Accepted demo debt: MacBook installation is too complicated

The demo required multiple avoidable manual steps and several repair bundles:

- unsigned/unnotarized Gatekeeper overrides;
- repair of an internally inconsistent Electron signature;
- manual **Open Anyway** approval;
- disagreement between `/Applications` and `~/Applications`;
- disagreement between canonical and package-derived runtime-config directories;
- repeated credential/config repair attempts;
- a final launcher that injected known-good runtime values directly.

Do not treat this as an acceptable release workflow. Before wider use, replace it with one deterministic install-and-first-run path tracked in `docs/plans/macos-install-and-onboarding-hardening.md`.

## 2026-07-21 remote browser agent activation

The live JobHunter agent can now inspect and operate the exact embedded JobOS browser on the remote MacBook through the authenticated JobOS capability channel.

Activation required three fixes:

1. registered the JobOS stdio MCP adapter in the live `job-hunter` Hermes profile using a private launcher that reads the existing local runtime credential at process start; no credential was copied into Hermes YAML;
2. registered the same MCP adapter in the unified dashboard host configuration because that long-lived dashboard currently performs MCP discovery in its launch-profile scope before building profile-scoped sessions;
3. fixed the JobOS desktop capability WebSocket to authenticate every credential in the configured device registry, not only the Mini's primary credential. Before this change, the MacBook repeatedly connected but was closed with authentication code `4401`, so MCP commands reported `503 Desktop capability is unavailable`.

Verification evidence:

- JobOS MCP discovery: connected, with the full `mcp__jobos__*` browser toolset present in the live JobHunter session;
- direct MCP execution: `browser_tabs_inspect` returned the MacBook's embedded tabs and `browser_snapshot` returned the active page;
- live JobHunter turn through the real JobOS conversation API called `mcp__jobos__browser_tabs_inspect` and `mcp__jobos__browser_snapshot`, then reported `JOBOS_REMOTE_BROWSER_OK` for **Product Manager at Barti • United States • Remote (Work from Home) | Wellfound**;
- complete Python suite: 313 tests passed with one expected skip;
- Ruff: passed for all changed Python files.

The distinction remains intentional: selected-job/workspace context is injected automatically, while full browser-page content is inspected on demand through JobOS MCP. Generic Hermes `computer_use` still targets the Mac Mini and is not the remote-browser path.
