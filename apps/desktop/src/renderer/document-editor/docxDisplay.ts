import type { DocxBinding } from '../../shared/docxDocuments'

export function displayDocxFilename(binding: DocxBinding): string {
  const appOwned = binding.canonicalPath
    .replaceAll('\\', '/')
    .includes('/editable-docx-artifacts/')
  const prefix = `${binding.bindingId}-`
  if (!appOwned || !binding.filename.startsWith(prefix)) return binding.filename
  const withoutBinding = binding.filename.slice(prefix.length)
  return /^[a-f0-9]{12}-/.test(withoutBinding)
    ? withoutBinding.slice(13)
    : withoutBinding
}
