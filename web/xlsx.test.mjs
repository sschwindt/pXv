import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createWorkbook } from './xlsx.mjs'

test('XLSX records typed velocities, literal text, and an empty audit sheet', async () => {
  const file = createWorkbook([
    { name: 'Vectors', headers: ['Speed (m/s)', 'Label', 'Accepted', 'Missing'],
      rows: [[1.48, '=A2 & <field>', true, null], [0, ' Isar ', false, NaN]] },
    { name: 'Excluded', headers: ['Reason'], rows: [] },
  ])
  assert.equal(file.type, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

  const bytes = Buffer.from(await file.arrayBuffer())
  const parts = new Map()
  let offset = 0
  while (bytes.readUInt32LE(offset) === 0x04034b50) {
    const size = bytes.readUInt32LE(offset + 18)
    const nameLength = bytes.readUInt16LE(offset + 26)
    const name = bytes.toString('utf8', offset + 30, offset + 30 + nameLength)
    const start = offset + 30 + nameLength + bytes.readUInt16LE(offset + 28)
    parts.set(name, bytes.toString('utf8', start, start + size))
    offset = start + size
  }
  const end = bytes.length - 22
  assert.equal(bytes.readUInt32LE(end), 0x06054b50)
  assert.equal(bytes.readUInt32LE(end + 16), offset)
  assert.equal(bytes.readUInt16LE(end + 10), parts.size)
  assert.match(parts.get('xl/workbook.xml'), /name="Excluded"/)
  const vectors = parts.get('xl/worksheets/sheet1.xml')
  assert.match(vectors, /<c r="A2"><v>1\.48<\/v>/)
  assert.match(vectors, /<c r="A3"><v>0<\/v>/)
  assert.match(vectors, /<c r="B2" t="inlineStr"><is><t xml:space="preserve">=A2 &amp; &lt;field&gt;/)
  assert.match(vectors, /<c r="C2" t="b"><v>1<\/v>/)
  assert.doesNotMatch(vectors, /r="D[23]"/)
  assert.match(vectors, /<autoFilter ref="A1:D3"/)
  assert.doesNotMatch(parts.get('xl/worksheets/sheet2.xml'), /autoFilter/)
})
