# MacBook Browser Save Update — 2026-07-21

## Outcome

Prepared a private, versioned MacBook update bundle for the shipped active-browser job save flow and the Greenhouse compatibility fix.

Source commit packaged:

- JobOS `main`: `4b417db0d3790f96a18435dcb058c99ed6771127`

## Operator artifact

- Filename: `JobOS-Browser-Save-Update-2026-07-21.zip`
- Private tailnet URL: `https://jacobis-mac-mini.tailf1a3a1.ts.net:10449/JobOS-Browser-Save-Update-2026-07-21.zip`
- Size: `143120304` bytes
- SHA-256: `d8cb9637330956023331e634a499ac1d593811a1c0d9c9753c084fa173d22377`

The updater keeps the existing MacBook runtime configuration, Keychain credential, selected jobs, and browser state. It replaces only `JobOS.app` and reopens it.

## Verification

- Rebuilt the arm64 app from current `main` with `pnpm --filter @jobos/desktop package:mac`.
- Ad-hoc signing completed after packaging.
- `codesign --verify --deep --strict` passed on the extracted app.
- Confirmed the main executable is arm64.
- Inner application ZIP integrity passed.
- Updater script passed `zsh -n`.
- Outer update ZIP integrity passed.
- Copied the artifact into the active private HTTP server root.
- Downloaded the artifact back through Tailscale HTTPS port `10449`.
- Downloaded size, SHA-256, and ZIP integrity matched the local source exactly.
- Restarted `com.cobibean.jobos.api` so the Mini loaded the new `POST /v1/jobs` route and updated Job Hunter facade.
- Live Mini verification passed: health `ready`, agent `online`, browser-create route loaded, and the existing MacBook credential returned HTTP `200` for `/v1/jobs`.

## Installation

1. Quit JobOS.
2. Download and unzip the private update.
3. Double-click `Update JobOS.command`.
4. JobOS replaces the existing app and reopens.

This remains a private one-user unsigned demo update, not a notarized public release.
