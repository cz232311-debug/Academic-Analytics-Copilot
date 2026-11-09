"""Small, explicitly requested example upload; never activates a dataset."""
from io import BytesIO

from openpyxl import Workbook


def example_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "示例成绩单"
    sheet.merge_cells("A1:H2")
    sheet["A1"] = "学业分析工作台 · 合成示例（非真实学生）"
    sheet.append([])
    headers = ["学号", "姓名", "班级", "课程门数",
               "Web技术基础，专业必修课，必修，学分2，学时32，2025-2026-1",
               "高等数学 (I)-1，学科基础必修课，必修，学分3，学时48，2025-2026-1",
               "工程实践2，集中实践教学环节，必修，学分1.25，学时20，2025-2026-1",
               "体育 III，通识必修课，必修，学分0.75，学时24，2025-2026-1"]
    for column, label in enumerate(headers, 1):
        sheet.cell(3, column, label)
    for number, grades in enumerate(((0, 60, 80), (59.99, 72.5, "缺考"), (60, "免修", 90),
                                      (79.99, 83, 85), (80, 70, 95), (100, None, 88)), 1):
        values = [f"EXAMPLE{number:04}", f"示例学生{number}", "24软工1班" if number <= 3 else "24计科1班", 3, *grades, None]
        for column, value in enumerate(values, 1):
            sheet.cell(number + 3, column, value)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()
