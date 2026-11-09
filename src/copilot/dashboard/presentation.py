"""Display-only projections of one backend report, never statistical calculations."""
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
BAND_LABELS = {"[0,60)": "0–<60", "[60,70)": "60–<70", "[70,80)": "70–<80",
               "[80,90)": "80–<90", "[90,100]": "90–100"}
PASS_COLOR, FAIL_COLOR = "#16826C", "#D67632"


def decimal_text(value, *, percent=False):
    return "—" if value is None else f"{value:.2f}" + ("%" if percent else "")


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


def rates_chart(report: CourseReport):
    data = rates_table(report)
    base = alt.Chart(data).encode(
        y=alt.Y("Course:N", sort=None, title=None, axis=alt.Axis(labelLimit=350)),
        yOffset=alt.YOffset("Series:N", sort=["Pass rate", "Fail rate"]),
        x=alt.X("Rate:Q", title="Percent of graded records", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("Series:N", title=None, scale=alt.Scale(domain=["Pass rate", "Fail rate"], range=[PASS_COLOR, FAIL_COLOR])),
        tooltip=[alt.Tooltip("Course:N"), alt.Tooltip("Series:N"), alt.Tooltip("Rate label:N"),
                 alt.Tooltip("Records:Q"), alt.Tooltip("Graded n:Q")])
    bars = base.mark_bar()
    labels = base.mark_text(align="left", dx=3, color="#283D47").encode(text="Rate label:N")
    return (bars + labels).properties(height=max(180, 46 * len(report.rows)), title="Course pass and fail rates — graded records only")


def mean_bands_table(report: CourseReport) -> pd.DataFrame:
    lookup = {row.course_id: row for row in report.rows}
    rows = []
    for band in report.mean_bands:
        by_type = {}
        for course_id in band.course_ids:
            row = lookup[course_id]
            by_type.setdefault(row.course_type, []).append(f"{row.course_name} ({row.course_id}, mean {decimal_text(row.statistics.average_score)}, n={row.statistics.graded_count})")
        rows.append({"Course mean band": BAND_LABELS[band.label],
                     **{label: "\n".join(by_type.get(code, [])) or "—" for code, label in TYPE_LABELS.items()}})
    ungraded = [row for row in report.rows if row.average_band is None]
    if ungraded:
        rows.append({"Course mean band": "No graded records", **{label: "\n".join(
            f"{row.course_name} ({row.course_id})" for row in ungraded if row.course_type == code) or "—" for code, label in TYPE_LABELS.items()}})
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
