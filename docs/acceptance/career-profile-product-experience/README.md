# Career Profile product-experience acceptance

This directory records the packaged-app acceptance for [Issue #56](https://github.com/cobibean/job-os/issues/56). The checked-in report is historical evidence for the exact source and package identities recorded in `acceptance-report.json`; it is not automatically evidence for later commits. The run used an isolated JobOS profile containing only visibly `(FAKE)` synthetic data. It did not read or mutate a live Career Profile, deploy JobOS, begin Issue #57, or perform the Issue #58 authority cutover.

## Verdict

**Passed.** The packaged arm64 app exercised the complete Career Profile journey across wide and narrow layouts, restarted twice, exported a zero-Evidence archive plus all three supported scopes through renderer → preload → main, restored a profile-only archive through that same native path, and exposed a bounded accessibility tree.

- Package, canonical product-source, generated-output, exercised-app, and ZIP-app identities: recorded in `acceptance-report.json`
- API binding: loopback only
- Credential provider: disposable local file
- Accessibility nodes observed: 336
- Synthetic Evidence sources: exactly one
- Restore result: revision 7, one non-undoable baseline, agent context reset to `none`

The machine-readable result is in [`acceptance-report.json`](acceptance-report.json).

## Current-commit frontend smoke

Run `pnpm test:frontend:packaged` on arm64 macOS to build the current commit and exercise the same visible packaged Electron journey against a disposable synthetic profile. The CI smoke crosses renderer → preload → main → API/storage, exports and restores archives, restarts the app, and fails when an interactive accessibility node has no accessible name. It deliberately does not claim that newly captured pixels match the separately reviewed historical screenshots.

## Captured journey

| Evidence | Product behavior proven |
| --- | --- |
| `01-wide-my-career-1440x1024.png` | My Career area and typed career details |
| `02-wide-looking-for-1440x1024.png` | What I’m Looking For preferences and explanations |
| `03-wide-evidence-imported-1440x1024.png` | Evidence import and available per-file state |
| `04-wide-agent-access-1440x1024.png` | Exact saved agent scope preview with zero implicit Evidence |
| `05-wide-export-choices-1440x1024.png` | Explicit profile-only, selected-Evidence, and all-Evidence choices |
| `06-wide-restore-warning-1440x1024.png` | Destructive restore warning and baseline semantics |
| `07-narrow-detail-980x640.png` | Narrow-layout accessible detail surface |
| `08-wide-after-restart-1440x1024.png` | Profile and Evidence persistence after packaged-app restart |
| `09-narrow-restored-baseline-980x640.png` | Restored baseline with excluded Evidence clearly marked unavailable |

Every PNG was visually inspected, stripped of metadata, and checksum-pinned in `tests/public-release/synthetic-fixtures.json`.

## Archive proof

The runner selected every export option in the packaged UI. A fail-closed acceptance-only dialog-path hook supplied deterministic paths inside the disposable `TMPDIR`; production runs still use native dialogs. Main then used the packaged descriptor-relative native writer for verified atomic writes. The runner inspected the resulting archive members directly:

- `zeroEvidence`: `manifest.json` only
- `profileOnly`: `manifest.json` only
- `selected`: `manifest.json` plus the one selected synthetic Evidence blob
- `all`: `manifest.json` plus the one active synthetic Evidence blob

It also verified packaged drag/drop, failed-import retry after API recovery, zero-Evidence context preview/export, native restore selection, typed destructive confirmation, keyboard/focus return, restart readback, excluded revision history/agent settings, unavailable omitted Evidence, disappearance of post-export changes, and a single non-undoable restored baseline. The report records the exact pytest node ID and `passed` result for each protocol lifecycle claim. In particular, the named complete-context recovery test restarts the service around an active turn and proves the exact selected item remains frozen, while the named expansion test adds an unselected project to the bound projection and proves integrity rejection occurs before gateway dispatch.

## Stable candidate and output identity

The report is bound to `HEAD` and a canonical product-source digest of the staged Git index. The harness reads every stage-0 index blob, constructs entries containing `path`, Git `mode`, byte `size`, and content SHA-256, orders them by UTF-8 path bytes, serializes them as recursively key-sorted compact JSON, and hashes that serialization with SHA-256. It applies only these exclusions/canonicalizations:

- excludes `docs/acceptance/career-profile-product-experience/acceptance-report.json`, because the report is the receipt and cannot hash itself;
- excludes PNGs directly under this acceptance directory, because they are generated evidence;
- keeps `tests/public-release/synthetic-fixtures.json` in the source manifest but canonicalizes it by removing only asset records whose path is one of those excluded acceptance PNGs. Every other manifest field and asset record remains source identity.

Therefore rerunning acceptance, copying the report/screenshots, or updating only their matching synthetic-fixture checksum records cannot change the canonical product-source digest recorded by the report. Any other staged source change does change it. The harness separately records a sorted generated-output manifest (`path`, `size`, SHA-256) and digest for all nine screenshots, and fails unless every screenshot hash matches its synthetic-fixture record. The report itself is intentionally not self-checksummed.

The report also records the exercised `.app` manifest SHA-256 and ZIP-extracted `.app` manifest SHA-256. The run fails unless those bundles are byte-for-byte equivalent by sorted file/symlink/mode manifest. Unstaged generated report/PNG changes are allowed; an unstaged fixture-manifest change is allowed only when its canonicalized non-acceptance content still equals the staged blob. Other unstaged product-source changes fail the run.

## Reproduce

Requirements: macOS arm64, Node and pnpm from the repository toolchain, `uv`, and a completed desktop package.

Stage the exact candidate first; the runner refuses unstaged product-source changes outside the documented generated-output exception so its source and package identity claims cannot drift.

```bash
pnpm check
pnpm --filter @jobos/desktop package:mac

runtime="$(mktemp -d "${TMPDIR:-/tmp}/jobos-issue56-acceptance.XXXXXX")"
output="$(mktemp -d "${TMPDIR:-/tmp}/jobos-issue56-screens.XXXXXX")"
mkdir -p "$runtime/home" "$runtime/tmp" "$runtime/xdg-config" "$runtime/xdg-cache" "$runtime/xdg-data"
chmod 700 "$runtime" "$output" "$runtime/home" "$runtime/tmp" "$runtime/xdg-config" "$runtime/xdg-cache" "$runtime/xdg-data"

JOBOS_KEYCHAIN_HELPER_PATH="$runtime/disabled-keychain-helper" \
  uv run jobos-init --data-dir "$runtime/profile" --no-demo

JOBOS_ACCEPTANCE_RUNTIME="$runtime" \
JOBOS_ACCEPTANCE_OUTPUT="$output" \
  node docs/acceptance/career-profile-product-experience/capture.mjs
```

Set `JOBOS_ACCEPTANCE_UV` to an absolute executable path if `uv` is not available on `PATH`. The runner chooses unused loopback ports, confines HOME/TMP/XDG paths to the disposable runtime, and terminates its API and app processes on exit.
