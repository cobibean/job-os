# JobOS OOXML Editor Fixture Manifest

All committed DOCX fixtures are disposable and visibly marked `(FAKE)`. They contain generated names, addresses, companies, and contact details only. None came from Cobi's real documents.

| Fixture | Source | Expected shape | Fidelity focus | External open allowed |
|---|---|---|---|---|
| `(FAKE)-polished-resume.docx` | Existing generated `two-page-resume-table.docx`, relabeled in OOXML | Two pages; table; manual page break | fonts, colors, alignment, spacing, margins, lists, table and pagination | Yes |
| `(FAKE)-cover-letter.docx` | Existing generated `cover-letter-header-footer.docx`, relabeled in OOXML | Letter with header/footer | paragraph spacing, indentation, header/footer retention | Yes |
| `(FAKE)-references.docx` | Existing generated `references-sheet.docx`, relabeled in OOXML | One-page references sheet | heading and contact block fidelity | Yes |
| `(FAKE)-protected-constructs.docx` | Existing generated `unsupported-objects.docx`, relabeled in OOXML | Unsupported embedded-object placeholder | capability scan and protected/read-only behavior | Yes |

Routine acceptance must use these fixtures. A real Cobi document may be used only for the separately approved final fidelity check and is never copied into Git.
