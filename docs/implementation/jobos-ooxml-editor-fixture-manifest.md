# JobOS OOXML Editor Fixture Manifest

All committed DOCX fixtures are disposable, visibly marked `(FAKE)`, and generated from fictional names, addresses, companies, and contact details. No fixture originates from a user's real documents.

| Fixture | Source | Expected shape | Fidelity focus | External open allowed |
|---|---|---|---|---|
| `(FAKE)-polished-resume.docx` | Generated `two-page-resume-table.docx`, relabeled in OOXML | Two pages; table; manual page break | fonts, colors, alignment, spacing, margins, lists, table and pagination | Yes |
| `(FAKE)-cover-letter.docx` | Generated `cover-letter-header-footer.docx`, relabeled in OOXML | Letter with header/footer | paragraph spacing, indentation, header/footer retention | Yes |
| `(FAKE)-references.docx` | Generated `references-sheet.docx`, relabeled in OOXML | One-page references sheet | heading and contact block fidelity | Yes |
| `(FAKE)-protected-constructs.docx` | Generated `unsupported-objects.docx`, relabeled in OOXML | Unsupported embedded-object placeholder | capability scan and protected/read-only behavior | Yes |

Routine acceptance uses these fixtures. Real user documents may be used only for separately approved private fidelity testing and are never copied into Git or public evidence.
