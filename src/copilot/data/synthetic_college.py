"""Reproducible fictional college workbooks for the existing import workflow.

This module only generates a single-sheet XLSX in memory. It never reads user
files, opens a database, calls analytics or invokes a model/provider.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
import random
import re
from typing import Annotated, Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pydantic import Field, model_validator

from copilot.models import Record

SyntheticMajor = Literal["software_engineering", "computer_science", "network_engineering"]
MAJOR_LABELS = {"software_engineering": "软件工程", "computer_science": "计算机科学与技术", "network_engineering": "网络工程"}
_MAJORS = {"software_engineering": ("软工", "SE"), "computer_science": ("计科", "CS"), "network_engineering": ("网工", "NE")}
PROFILE_LABELS = {"balanced": "均衡分布", "strong": "整体较好", "needs_support": "待加强"}
SCENARIO_LABELS = {"normal": "常规成绩", "edge_cases": "空值与临界成绩", "conflict": "重复学号冲突"}
_SURNAMES = tuple("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕张孔曹严华金魏陶姜谢邹苏潘葛范彭鲁韦马")
_GIVEN_FIRST = tuple("明晓思宇子雨文景嘉若承亦安泽梓清书晨知云")
_GIVEN_LAST = tuple("远宁涵辰轩怡悦然睿琪彤瑶航哲佳萱霖瑞昕溪")
_FIXED_TIME = datetime(2000, 1, 1)


@dataclass(frozen=True)
class _Course:
    name: str
    category: str
    nature: str
    credits: str
    hours: int
    difficulty: int


_COURSES = (
    _Course("高等数学 (I)-1", "学科基础必修课", "必修", "4", 64, -4),
    _Course("Web技术基础", "专业必修课", "必修", "2.5", 40, -1),
    _Course("工程实践2", "集中实践教学环节", "必修", "1.25", 20, 2),
    _Course("体育 III", "通识必修课", "必修", "0.75", 24, 10),
    _Course("程序设计基础 Python", "专业必修课", "必修", "3", 48, -3),
    _Course("大学英语 (II)", "通识必修课", "必修", "3", 48, -2),
    _Course("数据结构与算法", "专业必修课", "必修", "3.5", 56, -7),
    _Course("数据库原理", "专业必修课", "必修", "3", 48, -3),
    _Course("计算机网络", "专业必修课", "必修", "3", 48, -4),
    _Course("软件工程导论", "专业必修课", "必修", "2", 32, 2),
    _Course("思想道德与法治", "通识必修课", "必修", "2.5", 40, 5),
    _Course("创新创业实践", "通识选修课", "选修", "1", 16, 6),
)
_EMPTY_COURSE = _Course("合成全空课程（验证用）", "专业选修课", "选修", "1", 16, 0)


class SyntheticOptions(Record):
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20261111
    cohort: Annotated[int, Field(ge=1900, le=2199)] = 2024
    academic_year_start: Annotated[int, Field(ge=2000, le=2099)] = 2025
    semester: Annotated[int, Field(ge=1, le=2)] = 1
    majors: Annotated[tuple[SyntheticMajor, ...], Field(min_length=1, max_length=3)] = ("software_engineering",)
    classes_per_major: Annotated[int, Field(ge=1, le=4)] = 2
    students_per_class: Annotated[int, Field(ge=1, le=50)] = 30
    course_count: Annotated[int, Field(ge=3, le=12)] = 8
    score_profile: Literal["balanced", "strong", "needs_support"] = "balanced"
    scenario: Literal["normal", "edge_cases", "conflict"] = "normal"

    @model_validator(mode="after")
    def coherent_configuration(self):
        if len(set(self.majors)) != len(self.majors):
            raise ValueError("专业不能重复，请保留每个专业一次。")
        if self.academic_year_start < self.cohort:
            raise ValueError("统计学年的起始年不能早于入学年级。")
        students = len(self.majors) * self.classes_per_major * self.students_per_class
        if self.scenario == "edge_cases" and (students < 2 or students * self.course_count < 10):
            raise ValueError("空值与临界成绩场景至少需要2名学生和10个学生×课程单元格，请增加学生或课程。")
        return self


@dataclass(frozen=True)
class SyntheticWorkbook:
    content: bytes
    summary: dict
    filename: str = "合成学院成绩单.xlsx"


def _header(course: _Course, options: SyntheticOptions) -> str:
    return (f"{course.name}，{course.category}，{course.nature}，学分{course.credits}，学时{course.hours}，"
            f"{options.academic_year_start}-{options.academic_year_start + 1}-{options.semester}")


def _stable_xlsx(workbook: Workbook) -> bytes:
    """Normalize package and core-property clocks for byte reproducibility."""
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    output = BytesIO()
    with ZipFile(buffer) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            content = source.read(name)
            if name == "docProps/core.xml":
                # openpyxl updates modified during save. Only normalize the two
                # generated timestamps; do not change worksheet or score XML.
                content = re.sub(rb"(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:(?:created|modified)>)",
                                 rb"\g<1>2000-01-01T00:00:00Z\g<2>", content)
            entry = ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.create_system = 0
            target.writestr(entry, content)
    return output.getvalue()


def generate_college_workbook(options: SyntheticOptions) -> SyntheticWorkbook:
    """Generate fictional identities, curriculum metadata and score scenarios.

    Count/score expectations are accumulated from this source matrix, not from
    the production parser or statistical engine, allowing independent checks.
    """
    if not isinstance(options, SyntheticOptions):
        raise TypeError("options must be validated SyntheticOptions")
    rng = random.Random(options.seed)
    courses = _COURSES[:options.course_count]
    students, names, class_labels = [], set(), []
    matrix: list[list[int | str | None]] = []
    base_mean = {"balanced": 74, "strong": 87, "needs_support": 57}[options.score_profile]
    for major_index, major in enumerate(options.majors):
        short, code = _MAJORS[major]
        for class_number in range(1, options.classes_per_major + 1):
            class_label = f"{options.cohort}{short}{class_number}班"
            class_labels.append(class_label)
            class_shift = rng.uniform(-3, 3) + major_index * .8
            for position in range(1, options.students_per_class + 1):
                while True:
                    name = "合成·" + rng.choice(_SURNAMES) + rng.choice(_GIVEN_FIRST) + rng.choice(_GIVEN_LAST)
                    if name not in names:
                        names.add(name)
                        break
                students.append((f"SIM{options.cohort}{code}{class_number:02}{position:03}", name, class_label))
                ability = rng.gauss(0, 7)
                scores = []
                for course in courses:
                    number = base_mean + class_shift + ability + course.difficulty + rng.gauss(0, 7)
                    value = min(Decimal("100"), max(Decimal("0"), Decimal(str(number)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    scores.append(int(value * 100))
                matrix.append(scores)
    if options.scenario == "edge_cases":
        # At least two rows and ten cells are validated above. The first six
        # cells remain numeric so every active course has usable scores.
        for index, value in enumerate((0, 5999, 6000, 7999, 8000, 10000, None, "缺考", "免修", "待核验")):
            row, column = divmod(index, options.course_count)
            matrix[row][column] = value
    extra_empty = int(options.scenario == "edge_cases")
    counts = {"students": len(students), "student_rows": len(students), "classes": len(class_labels),
              "courses": len(courses), "course_columns": len(courses) + extra_empty,
              "graded": 0, "missing": 0, "exempt": 0, "blank": len(students) * extra_empty, "unknown": 0,
              "conflicting_rows": int(options.scenario == "conflict"), "excluded_empty_courses": extra_empty}
    score_sum = 0
    for scores in matrix:
        for value in scores:
            if type(value) is int:
                counts["graded"] += 1
                score_sum += value
            else:
                key = {None: "blank", "缺考": "missing", "免修": "exempt"}.get(value, "unknown")
                counts[key] += 1
    counts["enrollments"] = counts["grade_records"] = counts["graded"] + counts["missing"] + counts["exempt"]
    counts["student_rows"] += counts["conflicting_rows"]
    workbook = Workbook()
    workbook.properties.creator = "Academic Analytics Copilot - Synthetic"
    workbook.properties.lastModifiedBy = "Academic Analytics Copilot - Synthetic"
    workbook.properties.created = workbook.properties.modified = _FIXED_TIME
    sheet = workbook.active
    sheet.title = "合成学院成绩单"
    columns = ["学号", "姓名", "班级", *(_header(course, options) for course in courses)]
    if extra_empty:
        columns.append(_header(_EMPTY_COURSE, options))
    columns.extend(("课程门数", "不及格门数", "平均分"))
    sheet.merge_cells(start_row=1, start_column=1, end_row=2, end_column=len(columns))
    sheet["A1"] = (f"自动生成的合成学院成绩单（非真实学生） · 种子{options.seed}\n"
                   "仅供本地验证；末尾汇总为合成展示，系统导入后独立重算。")
    sheet["A1"].font = Font(size=15, bold=True)
    sheet["A1"].alignment = Alignment(wrap_text=True, vertical="center")
    for index, label in enumerate(columns, 1):
        cell = sheet.cell(3, index, label)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="175CD3")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet.column_dimensions[get_column_letter(index)].width = 24 if index <= 3 else 20
    sheet.row_dimensions[3].height = 95
    sheet.freeze_panes = "D4"
    sheet.auto_filter.ref = f"A3:{get_column_letter(len(columns))}{3 + counts['student_rows']}"
    source_rows = []
    for identity, scores in zip(students, matrix):
        numeric = [value for value in scores if type(value) is int]
        average = (Decimal(sum(numeric)) / len(numeric) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if numeric else None
        values = [*identity, *(float(Decimal(value) / 100) if type(value) is int else value for value in scores)]
        if extra_empty:
            values.append(None)
        values.extend((sum(value is not None for value in scores), sum(value < 6000 for value in numeric), float(average) if average is not None else None))
        source_rows.append(values)
    if options.scenario == "conflict":
        duplicate = list(source_rows[0])
        duplicate[2] = class_labels[1] if len(class_labels) > 1 else f"{options.cohort}{_MAJORS[options.majors[0]][0]}99班"
        source_rows.append(duplicate)
    for row_number, values in enumerate(source_rows, 4):
        for column, value in enumerate(values, 1):
            cell = sheet.cell(row_number, column, value)
            if type(value) in {int, float} and 4 <= column < 4 + options.course_count:
                cell.number_format = "0.00"
    summary = {"synthetic": True, "generator_version": "1", "seed": options.seed, "scenario": options.scenario,
               "score_profile": options.score_profile, "cohort": options.cohort,
               "academic_year": f"{options.academic_year_start}-{options.academic_year_start + 1}",
               "term": f"{options.academic_year_start}-T{options.semester}", "majors": list(options.majors),
               "class_labels": class_labels, "course_names": [course.name for course in courses],
               "counts": counts, "sum_score_cents": score_sum,
               "score_generation": "纯合成启发式：基础水平、专业/班级差异、学生能力和课程难度叠加随机波动；不代表真实课程培养方案或教学规律。",
               "counts_scope": "唯一主学生矩阵；student_rows另计冲突重复行，blank包含新增全空课程列；冲突行不重复累计成绩。"}
    return SyntheticWorkbook(content=_stable_xlsx(workbook), summary=summary)
