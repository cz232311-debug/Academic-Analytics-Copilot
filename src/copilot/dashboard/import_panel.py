"""Explicit local workbook preview and confirmation, through HTTP only."""
import hashlib

import streamlit as st

from copilot.dashboard.client import DashboardError

POLICY_LABELS = {"block": "阻止提交，先核对重复行（默认）", "first": "我已核对：保留第一行，排除后续重复行",
                 "last": "我已核对：保留最后一行，排除前面的重复行"}


def show_import_panel(client):
    success = st.session_state.get("college_import_success")
    if success:
        st.success("导入已提交，Dashboard已按新数据重新加载。真实身份未保存，AI对上传数据保持禁用。")
        st.caption(f"数据版本：{success['version']}；导入{success['students']}名学生、{success['enrollments']}条选课成绩；"
                   f"保留原课程目录{success['retained']}门。")
    with st.expander("学院成绩 XLSX 导入 · 上传 → 自动预览 → 确认提交", expanded=False):
        st.caption("仅本地解析；原姓名和学号不进入分析数据库、不发送模型。上传和预览均不会覆盖当前数据。")
        st.caption("重复冲突的第一行／最后一行策略取舍整行，不合并补充成绩；请先按问题坐标核对原表。")
        epoch = st.session_state.get("college_upload_epoch", 0)
        uploaded = st.file_uploader("上传学院原始成绩单（XLSX）", type=["xlsx"], key=f"college_upload_{epoch}")
        policy = st.selectbox("重复学号且记录冲突时的处理", list(POLICY_LABELS), format_func=POLICY_LABELS.get,
                              key=f"college_policy_{epoch}")
        if uploaded is None:
            return
        content = uploaded.getvalue()
        signature = hashlib.sha256(content).hexdigest() + ":" + policy
        if len(content) > 5 * 1024 * 1024:
            st.error("文件超过5 MiB；未解析、未导入。")
            return
        if st.button("重新解析预览"):
            st.session_state.pop("college_preview_signature", None)
        if st.session_state.get("college_preview_signature") != signature:
            st.session_state.pop("college_preview", None)
            st.session_state.pop("college_confirm", None)
            st.session_state.pop("college_import_success", None)
            st.session_state.college_preview_signature = signature
            try:
                with st.spinner("识别表头、清洗并校验；尚未写入数据库…"):
                    st.session_state.college_preview = client.college_preview(uploaded.name, content, duplicate_policy=policy)
            except DashboardError as exc:
                st.error(str(exc))
        preview = st.session_state.get("college_preview")
        if preview is None:
            return
        st.markdown("**解析及验证预览 · 当前数据库尚未改变**")
        cells = st.columns(4)
        for cell, label, name in zip(cells, ("有效学生", "识别课程列", "将导入选课成绩", "全空排除课程列"),
                                     ("students", "course_columns", "enrollments", "excluded_empty_courses")):
            cell.metric(label, preview.counts.get(name, 0))
        st.write({"学期（学年起始年-T学期）": preview.terms, "专业人数": preview.majors, "班级": preview.classes})
        st.caption(f"重复行策略：{POLICY_LABELS[preview.duplicate_policy]}。有效范围以本预览为准。")
        with st.expander("查看清洗假设、排除数量与问题坐标", expanded=bool(preview.issues)):
            for assumption in preview.assumptions:
                st.write("• " + assumption)
            st.write({"本次计数": preview.counts, "问题分类计数": preview.issue_counts,
                      "当前库计数（确认前）": preview.before_counts})
            if preview.issues:
                st.dataframe([i.model_dump() for i in preview.issues], hide_index=True, width="stretch")
            if preview.counts.get("issues_omitted"):
                st.warning("问题明细仅展示前200条，分类计数包含全部问题；请对照原表核查。")
        st.markdown("**课程识别与排除清单**")
        st.dataframe([c.model_dump() for c in preview.courses], hide_index=True, width="stretch")
        if preview.sample:
            st.markdown("**脱敏预览（最多8条；不含原姓名、学号）**")
            st.dataframe(preview.sample, hide_index=True, width="stretch")
        if not preview.can_import:
            st.error("验证未通过：请修正阻止提交的问题，或核对后显式选择重复行策略，再重新预览。")
            return
        st.warning("确认将替换当前学生及选课成绩；已有课程目录保留。异常单元格、无效行和所选重复行将按预览排除。")
        confirmed = st.checkbox("我已核对预览、排除项与重复行策略，确认替换当前分析数据", key=f"college_confirm_{signature}")
        if st.button("确认导入到当前数据库", disabled=not confirmed):
            try:
                with st.spinner("事务提交并刷新统计…"):
                    result = client.college_commit(uploaded.name, content, preview_token=preview.preview_token)
                st.session_state.college_import_success = {"version": result.dataset.dataset_version,
                    "students": result.imported_counts["students"], "enrollments": result.imported_counts["enrollments"],
                    "retained": result.retained_course_count}
                for key in ("course_report", "agent_response", "college_preview", "college_preview_signature", "college_confirm"):
                    st.session_state.pop(key, None)
                st.session_state.college_upload_epoch = epoch + 1
                st.rerun()
            except DashboardError as exc:
                st.error(str(exc))
