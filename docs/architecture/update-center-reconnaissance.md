# JobOS Update Center: Current Pipeline Map and Future Architecture

> **Status:** Long-range architecture proposal, not an implementation plan.
>
> **Idea context:** [`docs/ideas.md#in-app-update-center`](../ideas.md#in-app-update-center)
>
> **Evidence baseline:** `origin/main` at `da56f35cd1b97e89cd2ac64f4b57779d01dbf2b4` on 2026-08-17.

## Executive summary

JobOS already has a carefully verified **private MacBook desktop updater**, but it is a manually built and manually transferred outer ZIP. It has strong local replacement and rollback mechanics, yet no update discovery service, release manifest, in-app update state, official signing/notarization, or public binary release workflow.

The safest future direction is to keep the existing process-interruption-recoverable replacement engine and add two clearly separated systems around it:

1. a **maintainer release pipeline** that certifies and publishes immutable official releases; and
2. an **installed-app Update Center** that discovers and installs only releases it can authenticate.

Official builds and self-built source installations must remain separate lanes. A self-built or modified copy should never be silently replaced by an official binary.

## Current pipeline

```text
Clean exact Git commit
        |
        | JOBOS_EXPECTED_SOURCE_COMMIT must equal HEAD
        v
create-macbook-update.mjs
        |
        +--> build contracts + desktop
        +--> electron-builder creates arm64 JobOS.app
        +--> ad-hoc sign + deep verify
        +--> create JobOS-<version>-arm64.zip
        +--> inspect archive shape and app identity
        +--> generate Update JobOS.command
        +--> generate VERIFIED.txt receipt
        +--> create unique outer updater ZIP
        +--> extract and run adversarial updater smokes
        v
release/desktop/macbook/JobOS-MacBook-Update-<timestamp>-<commit>-<nonce>.zip
        |
        | manual private transfer, currently outside the repository workflow
        v
User unzips and runs Update JobOS.command
        |
        +--> verify inner SHA-256 and code signature
        +--> acquire kernel-managed advisory update lock
        +--> recover an interrupted prior transaction
        +--> stage replacement beside the installed app
        +--> quit exact JobOS process and prove it exited
        +--> preserve prior app in a recoverable backup
        +--> replace and verify /Applications/JobOS.app or ~/Applications/JobOS.app
        +--> relaunch and prove the new process is immediately alive
        +--> commit, or restore the previous app on failure
```

## Source-to-install evidence

### 1. Package entry points

`apps/desktop/package.json` owns both current packaging commands:

- `package:mac` builds helpers and the desktop, runs `electron-builder --mac dir --arm64`, then runs `scripts/sign-and-zip-mac.mjs`.
- `package:macbook-update` runs `scripts/create-macbook-update.mjs`.

The Electron build is currently:

- product: `JobOS`;
- bundle identifier: `com.cobibean.jobos`;
- architecture: arm64-only in the private updater contract;
- signing identity: `null` in electron-builder, followed by ad-hoc signing;
- output: `release/desktop/`.

There is no `electron-updater` dependency, auto-update provider configuration, or update publication metadata.

### 2. Inner application archive

`apps/desktop/scripts/sign-and-zip-mac.mjs`:

1. ad-hoc signs the unpacked `JobOS.app`;
2. deep-verifies the signature;
3. creates `JobOS-<version>-arm64.zip` with `ditto`.

This is appropriate for a trusted private demo boundary. It is not Developer ID signing or Apple notarization.

### 3. Outer local updater

`apps/desktop/scripts/create-macbook-update.mjs` is the real user-facing packaging authority. It requires:

- an exact full expected source commit;
- `HEAD` equality with that commit;
- a completely clean worktree before and after packaging;
- one exact app root in the inner ZIP;
- safe ZIP names, member types, bounds, compression, and symlinks;
- arm64, bundle, executable, CodeDirectory, and ad-hoc-signature identity checks.

The outer ZIP contains exactly:

```text
JobOS-MacBook-Update-.../
├── Update JobOS.command
├── VERIFIED.txt
└── JobOS-<version>-arm64.zip
```

The generated updater already provides unusually strong replacement behavior:

- checksum validation;
- code-signature verification before and after staging;
- a kernel-managed `lockf` lock;
- a private transaction marker that supports next-run recovery after process interruption;
- exact previous-app backup;
- interrupted-update recovery;
- bounded process shutdown and relaunch checks;
- rollback before the explicit commit point;
- cleanup after commit.

Its smoke suite covers fresh installation, replacement, rollback, interrupted existing/fresh installations, post-commit failure, and preservation of external config/state sentinels.

### 4. What the updater deliberately does not update

`VERIFIED.txt` states the current boundary accurately: the wrapper replaces only the desktop app. It does **not** update:

- the JobOS API service;
- runtime configuration;
- application data;
- Keychain credentials.

This matters because JobOS can run in at least two compositions:

- a desktop connected to a separately managed remote API host; or
- the local-first public architecture, where the API is part of the product system.

A future updater must model desktop and service compatibility explicitly rather than implying that one app replacement upgrades every component.

### 5. CI and publication

`.github/workflows/ci.yml` currently runs source quality and clean-clone checks. It does not:

- package an official macOS release;
- sign or notarize an app;
- publish GitHub Release assets;
- publish an authenticated update manifest;
- test prior-installed-version → update → rollback → update-forward.

`docs/public/release-process.md` intentionally describes JobOS as a source-first public alpha with no supported public binary. It reserves signed/notarized binaries and update/rollback acceptance for a later track.

### 6. Installed UI

The desktop currently has no update discovery or installation seam:

- no update state machine;
- no update-specific main-process module or IPC contract;
- no renderer badge, Update Center, progress, restart, or rollback status;
- no official-versus-self-built distribution identity.

The main process already owns trusted desktop capabilities and is the correct boundary for future update networking, verification, staging, and installer launch. The renderer should receive only a narrow projection of update state.

## Protected invariants

Any future Update Center must preserve the existing guarantees and explicitly close the known durability gaps:

1. **Exact provenance:** an artifact must bind to one immutable source commit.
2. **Stable macOS identity:** bundle and Keychain identifiers cannot drift during an update.
3. **No credential exposure:** release checks and diagnostics never receive or print Keychain tokens.
4. **Recovery boundary:** current tests prove process-interruption recovery; a public updater must additionally prove power-loss durability, including persisted marker and rename ordering.
5. **One updater at a time:** the kernel-backed update lock remains authoritative.
6. **External-state preservation:** app data, runtime configuration, API state, and Keychain credentials stay outside desktop replacement.
7. **Old-client compatibility:** a newly published release must be installable and verifiable by the updater already present on the prior version.
8. **No custom-build overwrite:** source/self-built copies require an explicit source-update path, not silent official binary replacement.
9. **Component honesty:** desktop, API service, schema, and runtime compatibility are reported separately.

## Proposed future architecture

```text
                         MAINTAINER LANE

Clean release commit
        |
        v
Build + full CI + installed prior-version acceptance
        |
        v
Developer ID sign + Apple notarize
        |
        v
Immutable release assets + canonical signed manifest
        |
        +--> desktop artifact identity, size, SHA-256, architecture
        +--> minimum compatible desktop/API/state schema
        +--> release sequence and channel
        +--> release notes URL
        +--> detached manifest signature
        v
Official release channel

                         CONSUMER LANE

JobOS main process
        |
        | bounded HTTPS fetch from allowlisted release origin
        v
Authenticate manifest before selecting a release
        |
        +--> official build? eligible for one-click install
        +--> self-built build? information/source-update lane only
        v
Update state machine
available -> downloading -> verified -> ready_to_install
          -> installing -> relaunching -> completed
          -> failed/rolled_back
        |
        v
Existing interruption-recoverable updater transaction
        |
        v
Relaunch exact installed JobOS + report result
        |
        v
Renderer receives narrow status projection
```

### A. Distribution identity

Every packaged app should carry immutable, non-secret build metadata, for example:

```json
{
  "distribution": "official",
  "version": "0.2.0",
  "releaseSequence": 7,
  "sourceCommit": "<full commit>",
  "channel": "stable"
}
```

Recommended behavior:

- `official`: may check and install authenticated official binary updates;
- `source` or missing metadata: may show that a newer source release exists, but must not self-replace with an official binary;
- locally modified/custom distributions: updater disabled unless the distributor provides its own trust root and channel.

### B. Signed release manifest

Use a small canonical manifest, published beside immutable release assets. It should include only bounded fields required by the old installed client:

- schema version;
- monotonically increasing release sequence;
- app version and full source commit;
- stable/beta channel;
- platform and architecture;
- exact asset name and immutable URL;
- byte size and SHA-256;
- bundle identifier and expected signing identity;
- minimum compatible desktop version;
- compatible API and state-schema range;
- release notes URL;
- detached signature.

The app should pin the manifest verification key. GitHub/TLS availability alone should not authorize replacement.

### C. Main-process update service

Create a future `UpdateService` in the Electron main process. It should own:

- scheduled and manual update checks;
- manifest fetching and signature verification;
- release selection by channel and monotonic sequence;
- bounded artifact download with size/hash enforcement;
- macOS signature, team, bundle, architecture, and notarization checks;
- staging and handoff to the replacement process;
- update recovery/result reporting after relaunch.

Expose narrow IPC operations such as:

- `getUpdateState()`;
- `checkForUpdates()`;
- `downloadUpdate()`;
- `installAndRestart()`;
- update-state events containing no filesystem paths, credentials, raw manifests, or arbitrary URLs.

The renderer must not download packages, validate signatures, choose destinations, or execute updater commands.

### D. Reuse the existing replacement engine

Do not build a second installer beside `Update JobOS.command`. Extract the current proven transaction semantics into one reusable, testable updater engine or generate both private and in-app launchers from the same implementation.

The in-app sequence should be:

1. main process authenticates manifest and downloads the exact authorized asset;
2. main process verifies bytes and macOS identity;
3. app launches a separately staged updater with a narrow, immutable request;
4. app quits;
5. updater performs the existing lock/stage/backup/replace/verify/relaunch transaction;
6. relaunched app reads a non-secret result receipt and shows success, rollback, or actionable failure.

Keeping replacement outside the running app avoids asking a process to overwrite its own bundle.

### E. UI projection

Keep the first user experience small:

- a quiet update badge near app/settings chrome;
- an Update Center panel showing current version, available version, release notes, and component compatibility;
- explicit **Download**, then **Update and restart** actions;
- progress and truthful states: checking, downloading, ready, restarting, rolled back, or failed;
- no automatic installation by default.

Later, official builds could support a user preference for automatic background download, while installation remains explicit.

### F. Desktop/API compatibility

The release model must distinguish:

- **desktop-only update:** safe when the connected API and state schema remain compatible;
- **coordinated desktop + local service update:** required when the public binary owns a managed local API;
- **host-first remote update:** remote service changes activate before a desktop client when the old desktop remains compatible;
- **protocol migration:** required when an old client cannot authenticate, stage, activate, or roll back the new component set.

The Update Center should block installation when compatibility evidence says the connected service is too old or too new, and explain which component must update first.

## Suggested maturity slices

These are architecture slices, not scheduled work.

### Slice 1 — Release awareness only

- Add distribution/build metadata.
- Publish a read-only release feed.
- Show “Update available” and release notes.
- Official and source builds both remain manually updated.

This proves discovery and official/self-built separation without creating a replacement authority.

### Slice 2 — Private official one-click update

- Use the existing ad-hoc/private artifact boundary.
- Main process authenticates a private manifest and downloads the verified outer artifact.
- Reuse the current updater transaction and result receipt.
- Limit to Cobi-owned trusted Macs.

This improves current operations but is not a public binary release.

### Slice 3 — Public official updater

- Developer ID signing and notarization.
- Protected release/signing environment.
- Signed stable/beta manifests and immutable public assets.
- Prior-version update/rollback/update-forward acceptance.
- Official one-click install enabled only for official builds.

This is the point where the updater becomes a supported public product surface.

## Open decisions for the far-away planning phase

1. Will the first supported public binary bundle/manage the FastAPI service, or remain a desktop client of a separately installed service?
2. Will official releases use GitHub Releases directly, a dedicated static release origin, or both?
3. Which signing key and rotation/bridge policy will authenticate manifests?
4. Does the first public updater support only Apple Silicon, or universal/architecture-specific assets?
5. How long must previous artifacts remain available for rollback?
6. What API/state-schema compatibility ranges can an old client understand and enforce?
7. Should private Cobi builds and future public official builds share one channel implementation with different trust roots, or remain operationally separate?

## Recommendation

When this idea eventually moves into planning, begin with **Slice 1: release awareness and distribution identity**. It creates the minimum contract every later updater needs, makes official versus self-built behavior explicit, and does not yet grant the application authority to replace itself.

Do not start by adding an updater library or drawing a badge. The durable first boundary is the authenticated release identity that an old installed client can safely understand.
