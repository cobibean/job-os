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
