import { applyStructuredOperations, buildPatchedDocx, parseDocxForEditing, serializeDocumentContext } from '@jobos/docx-editor-core'

import type { DocxWorkerEnvelope, DocxWorkerResponse } from '../../../../shared/docxWorker'

declare global {
  interface Window {
    jobosDocxWorker: {
      subscribe: (handler: (envelope: DocxWorkerEnvelope) => void) => () => void
      respond: (response: DocxWorkerResponse) => void
    }
  }
}

window.jobosDocxWorker.subscribe(envelope => {
  void (async () => {
    try {
      const source = new Uint8Array(envelope.request.bytes)
      const parsed = await parseDocxForEditing(source)
      if (envelope.request.kind === 'inspect') {
        window.jobosDocxWorker.respond({ requestId: envelope.requestId, result: {
          kind: 'inspect', capabilities: parsed.capabilities, context: serializeDocumentContext(parsed.pmDoc)
        } })
        return
      }
      const operationResult = applyStructuredOperations(parsed.pmDoc, envelope.request.operations)
      const bytes = await buildPatchedDocx(parsed, operationResult.document)
      window.jobosDocxWorker.respond({ requestId: envelope.requestId, result: {
        kind: 'apply',
        bytes: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer,
        capabilities: parsed.capabilities,
        context: serializeDocumentContext(operationResult.document)
      } })
    } catch (error) {
      window.jobosDocxWorker.respond({ requestId: envelope.requestId, error: error instanceof Error ? error.message : 'DOCX worker failed' })
    }
  })()
})
