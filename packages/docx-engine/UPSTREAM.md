# @jobos/docx-engine upstream provenance

- Upstream: https://github.com/genspark-ai/genoffice
- Pinned source commit: `d8305ff2dc152593a1ec5639d77e6860c6a512bd`
- Imported paths: `packages/docx-engine/src`, `packages/docx-engine/tests`, `packages/docx-engine/scripts`
- Imported license material: root `LICENSE` and `NOTICE`
- Omitted: `ee/`, Electron shell, accounts, updater, Genspark AI/provider transport, search, spreadsheets, slides, PDF app, branding, bundled fonts, generated output, `node_modules`, and npm lockfiles
- JobOS modifications: package rename, exact dependency pins, source-boundary assertion, `(FAKE)` JobOS fixtures/tests, and future compatibility changes recorded in Git
- Empirical benchmark: GenOffice macOS ARM64 `v0.5.83` preserved the tested DOCX layout in Apple Pages after a paragraph edit; that release binary is a benchmark, not a runtime dependency

This package is a modified Apache-2.0 work. Preserve upstream copyright headers and this provenance file when redistributing it.

Every tracked source, test, and script file in this modified package carries a
prominent JobOS change notice in its opening lines. That notice identifies the
file as part of JobOS's modified GenOffice-derived package and points back to
this provenance record.
