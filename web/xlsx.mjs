const spreadsheet = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
const officeRelationships = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
const packageRelationships = 'http://schemas.openxmlformats.org/package/2006/relationships'
const contentTypes = 'http://schemas.openxmlformats.org/package/2006/content-types'
const spreadsheetType = 'application/vnd.openxmlformats-officedocument.spreadsheetml'
const xlsxType = spreadsheetType + '.sheet'
const xmlDeclaration = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
const encoder = new TextEncoder()

const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }

function escapeXml(text) {
  return text.replace(/[&<>"']/g, character => entities[character])
}

function cellText(text) {
  // SpreadsheetML uses _xHHHH_ escapes for control characters and literal escape sequences.
  return escapeXml(text.replace(/_x[0-9a-f]{4}_/gi, match => '_x005F_' + match.slice(1))
    .replace(/[\u0000-\u0008\u000b-\u001f\ufffe\uffff]/g, character =>
      '_x' + character.charCodeAt(0).toString(16).padStart(4, '0').toUpperCase() + '_'))
}

function columnName(index) {
  let name = ''
  for (let number = index + 1; number > 0; number = Math.floor((number - 1) / 26)) {
    name = String.fromCharCode(65 + (number - 1) % 26) + name
  }
  return name
}

function cell(value, reference, header) {
  if (value == null || (typeof value === 'number' && !Number.isFinite(value))) return ''
  const start = '<c r="' + reference + '"'
  if (typeof value === 'number') return start + '><v>' + value + '</v></c>'
  if (typeof value === 'boolean') return start + ' t="b"><v>' + Number(value) + '</v></c>'
  if (typeof value !== 'string') throw new Error('Excel cells require a number, text, boolean, or blank.')
  if (value.length > 32767) throw new Error('A cell exceeds the Excel text limit.')
  // Write text as inline strings, including any text that starts with an equals sign.
  return start + (header ? ' s="1"' : '') + ' t="inlineStr"><is><t xml:space="preserve">' +
    cellText(value) + '</t></is></c>'
}

function worksheetXml({ headers, rows, filter = true }) {
  if (!headers.length || headers.length > 16384 || rows.length >= 1048576) {
    throw new Error('The table exceeds Excel worksheet limits or has no columns.')
  }
  const widths = headers.map(header => Math.min(60, Math.max(12, String(header).length + 5)))
  const columns = headers.map((_, index) => columnName(index))
  const sheetData = [headers, ...rows].map((row, index) => {
    if (!Array.isArray(row) || row.length > columns.length) {
      throw new Error('Excel rows must fit their column headers.')
    }
    const number = index + 1
    const cells = row.map((value, column) => {
      if (typeof value === 'string') {
        widths[column] = Math.min(60, Math.max(widths[column], value.length + 2))
      }
      if (typeof value === 'number') widths[column] = Math.max(widths[column], 16)
      return cell(value, columns[column] + number, index === 0)
    })
    return '<row r="' + number + '">' + cells.join('') + '</row>'
  }).join('')
  const bounds = 'A1:' + columns.at(-1) + (rows.length + 1)
  const columnWidths = widths.map((width, index) => {
    const position = index + 1
    return '<col min="' + position + '" max="' + position + '" width="' + width + '" customWidth="1"/>'
  }).join('')
  const pane = '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>' +
    '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
  return '<worksheet xmlns="' + spreadsheet + '"><dimension ref="' + bounds + '"/>' +
    '<sheetViews><sheetView showGridLines="0" workbookViewId="0">' + pane + '</sheetView></sheetViews>' +
    '<cols>' + columnWidths + '</cols><sheetData>' + sheetData + '</sheetData>' +
    (filter && rows.length ? '<autoFilter ref="' + bounds + '"/>' : '') + '</worksheet>'
}

function stylesXml() {
  return '<styleSheet xmlns="' + spreadsheet + '">' +
    '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font>' +
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font></fonts>' +
    '<fills count="3"><fill><patternFill patternType="none"/></fill>' +
    '<fill><patternFill patternType="gray125"/></fill>' +
    '<fill><patternFill patternType="solid"><fgColor rgb="FF294B3C"/><bgColor indexed="64"/></patternFill></fill></fills>' +
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>' +
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' +
    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' +
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>' +
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
}

function relationships(entries) {
  return '<Relationships xmlns="' + packageRelationships + '">' +
    entries.map(([type, target], index) =>
      '<Relationship Id="rId' + (index + 1) + '" Type="' + officeRelationships + '/' + type +
      '" Target="' + target + '"/>').join('') + '</Relationships>'
}

const crcTable = Uint32Array.from({ length: 256 }, (_, byte) => {
  let crc = byte
  for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0)
  return crc
})

function crc32(bytes) {
  let crc = 0xffffffff
  for (const byte of bytes) crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 255]
  return (crc ^ 0xffffffff) >>> 0
}

function zipRecord(length, signature) {
  const record = new DataView(new ArrayBuffer(length))
  record.setUint32(0, signature, true)
  return record
}

// OOXML uses ZIP packaging. Stored entries keep generation synchronous and portable.
function zip(files) {
  const entries = []
  const directory = []
  let offset = 0
  let directorySize = 0
  for (const [path, xml] of files) {
    const name = encoder.encode(path)
    const data = encoder.encode(xmlDeclaration + xml)
    const crc = crc32(data)
    const header = zipRecord(30, 0x04034b50)
    header.setUint16(4, 20, true)
    header.setUint16(12, 33, true) // DOS date 1980-01-01.
    header.setUint32(14, crc, true)
    header.setUint32(18, data.length, true)
    header.setUint32(22, data.length, true)
    header.setUint16(26, name.length, true)
    entries.push(header.buffer, name, data)

    const entry = zipRecord(46, 0x02014b50)
    entry.setUint16(4, 20, true)
    entry.setUint16(6, 20, true)
    entry.setUint16(14, 33, true)
    entry.setUint32(16, crc, true)
    entry.setUint32(20, data.length, true)
    entry.setUint32(24, data.length, true)
    entry.setUint16(28, name.length, true)
    entry.setUint32(42, offset, true)
    directory.push(entry.buffer, name)
    directorySize += entry.byteLength + name.length
    offset += header.byteLength + name.length + data.length
    if (offset + directorySize >= 0xffffffff) throw new Error('The Excel export is too large.')
  }
  const end = zipRecord(22, 0x06054b50)
  end.setUint16(8, files.length, true)
  end.setUint16(10, files.length, true)
  end.setUint32(12, directorySize, true)
  end.setUint32(16, offset, true)
  return new Blob([...entries, ...directory, end.buffer], { type: xlsxType })
}

/**
 * Create a local XLSX export from one or more flat tables.
 *
 * @param {Array<object>} sheets - Worksheets with a name, column headers, and rows.
 * @returns {Blob} An XLSX workbook.
 */
export function createWorkbook(sheets) {
  if (!sheets.length || sheets.length > 65530) throw new Error('Choose a valid number of Excel sheets.')
  const names = new Set()
  const sheetFiles = sheets.map(({ name }, index) => {
    if (typeof name !== 'string' || !name.trim() || name.length > 31 ||
        /[\[\]:*?\/\\\u0000-\u001f]/.test(name) || name.startsWith("'") || name.endsWith("'") ||
        names.has(name.toLowerCase())) throw new Error('Excel sheet names must be valid and unique.')
    names.add(name.toLowerCase())
    return 'worksheets/sheet' + (index + 1) + '.xml'
  })
  const overrides = [
    ['xl/workbook.xml', spreadsheetType + '.sheet.main+xml'],
    ['xl/styles.xml', spreadsheetType + '.styles+xml'],
    ...sheetFiles.map(path => ['xl/' + path, spreadsheetType + '.worksheet+xml']),
  ]
  const sheetElements = sheets.map(({ name }, index) => {
    const id = index + 1
    return '<sheet name="' + escapeXml(name) + '" sheetId="' + id + '" r:id="rId' + id + '"/>'
  }).join('')
  return zip([
    ['[Content_Types].xml', '<Types xmlns="' + contentTypes + '">' +
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
      '<Default Extension="xml" ContentType="application/xml"/>' +
      overrides.map(([path, type]) => '<Override PartName="/' + path + '" ContentType="' + type + '"/>').join('') +
      '</Types>'],
    ['_rels/.rels', relationships([['officeDocument', 'xl/workbook.xml']])],
    ['xl/workbook.xml', '<workbook xmlns="' + spreadsheet + '" xmlns:r="' + officeRelationships + '">' +
      '<bookViews><workbookView activeTab="0"/></bookViews><sheets>' + sheetElements + '</sheets></workbook>'],
    ['xl/_rels/workbook.xml.rels', relationships([...sheetFiles.map(path => ['worksheet', path]), ['styles', 'styles.xml']])],
    ['xl/styles.xml', stylesXml()],
    ...sheets.map((sheet, index) => ['xl/' + sheetFiles[index], worksheetXml(sheet)]),
  ])
}
