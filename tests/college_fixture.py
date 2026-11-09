"""Entirely synthetic, in-memory college-export fixtures; no real workbook data."""
from io import BytesIO
import re
import zipfile

from openpyxl import Workbook


WEB = "Web技术基础，专业必修课，必修，学分2，学时32，2025-2026-1"
PRACTICE = "工程实践2，集中实践教学环节，必修，学分1.25，学时20，2025-2026-1"
EMPTY = "体育 III，通识必修课，必修，学分0.75，学时24，2025-2026-1"
HEADERS = ["学号", "姓名", "班级", "课程门数", WEB, PRACTICE, EMPTY,
           "不及格门数", "平均分", "所得学分", "平均学分绩点", "排名"]
ROWS = [
    ["FIXTURE240001", "合成甲", "24软工1班", 123, 0, "缺考", None, 42, 99, 99, 9, 1],
    ["FIXTURE240002", "合成乙", "24软工1班", 123, "72.50", "免修", "", 42, 99, 99, 9, 2],
    ["FIXTURE240003", "合成丙", "24软工2班", 123, None, 80, " ", 42, 99, 99, 9, 3],
]


def college_workbook(*, headers=None, rows=None, title_rows=2,
                     extra_sheet=False, merge_data=False, stale_dimensions=False,
                     external=False):
    """Make the advertised wide format without reading any user-owned workbook."""
    headers = list(HEADERS if headers is None else headers)
    rows = [list(row) for row in (ROWS if rows is None else rows)]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "合成成绩单"
    if title_rows:
        sheet.cell(1, 1, "合成学院成绩信息（非真实记录）")
        sheet.merge_cells(start_row=1, start_column=1, end_row=title_rows,
                          end_column=len(headers))
    header_row = title_rows + 1
    for column, value in enumerate(headers, 1):
        sheet.cell(header_row, column, value)
    for row_number, row in enumerate(rows, header_row + 1):
        for column, value in enumerate(row, 1):
            sheet.cell(row_number, column, value)
    if merge_data and rows:
        sheet.merge_cells(start_row=header_row + 1, start_column=1,
                          end_row=header_row + min(2, len(rows)), end_column=1)
    if extra_sheet:
        workbook.create_sheet("第二张表")
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    content = output.getvalue()
    if stale_dimensions or external:
        # Some exports understate dimensions even though physical cells exist.
        output = BytesIO()
        with zipfile.ZipFile(BytesIO(content)) as before, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as after:
            for entry in before.infolist():
                value = before.read(entry)
                if entry.filename == "xl/worksheets/sheet1.xml":
                    if stale_dimensions:
                        value = re.sub(rb'<dimension ref="[^"]+"\s*/>', b'<dimension ref="A1:A1"/>', value)
                if external and entry.filename == "xl/_rels/workbook.xml.rels":
                    value = value.replace(b'</Relationships>', b'<Relationship Id="externalFixture" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/never-fetch" TargetMode="External"/></Relationships>')
                after.writestr(entry.filename, value)
        content = output.getvalue()
    return content
