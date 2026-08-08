# @jobos/docx-editor-core upstream provenance

- Upstream: https://github.com/genspark-ai/genoffice
- Pinned source commit: `d8305ff2dc152593a1ec5639d77e6860c6a512bd`
- Adapted paths: `apps/docs/src/renderer/editor`, pagination/style/metrics helpers, typed command protocol, `packages/pptx-render/src/preset-geometry.ts`, and the small shape/WordArt display helpers required by protected OOXML rendering.
- License: Apache-2.0; see `LICENSE` and `NOTICE`.
- Deliberately excluded: `ee/`, desktop shell, updater, accounts, provider transport, project store, slides, sheets, and GenOffice assistant branding.

JobOS owns this package boundary and the integration API in `src/document.ts`.
