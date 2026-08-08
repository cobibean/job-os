import { createHash } from 'node:crypto'
import JSZip from 'jszip'

export interface PackagePart {
  bytes: Uint8Array
  sha256: string
}

export async function inventoryDocx(bytes: Uint8Array): Promise<Map<string, PackagePart>> {
  const zip = await JSZip.loadAsync(bytes)
  const inventory = new Map<string, PackagePart>()
  for (const [name, entry] of Object.entries(zip.files)) {
    if (entry.dir) continue
    const part = await entry.async('uint8array')
    inventory.set(name, {
      bytes: part,
      sha256: createHash('sha256').update(part).digest('hex'),
    })
  }
  return inventory
}
