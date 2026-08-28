import JSZip from 'jszip'

const contentTypes = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
const documentXml = '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Attack fixture</w:t></w:r></w:p><w:sectPr/></w:body></w:document>'

export async function minimalDocx(entries: Record<string, string> = {}): Promise<Buffer> {
  const zip = new JSZip()
  zip.file('[Content_Types].xml', contentTypes)
  zip.file('word/document.xml', documentXml)
  zip.file('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
  for (const [name, value] of Object.entries(entries)) zip.file(name, value)
  return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' })
}

export async function entityExpansionDocx(): Promise<Buffer> {
  return minimalDocx({
    'word/document.xml': '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "boom"><!ENTITY b "&a;&a;&a;&a;">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>'
  })
}

/** Advertise an over-budget entry in the central directory; import must reject it before extraction. */
export async function advertisedZipBombDocx(): Promise<Buffer> {
  const bytes = await minimalDocx({ 'word/bomb.bin': 'x' })
  const name = Buffer.from('word/bomb.bin')
  for (let offset = 0; offset <= bytes.length - name.length; offset += 1) {
    if (!bytes.subarray(offset, offset + name.length).equals(name)) continue
    if (offset >= 46 && bytes.readUInt32LE(offset - 46) === 0x02014b50) {
      bytes.writeUInt32LE(61 * 1024 * 1024, offset - 22)
      return bytes
    }
  }
  throw new Error('Could not patch attack fixture central directory')
}

export async function missingDocumentEntryDocx(): Promise<Buffer> {
  const zip = new JSZip()
  zip.file('[Content_Types].xml', contentTypes)
  return zip.generateAsync({ type: 'nodebuffer' })
}

/** Advertise more than 2,000 entries so preflight rejects before any extraction. */
export async function advertisedEntryCountOverflowDocx(): Promise<Buffer> {
  const bytes = await minimalDocx()
  for (let offset = bytes.length - 22; offset >= 0; offset -= 1) {
    if (bytes.readUInt32LE(offset) !== 0x06054b50) continue
    bytes.writeUInt16LE(2_001, offset + 8)
    bytes.writeUInt16LE(2_001, offset + 10)
    return bytes
  }
  throw new Error('Could not patch attack fixture entry count')
}
