import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import JSZip from 'jszip'
import {
  Document, Footer, Header, HeadingLevel, Packer, PageBreak, Paragraph,
  Table, TableCell, TableRow, TextRun, WidthType
} from 'docx'

const directory = path.dirname(fileURLToPath(import.meta.url))
const fixedDate = new Date('2026-01-01T00:00:00.000Z')
const contact = () => new Paragraph({ children: [new TextRun({ text: 'Alex Morgan · alex@example.com · (555) 010-2000', bold: true })] })
const heading = text => new Paragraph({ text, heading: HeadingLevel.HEADING_1 })
const bullet = text => new Paragraph({ text, bullet: { level: 0 } })
async function deterministicZip(zip) {
  const core = zip.file('docProps/core.xml')
  if (core) {
    const xml = (await core.async('string')).replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z/g, fixedDate.toISOString())
    zip.file('docProps/core.xml', xml, { date: fixedDate })
  }
  for (const entry of Object.values(zip.files)) entry.date = fixedDate
  return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE', compressionOptions: { level: 9 }, platform: 'DOS' })
}
const write = async (name, document) => {
  const zip = await JSZip.loadAsync(await Packer.toBuffer(document))
  await fs.writeFile(path.join(directory, name), await deterministicZip(zip))
}
const doc = (children, options = {}) => new Document({ sections: [{ ...options, children }] })

await write('one-page-resume.docx', doc([
  contact(), heading('Summary'), new Paragraph('Product engineer focused on reliable local software.'),
  heading('Experience'), new Paragraph({ children: [new TextRun({ text: 'Senior Engineer — Acme', bold: true })] }),
  bullet('Shipped secure document workflows.'), heading('Education'), new Paragraph('B.S. Computer Science'),
  heading('Skills'), new Paragraph('TypeScript, Electron, Python')
]))

await write('two-page-resume-table.docx', doc([
  contact(), heading('Experience'), bullet('Led a cross-functional platform team.'), bullet('Reduced processing time by 40%.'),
  new Table({ width: { size: 9000, type: WidthType.DXA }, rows: [
    new TableRow({ children: [new TableCell({ children: [new Paragraph('Skill')] }), new TableCell({ children: [new Paragraph('Level')] })] }),
    new TableRow({ children: [new TableCell({ children: [new Paragraph('TypeScript')] }), new TableCell({ children: [new Paragraph('Expert')] })] })
  ] }),
  new Paragraph({ children: [new PageBreak()] }), heading('Education'), new Paragraph('M.S. Human-Computer Interaction'),
  heading('Skills'), bullet('Accessible systems'), bullet('Security review')
]))

await write('cover-letter-header-footer.docx', doc([
  contact(), heading('Body'), new Paragraph('Dear Hiring Manager,'), new Paragraph('I am excited to apply for the product engineering role.'),
  new Paragraph('My experience building reliable desktop software matches your needs.'), heading('Closing'), new Paragraph('Sincerely,'), new Paragraph('Alex Morgan')
], {
  headers: { default: new Header({ children: [new Paragraph('Alex Morgan — Cover Letter')] }) },
  footers: { default: new Footer({ children: [new Paragraph('Confidential application')] }) },
  properties: { page: { margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 }, size: { width: 12240, height: 15840 } } }
}))

await write('references-sheet.docx', doc([
  contact(), heading('References'), new Paragraph({ children: [new TextRun({ text: 'Jordan Lee', bold: true })] }),
  new Paragraph('VP Engineering, Acme · jordan@example.com'), new Paragraph({ children: [new TextRun({ text: 'Taylor Kim', bold: true })] }),
  new Paragraph('Director of Product, Example Co. · taylor@example.com')
]))

async function mutate(source, target, transform) {
  const bytes = await fs.readFile(path.join(directory, source))
  const zip = await JSZip.loadAsync(bytes)
  await transform(zip)
  await fs.writeFile(path.join(directory, target), await deterministicZip(zip))
}

await mutate('one-page-resume.docx', 'tracked-changes.docx', async zip => {
  let xml = await zip.file('word/document.xml').async('string')
  xml = xml.replace(/<w:r><w:t(?: xml:space="preserve")?>Product engineer focused on reliable local software\.<\/w:t><\/w:r>/, '<w:ins w:id="1" w:author="Fixture"><w:r><w:t>Product engineer focused on reliable local software.</w:t></w:r></w:ins><w:del w:id="2" w:author="Fixture"><w:r><w:delText>Old summary.</w:delText></w:r></w:del>')
  zip.file('word/document.xml', xml)
})

await mutate('one-page-resume.docx', 'unsupported-objects.docx', async zip => {
  let xml = await zip.file('word/document.xml').async('string')
  xml = xml.replace('</w:body>', '<w:p><w:r><w:object><o:OLEObject xmlns:o="urn:schemas-microsoft-com:office:office" ProgID="Excel.Sheet.12"/></w:object><w:t>Embedded spreadsheet preview</w:t></w:r></w:p></w:body>')
  zip.file('word/document.xml', xml)
  zip.file('word/embeddings/fixture.bin', Buffer.from('not-an-ole-file'))
})

await mutate('one-page-resume.docx', 'unsafe-content.docx', async zip => {
  let documentXml = await zip.file('word/document.xml').async('string')
  documentXml = documentXml.replace(
    /<w:r><w:t(?: xml:space="preserve")?>Product engineer focused on reliable local software\.<\/w:t><\/w:r>/,
    '<w:hyperlink r:id="rIdUnsafe"><w:r><w:t>Unsafe link</w:t></w:r></w:hyperlink><w:r><w:t> Safe visible text</w:t></w:r>'
  )
  zip.file('word/document.xml', documentXml)
  const relationshipsPath = 'word/_rels/document.xml.rels'
  let relationships = await zip.file(relationshipsPath).async('string')
  relationships = relationships.replace('</Relationships>', '<Relationship Id="rIdUnsafe" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="javascript:alert(1)" TargetMode="External"/></Relationships>')
  zip.file(relationshipsPath, relationships)
})
