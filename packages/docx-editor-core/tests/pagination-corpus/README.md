# DOCX pagination baseline

`baseline-lo.json` records page boundaries produced by LibreOffice for the approved `(FAKE)` synthetic DOCX fixtures in `packages/docx-engine/tests/fixtures`.

The parity suite fails closed when the baseline or a fixture is missing, when conversion errors occur, or when primary page-start parity drops below 85 percent. This is a regression signal, not a claim that LibreOffice and Word paginate identically.

Regenerate intentionally on a machine with LibreOffice:

```bash
pnpm docx:pagination-baseline
```

Review the JSON diff before committing it. Do not add real resumes or personal documents to this corpus.