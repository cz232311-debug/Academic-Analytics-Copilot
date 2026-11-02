"""Minimal OOXML fixture builder using stdlib ZIP/XML; not a user workbook artifact."""
import io
import zipfile
from xml.sax.saxutils import escape


def workbook_bytes(tables: dict, *, formula=False, external=False):
    output = io.BytesIO()
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    names = list(tables)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(names) + 1))
        archive.writestr("[Content_Types].xml", f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>{overrides}</Types>')
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{rel_ns}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1)) + '</sheets></workbook>')
        relationships = ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1))
        if external:
            relationships += '<Relationship Id="evil" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid" TargetMode="External"/>'
        archive.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{rel_ns}">{relationships}</Relationships>')
        for index, (name, records) in enumerate(tables.items(), 1):
            fields = list(records[0])
            rows = [fields, *[[r[f] for f in fields] for r in records]]
            xml_rows = []
            for row_number, row in enumerate(rows, 1):
                cells = []
                for col, value in enumerate(row, 1):
                    address = f"{chr(64 + col)}{row_number}"
                    if formula and name == "grade_records" and row_number == 2 and fields[col - 1] == "score_cents":
                        cells.append(f'<c r="{address}"><f>SUM(1,2)</f><v>3</v></c>')
                    elif value is None:
                        cells.append(f'<c r="{address}"/>')
                    elif type(value) is int:
                        cells.append(f'<c r="{address}"><v>{value}</v></c>')
                    else:
                        cells.append(f'<c r="{address}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
                xml_rows.append(f'<row r="{row_number}">' + ''.join(cells) + '</row>')
            archive.writestr(f"xl/worksheets/sheet{index}.xml", f'<worksheet xmlns="{ns}"><sheetData>' + ''.join(xml_rows) + '</sheetData></worksheet>')
    return output.getvalue()
