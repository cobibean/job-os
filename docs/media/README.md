# JobOS launch-media workflow

This repository-owned workflow captures the real production Electron renderer with only the approved synthetic starter profile. Output targets are accepted only after the documented command generates and verifies them and an independent reviewer approves them; README image embeds must not be added before then.

## Assets and static equivalents

| Asset | Description | Dimensions / duration |
|---|---|---|
| `screenshots/jobos-hero-1440x1024.png` | Review workbench with the fictional Northstar Kites Demo job selected | 1440×1024 PNG |
| `screenshots/jobos-browse-detail-1440x1024.png` | Local Browse list and detail for the fictional Demo role; **Open job** is not activated | 1440×1024 PNG |
| `screenshots/jobos-ooxml-editor-saved-1440x1024.png` | Retained-OOXML editor displaying the approved `(FAKE)-cover-letter.docx` fixture with **Saved** visible | 1440×1024 PNG |
| `jobos-demo.gif` | Silent sequence: accepted Review workspace → Demo selected → Browse workspace → Review workspace → fake document editor | 960×683 GIF, 12 fps, 120 frames, 10 seconds, ≤128-color palette |

The three full-resolution PNGs will be the static equivalent of the animation. The capture command generates exact artifact SHA-256 values in `checksums.sha256`.

## Provenance and reproducible capture

- Source base: accepted Phase 7 merge `66f8aff2d002b408eb117ed960eecb54f67b7542`. The eventual candidate must record the exact committed Phase 8 revision used for capture.
- Renderer: `apps/desktop/dist/renderer/index.html`, loaded by the production Electron main process at `apps/desktop/dist/main/main.js`.
- Synthetic profile: initialized into a fresh temporary directory by `jobos-init`; built-in SQLite jobs, local artifacts, offline agent, disposable file credentials, isolated `HOME`, XDG directories, temporary directory, and Electron `--user-data-dir`.
- Network: a dynamically reserved `127.0.0.1` port. No remote page or private adapter is opened.
- DOCX: `packages/docx-engine/tests/fixtures/(FAKE)-cover-letter.docx`, approved in `tests/public-release/synthetic-fixtures.json`, SHA-256 `e6cbea4e5185250e63f369ce0b1c7491c81547d4f2eb1783d7018a959b1ca04e`. Capture mode checksum-validates and binds a copy below disposable Electron local artifact storage; it never opens a native chooser.
- Raw frames, the profile, credentials, Electron state, logs, and intermediate assets remain below the temporary runtime directory and are deleted on success or failure. Only verified accepted outputs are copied into this directory.

From the repository root on macOS:

```bash
PATH="/opt/homebrew/bin:$PATH" pnpm install --frozen-lockfile
uv sync --all-packages --frozen
PATH="/opt/homebrew/bin:$PATH" pnpm media:capture
PATH="/opt/homebrew/bin:$PATH" pnpm media:verify
```

`media:capture` performs the production build, initializes the disposable profile, reserves the loopback port, launches Electron, drives the validated capture spec, captures renderer-only PNGs, encodes and metadata-strips the GIF, and verifies the complete candidate before individually replacing each accepted asset atomically. It also updates the four matching checksum entries in `tests/public-release/synthetic-fixtures.json`; all other fixture records remain unchanged. Capture-only styling disables animation/carets and hides the auxiliary Recovery panel so wall-clock checkpoint timestamps cannot enter public media; the document canvas and Saved state remain real renderer output. The workflow fails closed on a selector timeout, unexpected dimensions, unsafe output path, fixture checksum mismatch, forbidden visible private/host text, missing tool, non-file credential provider, non-offline agent, Electron error, or artifact verification failure.

## Recorded toolchain and settings

- Node.js `26.5.0` at `/opt/homebrew/bin/node`
- pnpm `10.33.1`
- Python `3.11` as constrained by the workspace
- uv `0.11.28`
- Electron `43.1.1` (repository lockfile/package manifest)
- FFmpeg / FFprobe `8.1.2`
- ExifTool `13.55`
- Static source: Electron `webContents.capturePage()`, exact 1440×1024 content area when the display permits it; larger same-ratio backing captures are normalized to 1440×1024 with Electron's high-quality native-image resize. PNGs have a 3,000,000-byte maximum and then receive `exiftool -all=`.
- GIF: FFmpeg at 12 fps, Lanczos resize to 960×683, `palettegen=max_colors=128:stats_mode=diff`, Bayer palette dithering at scale 3 with rectangle diff mode, infinite loop, no audio, `-map_metadata -1`, and a 20,000,000-byte maximum; then `exiftool -all=`

PNG encoding is Electron-owned and lossless after any documented same-ratio normalization. The GIF is always a resized/compressed derivative. The verifier checks PNG signatures and dimensions, GIF dimensions/frame rate/frame count/duration, SHA-256 values, and absence of EXIF, XMP, IPTC, PNG comments, and GIF comments.

## Synthetic-data and privacy checklist

All visible job, company, role, description, and document content is synthetic and clearly labeled **Demo**, **Fictional**, or **(FAKE)**. It must never be used as a real application.

Automated capture checks enforce:

- [x] disposable profile, file credentials, isolated home/XDG/temp/Electron data, loopback API, and offline agent;
- [x] approved checksum-pinned `(FAKE)` fixture only;
- [x] renderer-only capture at exact dimensions, with no native chooser or desktop chrome;
- [x] no visible absolute user path, `file:` URL, private adapter name, or credential label/value;
- [x] no **Open job**, shell-open, reveal, export, deploy, or publish action;
- [x] metadata stripping plus checksum/dimension/duration/frame-count verification;
- [x] temporary runtime and raw-frame cleanup by default.

Independent review remains required and is intentionally not claimed here:

- [ ] inspect every PNG and every GIF frame for real names, jobs, files, paths, host/user names, raw errors, private URLs/providers, credentials, clocks, notifications, dialogs, desktop chrome, clipping, and misleading capability claims;
- [ ] verify README rendering, accessibility descriptions, links, animation behavior, and static equivalents;
- [ ] approve each asset for public use.

No optional-capability screenshot is included: the core three views show the accepted local workflow without implying that an unconfigured integration is available.
