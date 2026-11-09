"""Display-only projections of one backend report, never statistical calculations."""
from collections import Counter, defaultdict
import csv
from io import StringIO

import altair as alt
import pandas as pd

from copilot.schemas import CourseReport

TYPE_LABELS = {"general_required": "General required", "professional_required": "Professional required",
               "general_elective": "General elective",
               "professional_elective": "Professional elective", "practical": "Practical teaching",
               "unspecified": "Not provided"}
MAJOR_LABELS = {"computer_science": "Computer Science", "business_administration": "Business Administration",
                "software_engineering": "软件工程", "network_engineering": "网络工程",
                "information_systems": "Information Systems", "unspecified": "Not provided"}
STAFF_TYPE_LABELS = {"general_required": "通识必修", "general_elective": "通识选修", "professional_required": "专业/学科必修",
                     "professional_elective": "专业选修", "practical": "实践教学", "unspecified": "未提供"}
STAFF_MAJOR_LABELS = {"computer_science": "计算机科学与技术", "software_engineering": "软件工程", "network_engineering": "网络工程",
                      "business_administration": "工商管理", "information_systems": "信息系统", "unspecified": "未提供"}
BAND_LABELS = {"[0,60)": "0–<60", "[60,70)": "60–<70", "[70,80)": "70–<80",
               "[80,90)": "80–<90", "[90,100]": "90–100"}
PASS_COLOR, FAIL_COLOR = "#16826C", "#D67632"


def decimal_text(value, *, percent=False):
    return "—" if value is None else f"{value:.2f}" + ("%" if percent else "")


def term_text(term):
    year, semester = term.split("-T")
    return f"{year}–{int(year)+1}学年 · 第{semester}学期"


def course_option_labels(courses):
    """Distinguish catalog options without changing their IDs or merging courses."""
    groups = defaultdict(list)
    for course in courses.values():
        groups[(course.course_name, course.grade_level)].append(course)
    labels = {}
    for group in groups.values():
        ordered = sorted(group, key=lambda course: course.course_id)
        candidates = {}
        for course in ordered:
            label = f"{course.course_name} · {course.grade_level}级"
            if len(ordered) > 1:
                details = [course.course_category or STAFF_TYPE_LABELS[course.course_type], f"{course.credits}学分"]
                if course.hours is not None:
                    details.append(f"{course.hours}学时")
                if course.source_term:
                    details.append(term_text(course.source_term))
                if course.study_nature:
                    details.append(course.study_nature)
                label += " · " + " / ".join(details)
            candidates[course.course_id] = label
        frequencies, positions = Counter(candidates.values()), Counter()
        for course in ordered:
            label = candidates[course.course_id]
            if frequencies[label] > 1:
                positions[label] += 1
                label += f" · 目录项{positions[label]}"
            labels[course.course_id] = label
    return labels


def staff_scope_text(report):
    scope = report.scope
    course = next((row.course_name for row in report.rows if row.course_id == scope.course_id), "所选课程")
    return " · ".join([term_text(scope.term), f"{scope.grade_level}级" if scope.grade_level else "全部年级",
                       STAFF_MAJOR_LABELS.get(scope.major, scope.major) if scope.major else "全部专业",
                       "已选班级" if scope.class_id else "全部班级",
                       STAFF_TYPE_LABELS.get(scope.course_type, scope.course_type) if scope.course_type else "全部课程类型",
                       course if scope.course_id else "全部课程"])


def scope_text(report: CourseReport):
    scope = report.scope
    grade_label = f"{scope.grade_level}级" if scope.grade_level and scope.grade_level >= 1900 else f"Grade {scope.grade_level}" if scope.grade_level else "All grades"
    return " · ".join([scope.term, grade_label,
                       MAJOR_LABELS.get(scope.major, "All majors") if scope.major else "All majors",
                       scope.class_id or "All classes", TYPE_LABELS.get(scope.course_type, scope.course_type) if scope.course_type else "All course types",
                       scope.course_id or "All courses"])


def course_table(report: CourseReport) -> pd.DataFrame:
    rows = []
    for row in report.rows:
        stats = row.statistics
        rows.append({"Course ID": row.course_id, "Course": row.course_name, "Course type": TYPE_LABELS[row.course_type],
                     **{BAND_LABELS[band.label]: band.count for band in stats.distribution},
                     "Average": decimal_text(stats.average_score), "Pass rate": decimal_text(stats.pass_rate_percent, percent=True),
                     "Fail rate": decimal_text(row.fail_rate_percent, percent=True),
                     "Excellent ≥80": decimal_text(row.excellent_rate_percent, percent=True),
                     "Graded n": stats.graded_count, "Passed n": stats.passed_count, "Failed n": stats.failed_count,
                     "Missing n": stats.missing_count, "Exempt n": stats.exempt_count, "Enrolled n": stats.enrollment_count})
    return pd.DataFrame(rows)


def rates_table(report: CourseReport) -> pd.DataFrame:
    rows = []
    for row in report.rows:
        stats = row.statistics
        for series, value, count in (("Pass rate", stats.pass_rate_percent, stats.passed_count),
                                     ("Fail rate", row.fail_rate_percent, stats.failed_count)):
            rows.append({"Course ID": row.course_id, "Course": f"{row.course_id} · {row.course_name}", "Series": series,
                         "Rate": None if value is None else float(value), "Rate label": decimal_text(value, percent=True),
                         "Records": count, "Graded n": stats.graded_count})
    return pd.DataFrame(rows, columns=["Course ID", "Course", "Series", "Rate", "Rate label", "Records", "Graded n"])


def rates_chart(report: CourseReport, *, course_labels=None, staff_labels=False):
    data = rates_table(report)
    if course_labels is not None:
        data["Course"] = data["Course ID"].map(course_labels)
    series = ["及格率", "不及格率"] if staff_labels else ["Pass rate", "Fail rate"]
    if staff_labels:
        data["Series"] = data["Series"].replace(dict(zip(["Pass rate", "Fail rate"], series)))
    base = alt.Chart(data).encode(
        y=alt.Y("Course:N", sort=None, title=None, axis=alt.Axis(labelLimit=350)),
        yOffset=alt.YOffset("Series:N", sort=series),
        x=alt.X("Rate:Q", title="有效成绩占比（%）" if staff_labels else "Percent of graded records", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("Series:N", title=None, scale=alt.Scale(domain=series, range=["#116855", "#A4431E"] if staff_labels else [PASS_COLOR, FAIL_COLOR]),
                        legend=alt.Legend(orient="top", direction="horizontal") if staff_labels else alt.Undefined),
        tooltip=[alt.Tooltip("Course:N", title="课程" if staff_labels else "Course"),
                 alt.Tooltip("Series:N", title="指标" if staff_labels else "Series"),
                 alt.Tooltip("Rate label:N", title="比率" if staff_labels else "Rate label"),
                 alt.Tooltip("Records:Q", title="人数" if staff_labels else "Records"),
                 alt.Tooltip("Graded n:Q", title="有效样本量" if staff_labels else "Graded n")])
    bars = base.mark_bar()
    labels = base.mark_text(align="left", dx=3, color="#283D47").encode(text="Rate label:N")
    chart = bars + labels
    if staff_labels:
        outside = base.transform_filter(alt.datum.Rate < 95).mark_text(align="left", dx=3, fontSize=14).encode(text="Rate label:N", color=alt.value("#182230"))
        inside = base.transform_filter(alt.datum.Rate >= 95).mark_text(align="right", dx=-4, fontSize=14, fontWeight="bold").encode(text="Rate label:N", color=alt.value("white"))
        chart = bars + outside + inside
    height = max(320, 140 + 46 * len(report.rows)) if staff_labels else max(180, 46 * len(report.rows))
    chart = chart.properties(height=height,
                             title="课程及格与不及格率 · 仅有效成绩" if staff_labels else "Course pass and fail rates — graded records only")
    if staff_labels:
        # A neutral plot surface keeps exported/chart text readable in either
        # app theme, including during native theme switches without a rerun.
        chart = chart.properties(background="#FFFFFF").configure_axis(
            labelFontSize=14, titleFontSize=15, labelColor="#182230", titleColor="#182230").configure_legend(
            labelFontSize=15, labelColor="#182230").configure_title(fontSize=17, color="#182230").configure_view(stroke=None)
    return chart


def mean_bands_rows(report: CourseReport, course_labels=None) -> pd.DataFrame:
    """Display backend-assigned mean bands one course per row, without rebinning."""
    lookup = {row.course_id: row for row in report.rows}
    rows = []
    for band in report.mean_bands:
        for course_id in band.course_ids:
            row = lookup[course_id]
            rows.append({"课程均分区间": BAND_LABELS[band.label],
                         "课程": course_labels.get(course_id, row.course_name) if course_labels else row.course_name,
                         "课程类别": STAFF_TYPE_LABELS[row.course_type], "平均分": decimal_text(row.statistics.average_score),
                         "有效成绩数": row.statistics.graded_count})
    for row in report.rows:
        if row.average_band is None:
            rows.append({"课程均分区间": "暂无有效成绩", "课程": course_labels.get(row.course_id, row.course_name) if course_labels else row.course_name,
                         "课程类别": STAFF_TYPE_LABELS[row.course_type], "平均分": "—", "有效成绩数": row.statistics.graded_count})
    return pd.DataFrame(rows, columns=["课程均分区间", "课程", "课程类别", "平均分", "有效成绩数"])


def mean_bands_table(report: CourseReport, *, include_ids=True, course_labels=None) -> pd.DataFrame:
    lookup = {row.course_id: row for row in report.rows}
    rows = []
    for band in report.mean_bands:
        by_type = {}
        for course_id in band.course_ids:
            row = lookup[course_id]
            name = course_labels.get(course_id, row.course_name) if course_labels else row.course_name
            identifier = f"{row.course_id}, " if include_ids else ""
            by_type.setdefault(row.course_type, []).append(f"{name} ({identifier}mean {decimal_text(row.statistics.average_score)}, n={row.statistics.graded_count})")
        rows.append({"Course mean band": BAND_LABELS[band.label],
                     **{label: "\n".join(by_type.get(code, [])) or "—" for code, label in TYPE_LABELS.items()}})
    ungraded = [row for row in report.rows if row.average_band is None]
    if ungraded:
        rows.append({"Course mean band": "No graded records", **{label: "\n".join(
            f"{row.course_name} ({row.course_id})" if include_ids else (course_labels.get(row.course_id, row.course_name) if course_labels else row.course_name)
            for row in ungraded if row.course_type == code) or "—" for code, label in TYPE_LABELS.items()}})
    return pd.DataFrame(rows)


def report_csv(report: CourseReport) -> bytes:
    """Export the displayed table with version/scope metadata on every record."""
    table = course_table(report)
    if table.empty:
        table = pd.DataFrame(columns=["Course ID", "Course", "Graded n"])
    table.insert(0, "Dataset version", report.dataset_version)
    table.insert(1, "Generated at (UTC)", report.generated_at)
    table.insert(2, "Scope", scope_text(report))
    # Defend spreadsheet formula interpretation of any future display labels.
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(table.columns)
    for values in table.itertuples(index=False, name=None):
        writer.writerow("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values)
    return stream.getvalue().encode("utf-8-sig")
