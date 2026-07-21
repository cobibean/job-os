# macOS Install and Onboarding Hardening

## Status

**Deferred demo debt.** The one-user JobOS demo is operational, and Cobi confirmed the MacBook app connects to the Mac Mini API through Tailscale. Do not spend more time on this until the demo has been used enough to justify release hardening.

## Problem

The core cross-device architecture works, but reaching the first successful launch required too many manual and repair steps. A future user should not need to understand Gatekeeper, runtime paths, Keychain helpers, Tailscale proxy ports, Terminal commands, or the difference between system and personal Applications folders.

## What happened in the demo

1. Electron Builder produced an app with inconsistent nested signatures, leading macOS to report that JobOS was damaged.
2. The build was changed to ad-hoc sign and deeply verify the complete app before creating the ZIP.
3. macOS still required **Privacy & Security → Open Anyway** because the demo is not Developer ID signed or notarized.
4. The installer used `~/Applications`, while Cobi reasonably moved JobOS to `/Applications`.
5. The installer wrote runtime configuration to `Application Support/JobOS`, while the already-built app looked in a package-derived user-data directory.
6. Repair scripts made incorrect assumptions about the prior installer and app location.
7. The successful launcher explicitly authenticated to the Tailscale endpoint, installed config in both paths, stored the device token in Keychain, and launched the app with known-good remote-client environment values.

## Target experience

The desired flow is:

1. Download one private artifact.
2. Open or drag JobOS to Applications.
3. Approve one expected macOS prompt at most.
4. JobOS discovers or receives the Mini endpoint.
5. Device pairing completes once and stores its credential in Keychain.
6. The app opens showing **Mac Mini connected**.
7. Every later launch works by opening JobOS normally.

No Terminal window, repair ZIP, hidden credential file, path copying, or environment-variable launcher should be required.

## Hardening backlog

### P0 — deterministic runtime and credential bootstrap

- Use one stable runtime-config location owned by JobOS.
- Make first-run setup create or validate that config atomically.
- Detect a missing or invalid credential and present a specific recovery action.
- Validate the API endpoint, device ID, and token together before completing setup.
- Persist enough safe diagnostic state to distinguish configuration, authentication, Tailscale, and API failures.

### P0 — one installation target

- Choose and document `/Applications` or `~/Applications`.
- Make installers and launch/repair logic detect both locations during migration.
- Remove assumptions tied to where the archive was extracted.

### P1 — proper macOS distribution

- Obtain explicit approval before using a paid Apple Developer account.
- Developer ID sign and notarize the app and all nested helpers.
- Verify the final downloaded artifact with `codesign`, `spctl`, checksum validation, and a clean-machine smoke test.
- Package as a polished DMG or installer rather than a nested personalized ZIP plus shell command.

### P1 — first-run pairing UI

- Replace embedded credential files with a short-lived pairing flow.
- Let the Mini authorize/revoke a MacBook device visibly.
- Keep the Mini API loopback-only and expose only the JobOS proxy through tailnet-only Tailscale Serve.
- Show actionable states such as **Tailscale unavailable**, **Mini offline**, **pairing expired**, and **credential rejected**.

### P1 — clean Mac acceptance test

Test from a Mac account with no prior JobOS files or Keychain entries:

- install;
- first open;
- Gatekeeper behavior;
- pairing;
- API health and authenticated session;
- job list load;
- agent conversation;
- quit and normal relaunch;
- app moved between personal and system Applications folders;
- credential revocation and recovery.

## Exit criteria

This debt is closed only when a clean MacBook reaches **Mac Mini connected** from one documented installer flow, relaunches normally without Terminal, and passes the cross-device acceptance test without a repair artifact.
