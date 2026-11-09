"""Report workspace entry and reusable view/legacy-Agent components."""
from collections import Counter

import streamlit as st

from copilot.dashboard.client import DashboardClient, DashboardError
from copilot.dashboard.presentation import (TYPE_LABELS, STAFF_TYPE_LABELS, course_table, decimal_text,
                                           mean_bands_table, rates_chart, report_csv, staff_scope_text)
from copilot.dashboard.workflow import VIEW_LABELS


def show_error(exc):
    st.error(str(exc))
    if exc.request_id:
        with st.expander("请求详情"):
            st.caption(f"Request ID: {exc.request_id}")


def show_report(report, selected_views=tuple(VIEW_LABELS)):
    st.caption("统计范围：" + staff_scope_text(report))
    summary = report.summary
    cells = st.columns(4)
    for cell, label, value in zip(cells, ("平均分", "及格率", "有效成绩数", "范围内学生"),
                                  (decimal_text(summary.average_score), decimal_text(summary.pass_rate_percent, percent=True),
                                   str(summary.graded_count), str(summary.student_count))):
        cell.metric(label, value)
    st.caption(f"成绩记录 {summary.enrollment_count} · 缺考/缺失 {summary.missing_count} · 免修 {summary.exempt_count}。"
               "均分和比率只使用有效成绩；空白不补零。")
    names = Counter(row.course_name for row in report.rows)
    labels = {row.course_id: row.course_name if names[row.course_name] == 1 else f"{row.course_name}（课程{i+1}）"
              for i, row in enumerate(report.rows)}
    if not report.rows:
        st.info("这个范围内没有选课成绩，请调整范围后更新预览。")
    elif selected_views:
        for tab, view in zip(st.tabs([VIEW_LABELS[v] for v in selected_views]), selected_views):
            with tab:
                if view == "course_table":
                    table = course_table(report)
                    table["Course type"] = table["Course type"].replace({label: STAFF_TYPE_LABELS[code] for code, label in TYPE_LABELS.items()})
                    table["Course"] = table["Course ID"].map(labels)
                    table = table.drop(columns=["Course ID"]).rename(columns={"Course": "课程", "Course type": "课程类别",
                        "Average": "平均分", "Pass rate": "及格率", "Fail rate": "不及格率", "Excellent ≥80": "优良率（≥80）",
                        "Graded n": "有效成绩数", "Passed n": "及格数", "Failed n": "不及格数",
                        "Missing n": "缺考/缺失", "Exempt n": "免修数", "Enrolled n": "成绩记录数"})
                    st.dataframe(table, hide_index=True, width="stretch")
                    st.download_button("下载当前成绩分布（CSV）", report_csv(report), mime="text/csv",
                                       file_name=f"course-analysis-{report.scope.term}.csv")
                elif view == "rates_chart":
                    if summary.graded_count:
                        st.altair_chart(rates_chart(report, course_labels=labels, staff_labels=True), width="stretch")
                    else:
                        st.info("没有有效成绩，比率不可用，不显示为零。")
                    st.caption("绿色为及格率，橙色为不及格率。悬停可查看人数和有效样本量。")
                else:
                    st.caption("每一项是一门课程的精确均分，不是学生成绩分段。显示四舍五入不会改变分层。")
                    bands = mean_bands_table(report, include_ids=False, course_labels=labels)
                    bands = bands.rename(columns={"Course mean band": "课程均分区间", **{label: STAFF_TYPE_LABELS[code] for code, label in TYPE_LABELS.items()}})
                    bands = bands.map(lambda value: value.replace("mean ", "均分 ").replace(", n=", "，有效数=") if isinstance(value, str) else value)
                    st.dataframe(bands, hide_index=True, width="stretch")
    with st.expander("统计口径与证据来源"):
        st.write("及格：≥60分；不及格：<60分；优良：≥80分。分母为有效成绩记录。缺考/缺失、免修单列。")
        st.write("课程均分按未舍入值分层，分数和比率按半入方式保留两位小数。同名课程序号可在下方查证。")
        st.json({"dataset_version": report.dataset_version, "generated_at": report.generated_at,
                 "scope": report.scope.model_dump(exclude_none=True), "policy": report.policy.model_dump(mode="json"),
                 "course_labels": labels, "source": "/api/reports/courses → AnalyticsEngine.course_report"})


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




def show_agent_panel(client, dataset, report=None):
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


def main():
    from copilot.dashboard.workbench import main as run_workbench
    run_workbench(DashboardClient, show_report)
