"""Staff-facing upload and review steps. Parsing and activation use HTTP."""
import hashlib
import json

import streamlit as st

from copilot.dashboard.client import DashboardError
from copilot.dashboard.example import example_workbook
from copilot.dashboard.presentation import term_text
from copilot.dashboard.workflow import WorkflowError

POLICY_LABELS = {"block": "先核对原表，暂不处理冲突（默认）", "first": "已核实：保留第一行，排除后续重复行",
                 "last": "已核实：保留最后一行，排除前面的重复行"}
IMPORTANT_CODES = {"conflicting_student", "duplicate_student", "unknown_status", "unrecognized_header",
                   "unparsed_course", "invalid_student", "unrecognized_class", "unknown_major", "unknown_course_category"}


def parse_selected(client, state, policy="block"):
    try:
        if len(state.upload.content) > 5 * 1024 * 1024:
            raise WorkflowError("文件超过5 MiB，请使用较小的学院成绩工作簿。")
        digest = state.upload.sha256
        with st.spinner("正在识别课程、检查空值与重复记录…"):
            result = client.college_preview(state.upload.name, state.upload.content, duplicate_policy=policy)
        state.set_preview(result, file_hash=digest)
        state.invalid_reason = None
    except (DashboardError, WorkflowError) as exc:
        state.invalid_reason = str(exc)
        st.error(str(exc))


def show_upload(client, state):
    st.markdown("### 从一份成绩单开始")
    st.write("上传学院导出的 Excel，先检查识别与清洗结果，再预览统一口径的统计和图表。")
    epoch = st.session_state.get("college_upload_epoch", 0)
    with st.container(border=True):
        uploaded = st.file_uploader("选择学院成绩单", type=["xlsx"], key=f"college_upload_{epoch}", max_upload_size=5,
                                    help="支持单工作表学院成绩单，最多5 MiB；上传与解析不会替换现有数据。")
        if uploaded is not None and state.select_upload(uploaded.name, uploaded.getvalue()):
            parse_selected(client, state)
            st.rerun()
        st.caption("仅支持 XLSX · 先核对后提交 · 真实学生数据不发送外部模型")
    if state.upload:
        st.info("本次工作簿与核对记录仍保留，可返回继续；只有再次确认才会替换本地分析数据。")
        if st.button("继续数据核对", width="stretch"):
            if state.preview is None:
                parse_selected(client, state)
            else:
                state.navigate("review")
            st.rerun()
        if state.confirmed_matches_upload and st.button("返回已确认的分析"):
            state.navigate("analysis")
            st.rerun()
    st.markdown("**没有成绩单？**")
    if st.button("使用示例数据体验", type="secondary"):
        state.select_upload("示例成绩单.xlsx", example_workbook(), source="example", force=True)
        st.session_state.college_upload_epoch = epoch + 1
        parse_selected(client, state)
        st.rerun()
    with st.expander("开始前需要了解什么？"):
        st.write("1. 成绩单应含学号、班级及带学分、学时、学期的课程表头。")
        st.write("2. 空白不被当成缺考或零分；重复学号、未知状态等需要核对。")
        st.write("3. 确认后替换当前学生及成绩，已有课程目录保留。原件与清洗、确认记录仅在本地私有目录留存。")
        st.write("4. 本轮提供上传、核对、分析；AI报告撰写和Word导出将在后续开放。")


def issue_table(items):
    return [{"行": item.row, "列": item.column, "问题": item.message} for item in items]


def show_review(client, state):
    st.subheader("解析与数据核对")
    if state.upload is None:
        state.step = "upload"
        st.rerun()
    st.caption("合成示例工作簿" if state.upload.source == "example" else "当前本地工作簿 · 先核对再使用")
    preview = state.preview
    if preview is None:
        st.warning(state.invalid_reason or "本次工作簿尚未完成解析。")
        if st.button("重新解析工作簿"):
            parse_selected(client, state)
            st.rerun()
        return
    metrics = st.columns(4)
    for cell, label, value in zip(metrics, ("课程列", "班级", "成绩记录", "全空课程列"),
                                  (preview.counts["course_columns"], preview.counts["classes"],
                                   preview.counts["enrollments"], preview.counts["excluded_empty_courses"])):
        cell.metric(label, value)
    st.caption(f"已识别 {preview.counts['students']} 名学生的有效信息；统计只覆盖成功解析的成绩或明确状态。")
    st.write("学期：" + "；".join(term_text(term) for term in preview.terms))
    errors = [i for i in preview.issues if i.severity == "error"]
    important = [i for i in preview.issues if i.severity != "error" and i.code in IMPORTANT_CODES]
    informational = [i for i in preview.issues if i not in errors and i not in important]
    if errors:
        st.error("存在必须先解决的问题，当前不能确认数据。请对照原表按行列坐标核查。")
        st.dataframe(issue_table(errors), hide_index=True, width="stretch")
    if preview.issue_counts.get("conflicting_student"):
        st.warning("学号重复且记录冲突。以下策略会取舍整行，不拼接成绩，也不判断重修或留级。")
        policy = st.selectbox("重复学号处理", list(POLICY_LABELS), index=list(POLICY_LABELS).index(preview.duplicate_policy),
                              format_func=POLICY_LABELS.get, key=f"review_policy_{state.upload.sha256}")
        if policy != preview.duplicate_policy:
            parse_selected(client, state, policy)
            st.rerun()
    if important:
        with st.expander("需要人工核对的异常与排除项", expanded=True):
            st.warning("确认前请检查这些处理结果；异常值不会被默认为零分或缺考。")
            st.dataframe(issue_table(important), hide_index=True, width="stretch")
    if informational:
        with st.expander("其他清洗说明与结构提醒"):
            st.dataframe(issue_table(informational), hide_index=True, width="stretch")
    with st.expander("查看课程识别、排除清单和脱敏样例"):
        st.dataframe([{"原列": c.column, "课程": c.name, "学期": c.term, "学分": c.credits,
                       "学时": c.hours, "处理": c.reason if c.excluded else "保留明确成绩与状态"} for c in preview.courses],
                     hide_index=True, width="stretch")
        if preview.sample:
            st.caption("最多8条别名样例，不含原姓名、学号。")
            st.dataframe(preview.sample, hide_index=True, width="stretch")
        st.write({"专业人数": preview.majors, "班级": preview.classes, "全部问题计数": preview.issue_counts})
        for assumption in preview.assumptions:
            st.write("• " + assumption)
        if preview.counts.get("issues_omitted"):
            st.warning("明细最多展示200条；分类计数包含全部问题。请先核查完整原表。")
    with st.expander("原件、清洗步骤与确认记录（仅本地）"):
        st.download_button("下载本次原始工作簿", state.upload.content, file_name="成绩单原件.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("下载清洗与确认摘要", json.dumps(state.history, ensure_ascii=False, indent=2).encode("utf-8"),
                           file_name="导入核对记录.json", mime="application/json")
        if state.confirmed:
            st.json(state.confirmed.model_dump(mode="json"))
        st.caption("原件保留于当前会话，确认后另存本地私有档案。关闭或刷新浏览器会清空会话，但不会删除已确认数据及本地档案。")
    if state.confirmed_matches_upload:
        st.success("这些数据已确认，返回核对不会重复提交或改变数据库。")
        if state.confirmed.archive_status == "pending":
            st.warning("数据已提交，本地原件已留存，但档案整理未完成；请核查本地档案，勿重复提交。")
        if st.button("返回统计与图表", type="primary"):
            state.navigate("analysis")
            st.rerun()
        return
    if not preview.can_import:
        st.info("请先处理上述阻止项，或修正原表后重新上传。")
        st.button("确认数据并进入分析", disabled=True)
        return
    st.info("确认将使用本次清洗后的数据替换当前学生及成绩；原件、清洗步骤和确认记录仅本地留存。")
    signature = hashlib.sha256((state.upload.sha256 + preview.duplicate_policy + (preview.preview_token or "")).encode()).hexdigest()
    checked = st.checkbox("我已核对清洗结果、异常项及重复行处理，确认使用这些数据", key=f"workflow_confirm_{signature}")
    if st.button("确认数据并进入分析", disabled=not checked, type="primary"):
        try:
            with st.spinner("正在确认并准备分析…"):
                result = client.college_commit(state.upload.name, state.upload.content, preview_token=preview.preview_token)
            state.confirm(result)
            st.rerun()
        except (DashboardError, WorkflowError) as exc:
            state.invalid_reason = str(exc)
            st.error(str(exc))
    if st.button("重新解析并更新核对记录"):
        parse_selected(client, state, preview.duplicate_policy)
        st.rerun()
