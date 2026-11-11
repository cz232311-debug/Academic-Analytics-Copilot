"""Staff-facing upload and review steps. Parsing and activation use HTTP."""
import hashlib

import streamlit as st

from copilot.dashboard.client import DashboardError
from copilot.dashboard.example import example_workbook
from copilot.dashboard.presentation import term_text
from copilot.dashboard.synthetic_panel import show_synthetic_panel
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
    st.write("**先选择一份学院导出的Excel成绩单。** 系统会自动整理课程和成绩，随后请你核对，再查看统计结果。")
    epoch = st.session_state.get("college_upload_epoch", 0)
    with st.container(border=True):
        uploaded = st.file_uploader("选择学院成绩单", type=["xlsx"], key=f"college_upload_{epoch}", max_upload_size=5,
                                    help="支持单工作表学院成绩单，最多5 MiB；上传与解析不会替换现有数据。")
        if uploaded is not None and state.select_upload(uploaded.name, uploaded.getvalue()):
            parse_selected(client, state)
            st.rerun()
        st.write("支持.xlsx文件，最大5 MB。上传后先核对，点击确认才会替换当前数据。")
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
    show_synthetic_panel(client, state, parse_selected)
    with st.expander("开始前需要了解什么？"):
        st.write("成绩单应只有一个成绩工作表，包含学号、班级，以及带学分、学时、学期的课程表头。")
        st.write("空白不会算作零分或缺考。重复学号、无法识别的成绩等问题，会在下一步提示你核对。")
        st.write("原件和处理记录只在本机保留；真实学生数据不会发送给外部AI。")
    st.info("目前可完成：上传 → 核对与确认 → 统计分析及图表预览。报告撰写与审阅、Word导出正在开发中。")


def issue_table(items):
    return [{"行": item.row, "列": item.column, "问题": item.message} for item in items]


def show_review(client, state):
    st.subheader("解析与数据核对")
    if state.upload is None:
        state.step = "upload"
        st.rerun()
    st.write("请先看人数和课程是否符合原表，再检查下方提示。没有阻止项后，勾选确认并进入分析。")
    if state.upload.source == "example":
        st.write("当前为合成示例，学生与成绩均为虚构。")
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
    st.write(f"已识别 **{preview.counts['students']} 名学生**。空白成绩不计入统计，零分会保留。")
    st.write("学期：" + "；".join(term_text(term) for term in preview.terms))
    errors = [i for i in preview.issues if i.severity == "error"]
    important = [i for i in preview.issues if i.severity != "error" and i.code in IMPORTANT_CODES]
    informational = [i for i in preview.issues if i not in errors and i not in important]
    if errors:
        st.error("还有问题需要处理，暂时不能进入分析。请按下表位置检查Excel，修改后重新上传；重复学号也可在下方核实处理。")
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
        with st.expander(f"查看自动整理说明（{len(informational)}条）"):
            st.dataframe(issue_table(informational), hide_index=True, width="stretch")
    if preview.counts["excluded_empty_courses"]:
        st.info(f"有{preview.counts['excluded_empty_courses']}列课程没有任何成绩，本次已跳过。你可以展开课程清单核对。")
    with st.expander("核对课程清单与跳过的课程"):
        st.dataframe([{"原列": c.column, "课程": c.name, "学期": c.term, "学分": c.credits,
                       "学时": c.hours, "处理": c.reason if c.excluded else "保留明确成绩与状态"} for c in preview.courses],
                     hide_index=True, width="stretch")
        st.write("班级：" + "、".join(preview.classes))
        if preview.counts.get("issues_omitted"):
            st.warning("明细最多展示200条；分类计数包含全部问题。请先核查完整原表。")
    with st.expander("数据会如何处理和保存？"):
        st.write("确认后会替换当前学生与成绩，已有课程目录保留。空白不生成成绩；零分保留；明确的缺考与免修单独记录。")
        st.write("原件、整理摘要和确认记录保存在本机。分析完成后，可从统一入口下载不含学生名单和原件的详细统计。")
        st.write("返回上一步不会删除已确认的数据。整页刷新后会回到上传页，本机数据仍保留。")
    if state.confirmed_matches_upload:
        st.success("这些数据已确认。下一步点击“返回统计与图表”，无需再次提交。")
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
    st.write("**下一步：** 核对完毕后，勾选下方确认，再点击“确认数据并进入分析”。这会替换当前学生与成绩。")
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
