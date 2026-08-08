import assert from 'node:assert/strict'

const [engine, editor] = await Promise.all([
  import('@jobos/docx-engine'),
  import('@jobos/docx-editor-core')
])

assert.equal(typeof engine.parseDocx, 'function', 'DOCX engine must resolve to compiled runtime JavaScript')
assert.equal(typeof editor.parseDocxForEditing, 'function', 'DOCX editor core must resolve to compiled runtime JavaScript')
