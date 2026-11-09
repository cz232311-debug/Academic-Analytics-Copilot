"""English Streamlit workbench. Every analytical value comes from FastAPI."""
import streamlit as st

from copilot.dashboard.client import DashboardClient, DashboardError
from copilot.dashboard.import_panel import show_import_panel
from copilot.dashboard.presentation import (BAND_LABELS, MAJOR_LABELS, TYPE_LABELS, course_table, decimal_text,
                                           mean_bands_table, rates_chart, report_csv, scope_text)
from copilot.schemas import ReportScope


def show_error(exc: DashboardError):
    st.error(str(exc))
    if exc.request_id:
        st.caption(f"Request ID: {exc.request_id}")


def show_report(report):
    st.subheader("Academic analysis")
    st.caption(scope_text(report))
    st.caption(f"Dataset version: {report.dataset_version} · Generated at (UTC): {report.generated_at}")
    summary = report.summary
    columns = st.columns(4)
    columns[0].metric("Average score", decimal_text(summary.average_score))
    columns[1].metric("Pass rate", decimal_text(summary.pass_rate_percent, percent=True))
    columns[2].metric("Graded records", f"{summary.graded_count:,}")
    columns[3].metric("Students in scope", f"{summary.student_count:,}")
    st.caption(f"Enrolled records: {summary.enrollment_count:,} · Missing: {summary.missing_count:,} · Exempt: {summary.exempt_count:,}. "
               "Averages and percentages use graded records only. No grades are imputed. Overall averages are over enrollment records.")
    st.caption("Score bands use continuous boundaries, for example 60–<70 includes 69.99. Excellent rate means graded scores ≥80.00. "
               "Display rounds half up to two decimals. Course mean bands use exact, unrounded means.")
    if not report.rows:
        st.info("No enrollments match these filters. Choose another combination and load analysis again.")
        return
    tabs = st.tabs(["Course analysis", "Rates and samples", "Course mean bands", "Report preview"])
    with tabs[0]:
        st.markdown("**Table A · Course score distribution and outcomes**")
        st.dataframe(course_table(report), hide_index=True, width="stretch")
        st.download_button("Download course analysis CSV", data=report_csv(report), mime="text/csv",
                           file_name=f"course-analysis-{report.scope.term}-{report.dataset_version[:8]}.csv")
    with tabs[1]:
        st.markdown("**Figure B · Course pass and fail rates**")
        if summary.graded_count:
            st.altair_chart(rates_chart(report), width="stretch")
        else:
            st.info("There are no graded records. Rates are unavailable rather than zero.")
        st.caption("Green = passed · Orange = failed. Hover to see the numerator and graded sample size. "
                   "Courses with no graded records have unavailable rates. Sample sizes differ; these descriptive rates do not establish teaching quality.")
    with tabs[2]:
        st.markdown("**Table C · Courses grouped by their average score**")
        st.caption("Each item represents a course mean. This is different from the student-record score distribution in Table A. "
                   "A mean displayed as 80.00 can belong to 70–<80 when its exact value is 79.995.")
        st.dataframe(mean_bands_table(report), hide_index=True, width="stretch")
    with tabs[3]:
        st.markdown(f"**Academic performance report · {report.scope.term}**")
        st.write(f"Scope: {scope_text(report)}")
        st.write(f"Across {len(report.rows)} courses and {summary.graded_count:,} graded enrollment records, "
                 f"the average score is {decimal_text(summary.average_score)} and the pass rate is {decimal_text(summary.pass_rate_percent, percent=True)}.")
        st.write("Table A records grade distributions and outcomes. Figure B visualizes the same backend rates. "
                 "Table C classifies exact course means. Missing and exempt records are listed separately.")
        st.caption("Deterministic preview from this report response. AI commentary appears separately with its own evidence and query scope. "
                   "Word/PDF export is planned for a later increment.")
        st.json({"dataset_version": report.dataset_version, "generated_at": str(report.generated_at),
                 "scope": report.scope.model_dump(exclude_none=True), "policy": report.policy.model_dump(mode="json")})


def show_agent_result(result, report, active_version):
    st.caption(f"Provider: {result.provider} · Model: {result.model} · "
               f"{'SIMULATED / no LLM call' if result.simulated else 'External LLM'} · Answer mode: {result.answer_mode}")
    if result.dataset_version != active_version or (report and result.dataset_version != report.dataset_version):
        st.warning("This AI response uses a different dataset version from the current data or report. Reload and ask again before comparing values.")
    if result.status != "completed":
        st.info(result.answer)
    else:
        st.write(result.answer)
    if result.fallback_reason:
        st.warning(f"Model wording failed validation ({result.fallback_reason}). A deterministic evidence-based answer is displayed.")
    if result.guidance:
        st.markdown("**Suggested follow-up · not statistical findings**")
        for guidance in result.guidance:
            st.write(f"• {guidance}")
    st.caption(f"Request ID: {result.request_id} · AI dataset version: {result.dataset_version}")
    with st.expander("Statistical evidence and actual query scope"):
        if not result.evidence:
            st.info("No statistical evidence was returned for this response.")
        for evidence in result.evidence:
            st.markdown(f"**Evidence {evidence.evidence_id} · {evidence.tool}**")
            st.json({"evidence_id": evidence.evidence_id, "dataset_version": evidence.dataset_version,
                     "arguments": evidence.arguments, "data": evidence.data})
    with st.expander("Agent trace and token usage"):
        st.json({"request_id": result.request_id, "model_turns": result.model_turns,
                 "usage": result.usage.model_dump() if result.usage else None,
                 "tool_calls": [trace.model_dump(mode="json") for trace in result.tool_calls],
                 "model_trace": [trace.model_dump(mode="json") for trace in result.model_trace]})


def main():
    st.set_page_config(page_title="Academic Analytics Copilot", page_icon="🎓", layout="wide")
    st.title("Academic Analytics Copilot")
    st.caption("Academic analysis workbench · Local aggregate reports · Synthetic demo or confirmed local XLSX import")
    st.info("本地统计支持已确认的学院成绩导入。AI仅对生成的合成数据可用；真实学生数据不会发送给外部模型。")
    try:
        client = DashboardClient()
        dataset, catalog = client.dataset(), client.catalog()
    except DashboardError as exc:
        show_error(exc)
        st.code("& '.\\.venv\\Scripts\\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000", language="powershell")
        return
    if dataset.dataset_version != catalog.dataset_version:
        st.warning("The dataset changed while the page loaded. Refresh to obtain a consistent catalog.")
        return
    show_import_panel(client)
    with st.sidebar:
        st.subheader("Dataset and filters")
        st.caption(f"FastAPI: {client.base_url}")
        source_label = "synthetic students" if dataset.source == "generated" else "imported student aliases"
        st.write(f"{dataset.counts['students']:,} {source_label} · {dataset.counts['classes']} classes · {dataset.counts['courses']} catalog courses")
        st.caption(f"Source: {dataset.source} · Version: {dataset.dataset_version[:12]}")
        if st.button("Refresh data"):
            st.session_state.pop("course_report", None)
            st.rerun()
        with st.form("report_filters"):
            term = st.selectbox("Term", catalog.terms)
            grades = sorted({row.grade_level for row in catalog.classes})
            grade = st.selectbox("Grade", [None, *grades], format_func=lambda value: "All grades" if value is None else
                                 f"{value}级" if value >= 1900 else f"Grade {value}")
            major = st.selectbox("Major", [None, *catalog.majors], format_func=lambda value: "All majors" if value is None else MAJOR_LABELS.get(value, value))
            classes = {row.class_id: row for row in catalog.classes}
            class_id = st.selectbox("Class", [None, *classes], format_func=lambda value: "All classes" if value is None else f"{value} · {classes[value].class_label}")
            course_type = st.selectbox("Course type", [None, *catalog.course_types], format_func=lambda value: "All course types" if value is None else TYPE_LABELS.get(value, value))
            courses = {row.course_id: row for row in catalog.courses}
            course_id = st.selectbox("Course", [None, *courses], format_func=lambda value: "All courses" if value is None else f"{value} · {courses[value].course_name}")
            load = st.form_submit_button("Load analysis")
        st.caption("Click Load analysis after changing filters. The report heading always shows the filters used to compute the displayed result.")
        if "unspecified" in catalog.majors or "unspecified" in catalog.course_types:
            st.caption("Not provided means the source has no major/course-type classification. Legacy data is not assigned invented categories.")
    if load or "course_report" not in st.session_state:
        try:
            scope = ReportScope(term=term, grade_level=grade, class_id=class_id, course_id=course_id, major=major, course_type=course_type)
            with st.spinner("Loading deterministic analysis from FastAPI…"):
                st.session_state.course_report = client.report(scope, expected_version=catalog.dataset_version)
        except DashboardError as exc:
            st.session_state.pop("course_report", None)
            show_error(exc)
    report = st.session_state.get("course_report")
    if report:
        if report.dataset_version != dataset.dataset_version:
            st.warning("The displayed report is from an earlier dataset version. Click Load analysis to refresh it.")
        show_report(report)
    st.divider()
    st.subheader("AI Copilot")
    st.caption("AI uses the existing four controlled tools. It does not inherit the dashboard filters. Include term, grade, course or class IDs in your English question. "
               "Major and course-type filters are supported by the report API only. Chinese questions and personal-list queries are currently unsupported.")
    try:
        status = client.agent_status()
    except DashboardError as exc:
        show_error(exc)
        return
    st.caption(f"Configured backend provider: {status.provider} · Model: {status.model} · "
               f"{'SIMULATED offline provider' if status.simulated else 'External provider; submitting a question may incur API charges'}")
    if not dataset.external_llm_allowed:
        st.warning("AI is disabled for uploaded datasets. Local aggregate reports remain available.")
    with st.form("agent_query"):
        question = st.text_input("Ask an aggregate academic question", "What is the average score and pass rate for G1-C01?", key="agent_question", max_chars=1000)
        authorized = True if status.simulated else st.checkbox("I authorize this submitted question to use the configured external LLM API.")
        ask = st.form_submit_button("Ask Copilot", disabled=not dataset.external_llm_allowed)
    if ask:
        st.session_state.pop("agent_response", None)
        if not authorized:
            st.info("Confirm the external API call above, then submit the question.")
        elif not question.strip():
            st.info("Enter an English aggregate question before submitting.")
        else:
            try:
                with st.spinner("Selecting controlled tools and validating the answer…"):
                    st.session_state.agent_response = client.query(question.strip())
            except DashboardError as exc:
                show_error(exc)
    result = st.session_state.get("agent_response")
    if result:
        show_agent_result(result, report, dataset.dataset_version)
