# Phase 9 macOS Runtime and Private Access

## Status

Approved for implementation on July 20, 2026. This document is the durable source of truth for the Phase 9 build and was written before lifecycle, credential, or Tailscale code changed.

## Goal

Opening `JobOS.app` on the Mac Mini should recover or start the local JobOS API and connect without requiring a terminal. A MacBook installation should connect to that same Mini-hosted API through a private Tailscale gateway without starting a second authoritative API.

## Product contract

### Mac Mini: `local-service`

1. JobOS reads non-secret runtime configuration from the user Application Support directory.
2. It retrieves the device credential from macOS Keychain.
3. It probes the loopback API at `http://127.0.0.1:8766`.
4. If the API is unavailable, it asks the per-user `launchd` service `com.cobibean.jobos.api` to start or recover it.
5. It waits on bounded readiness probes rather than a fixed sleep.
6. It creates the workbench using one immutable runtime configuration shared by every main-process client.
7. Closing a JobOS window does not kill the API. `launchd`, not Electron, owns the service lifecycle.

### MacBook: `remote-client`

1. JobOS reads a configurable private HTTPS base URL and device identity from Application Support.
2. It retrieves that device's credential from macOS Keychain.
3. It connects to the Mini through the configured gateway.
4. It never starts a local authoritative API.
5. Offline, gateway-unavailable, and authentication failures remain distinct user-facing connectivity states.

## Network topology

```text
Mac Mini JobOS.app ──────────────> 127.0.0.1:8766
                                            ^
                                            |
MacBook JobOS.app -> private HTTPS gateway -+
                       (Tailscale Serve)
```

The Python API continues to bind only to loopback. Tailscale Serve is an optional deployment adapter that forwards a private tailnet HTTPS endpoint to loopback. The API must not bind to `0.0.0.0`, a LAN address, or a public interface.

The Mini deliberately uses loopback rather than its own Tailscale URL. Local use must continue when Tailscale, DNS, or the internet is unavailable.

## Open-source boundary

Core JobOS code knows only:

- runtime mode: `local-service` or `remote-client`;
- API base URL;
- device identity;
- credential-provider interface;
- optional local lifecycle adapter.

The repository must not contain an operator's tailnet hostname, Tailscale IP, device token, Hermes token, credential value, or machine-specific runtime path. Tailscale is the recommended private adapter for this installation, not a mandatory product dependency. Other operators may use local-only mode, Headscale/WireGuard, an SSH tunnel, or another authenticated private reverse proxy.

Development environment variables remain supported as the highest-priority override so existing tests and disposable runs stay simple.

## Configuration and credentials

### Non-secret runtime configuration

Default path:

```text
~/Library/Application Support/JobOS/runtime.json
```

Schema version 1:

```json
{
  "schemaVersion": 1,
  "mode": "local-service",
  "apiBaseUrl": "http://127.0.0.1:8766",
  "deviceId": "operator-selected-device-id",
  "launchdLabel": "com.cobibean.jobos.api"
}
```

Remote mode requires a private `https://` base URL and omits `launchdLabel`. The loader rejects unknown keys, non-loopback local URLs, non-HTTPS remote URLs, oversized values, embedded URL credentials, query strings, and fragments.

### Secret storage

Device credentials live in macOS Keychain under a stable JobOS service name and the configured device ID as account. The renderer never receives them. The API service retrieves its required secrets during startup; no secret is stored in the launchd plist, runtime JSON, repository, application archive, logs, screenshots, or command output.

Environment-provided credentials are development overrides only.

## Lifecycle ownership

`launchd` is the single owner of the API process on the Mini:

- label: `com.cobibean.jobos.api`;
- per-user domain: `gui/<uid>`;
- loopback listener only;
- `RunAtLoad` enabled;
- restart on abnormal exit with bounded launchd throttling;
- stable stdout/stderr files under Application Support;
- no Electron child-process ownership;
- no API termination when a window closes.

The desktop lifecycle module has one narrow interface:

```text
ensureApiReady(runtimeConfig, credential) -> connectivity result
```

Its implementation probes, optionally kickstarts launchd once, polls with a bounded deadline, and returns a classified failure. Concurrent Electron launches can issue duplicate `kickstart` requests safely because launchd owns one labeled job; they cannot spawn duplicate Python processes directly.

## Service installation model

Phase 9 installs a development/runtime service from an explicitly supplied JobOS checkout and Python environment. The installer:

1. validates the checkout, interpreter, API import, and required non-secret paths;
2. writes a mode-0600 service configuration outside the repository;
3. provisions secrets through the Keychain adapter without printing values;
4. writes and bootstraps a per-user launchd plist;
5. verifies the exact loopback listener and authenticated readiness;
6. supports an idempotent status command and a bounded uninstall/rollback path.

This phase does not pretend the Python runtime is embedded in the Electron archive. A later signed distribution may bundle or separately certify that runtime without changing the desktop lifecycle interface.

## Tailscale adapter

A separate operator script configures Tailscale Serve from an explicit private hostname/port context discovered at runtime. It must:

- require Tailscale to report `Running`;
- forward private HTTPS to `http://127.0.0.1:8766`;
- never enable Funnel/public exposure;
- print only non-secret endpoint and status information;
- verify that the API listener itself remains loopback-only;
- provide a status command and exact rollback command;
- avoid writing the actual tailnet hostname into tracked files.

The MacBook receives its own device ID and revocable device token. It must not reuse a credential intended for another machine.

## Readiness and errors

Readiness is accepted only after both checks succeed:

1. `GET /v1/health` returns the expected JobOS ready contract.
2. `GET /v1/device-session` authenticates the configured device credential.

Classifications:

- `starting`: local launchd service was requested and readiness is pending;
- `connected`: health and device authentication passed;
- `service-unavailable`: local service or remote gateway could not be reached;
- `authentication-failed`: API responded but rejected the device;
- `configuration-required`: runtime config or Keychain credential is missing/invalid;
- `private-network-unavailable`: remote private hostname could not be reached.

No fixed sleep is acceptance evidence.

## TDD and verification matrix

Automated tests must first fail, then pass, for:

- environment override precedence;
- valid local and remote config loading;
- malformed/unsafe config rejection;
- Keychain read success, missing item, and helper failure without secret logging;
- healthy API reuse without launchctl;
- local unavailable API triggering one launchd kickstart;
- readiness after delayed startup;
- bounded timeout and child/service failure classification;
- remote mode never invoking launchctl;
- duplicate ensure calls remaining safe;
- Tailscale command generation never using Funnel or a non-loopback target;
- installer rendering no secret into plist/config.

Disposable native proof must demonstrate:

1. no API running;
2. Finder-style packaged app launch with no shell-provided API URL or token;
3. launchd starts the disposable API;
4. the app authenticates and renders the real three-pane workbench;
5. a second app launch reuses the one API PID;
6. API termination is recovered by launchd/app readiness;
7. app closure leaves the shared API lifecycle correct;
8. `lsof` shows only a loopback API listener;
9. remote mode never starts a local API;
10. app archive, zip, plist, runtime JSON, and logs contain no credential values.

## Activation gate

Disposable implementation and proof are authorized by this phase. Installing the persistent service against the authoritative Job Hunter database and configuring the live private Tailscale route are deployment mutations. They occur only after the candidate passes tests, review, secret scanning, and disposable native proof, followed by an explicit activation confirmation.

## Non-goals

- Public internet exposure.
- Tailscale Funnel.
- Binding the API to all interfaces.
- Bundling personal hostnames or credentials.
- Shipping a universal Python runtime in the first lifecycle slice.
- Developer ID signing/notarization; this remains a separate distribution decision.
- Changing authoritative Job Hunter data during disposable verification.
