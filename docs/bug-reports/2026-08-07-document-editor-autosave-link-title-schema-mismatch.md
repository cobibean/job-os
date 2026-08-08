# Document editor autosave rejects linked documents

Date: 2026-08-07

Status: root cause confirmed; fix deferred to the planned document-editor rebuild

Severity: high within the current editor — autosave is blocked for any editable document containing a hyperlink

## Recommended disposition

Do not spend a packaging and installed-acceptance cycle repairing the current editor if its rebuild is the next document-editor work.

Carry this bug into the rebuild as a required regression and acceptance case. Fix it before the rebuild only if the current editor must be used for real application documents in the meantime or the rebuild is deferred.

## User-visible behavior

After changing any text in an affected document, JobOS automatically displays:

```text
Error invoking remote method 'jobos:editable-documents:save': Error: Unknown link attribute: title
```

There is no manual Save button because the current editor is designed to autosave 750 milliseconds after a change. The failure is real: the save request is rejected inside the Electron main process before it reaches the JobOS API.

## Scope confirmed in the live workspace

- The affected Cohere resume contains three hyperlinks.
- Its persisted link marks contain `href`, `target`, `rel`, and `class`.
- The document remains at revision 1 after the failed saves.
- The other three current editable documents contain no links and have reached revision 2.
- Any edit triggers the bug because Tiptap serializes the entire document, not only the changed paragraph.

## Root cause

The canonical JobOS validator and Tiptap's real link schema disagree.

1. JobOS uses Tiptap `3.29.2` through `StarterKit`.
2. Tiptap's Link extension defines five attributes: `href`, `target`, `rel`, `class`, and `title`.
3. Tiptap adds `title: null` when it constructs and serializes every link mark.
4. JobOS's TypeScript validator permits only `href`, `target`, `rel`, and `class`.
5. Autosave calls that validator before making the API request, which produces the exact Electron IPC error.

Relevant code:

- `apps/desktop/src/shared/documentExtensions.ts:108-135` — real Tiptap schema includes StarterKit's Link extension.
- `apps/desktop/src/renderer/document-editor/DocumentEditor.tsx:30-32` — every editor update is serialized with `editor.getJSON()`.
- `apps/desktop/src/renderer/document-editor/useDocumentAutosave.ts:104-117` — changes queue an autosave after 750 milliseconds.
- `apps/desktop/src/main/editableDocuments.ts:490-495` — TypeScript validation runs before the API call.
- `apps/desktop/src/shared/editableDocumentSchema.ts:237-243` — link allowlist omits `title`.
- `services/api/jobos_api/editable_documents.py:444-455` — the backend validator independently omits `title` too.

The mismatch was introduced with the initial editor commit `71c3c70` (`feat: add open source document editor`). Tiptap `3.29.2` and the four-attribute JobOS allowlist landed together; this is not a later dependency upgrade or recent UI regression.

## Deterministic reproduction

Passing the real persisted Cohere document through the actual JobOS Tiptap schema produced:

```text
actual_document_editor_link_count=3
actual_document_editor_link_keysets=["class,href,rel,target,title"]
actual_document_editor_title_values=[null]
actual_document_save_validation=Unknown link attribute: title
```

A synthetic canonical document with one valid link produced the same transition:

```text
before_link_keys=href,target,rel,class
after_tiptap_link_keys=href,target,rel,class,title
after_tiptap_title=null
post_tiptap_validation=Unknown link attribute: title
```

## Why existing tests missed it

- Autosave renderer tests mock the Electron save bridge and accept the payload without running the real boundary validator.
- Schema tests construct link JSON manually instead of round-tripping it through Tiptap.
- Import tests validate normalized JSON before the renderer's Tiptap schema adds default attributes.
- There is no contract test that performs a real editor transaction and validates the resulting JSON in both TypeScript and Python.

The focused schema and editor suites still pass 14/14 while this real boundary fails.

## Rebuild requirements

The rebuilt editor must prove all of the following before acceptance:

1. The real editor schema, TypeScript validator, and Python validator agree on every emitted node and mark attribute, including nullable defaults such as link `title`.
2. A linked canonical document can be loaded, edited through a real Tiptap transaction, serialized, validated, saved through Electron IPC and the API, then reconstructed after reload.
3. A representative real DOCX containing email/web links can be opened in the exact installed app, edited away from the links, reach visible **Saved** state, leave the editor, reopen, and preserve the edit.
4. Autosave failures display concise product-language recovery guidance rather than raw `Error invoking remote method` text.
5. The regression uses editor-produced JSON; a hand-built fixture alone is insufficient.

## Investigation boundary

No source fix, document mutation, API restart, package build, or reinstall was performed during root-cause investigation. The installed app was returned to Review, the affected document remained at revision 1, and the repository was clean afterward.
