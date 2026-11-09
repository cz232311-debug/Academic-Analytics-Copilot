"""Upload-first report workspace; no statistical computation or model calls."""
import streamlit as st

from copilot.dashboard.client import DashboardError
from copilot.dashboard.import_panel import show_review, show_upload
from copilot.dashboard.presentation import STAFF_MAJOR_LABELS, STAFF_TYPE_LABELS, term_text
from copilot.dashboard.workflow import SECTION_LABELS, VIEW_LABELS, WorkflowError, WorkflowState
from copilot.schemas import ReportScope

STEP_LABELS = {"upload": "1 上传", "review": "2 核对", "analysis": "3 分析", "writing": "4 撰写", "export": "5 导出"}


def navigation(state):
    for column, (step, label) in zip(st.columns(5), STEP_LABELS.items()):
        allowed = step == "upload" or step == "review" and state.upload is not None and state.preview is not None
        allowed |= step == "analysis" and state.confirmed_matches_upload
        allowed |= step in {"writing", "export"} and state.has_current_context
        if column.button(label, disabled=not allowed, type="primary" if step == state.step else "secondary", width="stretch"):
            try:
                state.navigate(step)
                st.rerun()
            except WorkflowError as exc:
                st.warning(str(exc))


def _index(options, selected):
    return options.index(selected) if selected in options else 0


def show_analysis(client, state, render_report):
    if not state.confirmed_matches_upload:
        st.warning("当前工作簿还没有确认，不能复用此前的分析。")
        return
    st.subheader("统计与图表预览")
    st.caption("合成示例 · 仅供体验" if state.confirmed.input_source == "example" else "已确认的本地成绩单")
    if state.confirmed.archive_status == "pending":
        st.warning("数据已提交，原件已在本地留存，但档案整理未完成。请核查本地档案，勿重复提交。")
    try:
        catalog = client.catalog()
    except DashboardError as exc:
        state.invalidate_analysis("暂时无法核实统计目录，请重试。")
        st.error(str(exc))
        return
    if catalog.dataset_version != state.confirmed.dataset_version:
        state.invalidate_analysis("数据目录已改变，请重新核对并确认。")
        st.warning(state.invalid_reason)
        return
    first_scope = state.scope is None
    previous = state.scope or ReportScope(term=catalog.terms[0])
    suffix = str(state.selection_revision) + "_" + state.confirmed.dataset_version
    classes, courses = {c.class_id: c for c in catalog.classes}, {c.course_id: c for c in catalog.courses}
    with st.container(border=True):
        st.markdown("**报告范围**")
        top = st.columns(3)
        term = top[0].selectbox("学期", catalog.terms, index=_index(catalog.terms, previous.term), key="wf_term_" + suffix,
                               format_func=term_text, on_change=state.invalidate_analysis)
        grades = [None, *sorted({c.grade_level for c in catalog.classes})]
        grade = top[1].selectbox("入学年级", grades, index=_index(grades, previous.grade_level), key="wf_grade_" + suffix,
                                format_func=lambda v: "全部年级" if v is None else f"{v}级" if v >= 1900 else f"演示年级 {v}",
                                on_change=state.invalidate_analysis)
        majors = [None, *catalog.majors]
        major = top[2].selectbox("专业", majors, index=_index(majors, previous.major), key="wf_major_" + suffix,
                                format_func=lambda v: "全部专业" if v is None else STAFF_MAJOR_LABELS.get(v, v), on_change=state.invalidate_analysis)
        bottom = st.columns(3)
        class_options = [None, *classes]
        class_id = bottom[0].selectbox("班级", class_options, index=_index(class_options, previous.class_id), key="wf_class_" + suffix,
                                      format_func=lambda v: "全部班级" if v is None else classes[v].class_label,
                                      on_change=state.invalidate_analysis)
        types = [None, *catalog.course_types]
        course_type = bottom[1].selectbox("课程类型", types, index=_index(types, previous.course_type), key="wf_type_" + suffix,
                                         format_func=lambda v: "全部类型" if v is None else STAFF_TYPE_LABELS.get(v, v),
                                         on_change=state.invalidate_analysis)
        course_options = [None, *courses]
        course_id = bottom[2].selectbox("课程", course_options, index=_index(course_options, previous.course_id), key="wf_course_" + suffix,
                                       format_func=lambda v: "全部课程" if v is None else f"{courses[v].course_name} · {courses[v].grade_level}级",
                                       on_change=state.invalidate_analysis)
        state.set_scope(ReportScope(term=term, grade_level=grade, major=major, class_id=class_id, course_type=course_type, course_id=course_id))
        update = st.button("更新统计预览", type="primary")
    if first_scope or update:
        try:
            with st.spinner("正在生成本范围的统一统计与图表…"):
                report = client.report(state.scope, expected_version=state.confirmed.dataset_version)
            state.accept_report(report)
        except (DashboardError, WorkflowError) as exc:
            state.invalidate_analysis(str(exc))
            st.error(str(exc))
    if not state.has_current_report:
        st.info(state.invalid_reason or "请生成当前范围的统计预览。")
        return
    chosen = state.context.selected_views if state.has_current_context else tuple(VIEW_LABELS)
    views = st.multiselect("纳入报告的内容", list(VIEW_LABELS), default=chosen, format_func=VIEW_LABELS.get,
                           key="wf_views_" + suffix, on_change=state.invalidate_content)
    sections = st.multiselect("报告章节", list(SECTION_LABELS),
                              default=state.context.sections if state.has_current_context else tuple(SECTION_LABELS),
                              format_func=SECTION_LABELS.get, key="wf_sections_" + suffix, on_change=state.invalidate_content)
    render_report(state.report, selected_views=views)
    if views and sections and state.report.rows:
        try:
            state.build_context(views, sections)
        except WorkflowError as exc:
            state.invalidate_content()
            st.warning(str(exc))
    else:
        state.invalidate_content()
        st.info("请选择至少一项图表和一个章节；无成绩的范围暂不能进入后续报告结构。")
    if st.button("保存内容选择，进入撰写", disabled=not state.has_current_context):
        state.navigate("writing")
        st.rerun()


def show_future(state):
    writing = state.step == "writing"
    st.subheader("AI 辅助报告撰写" if writing else "Word 导出")
    if not state.has_current_context:
        st.warning("原统计或报告内容已失效，请返回分析重新生成。")
        return
    st.info("本步骤为后续功能预留。本轮未实现最终报告生成或DOCX导出，不会调用外部模型。")
    st.write("已选内容：" + "、".join(VIEW_LABELS[v] for v in state.context.selected_views))
    st.write("已选章节：" + "、".join(SECTION_LABELS[s] for s in state.context.sections))
    st.caption("真实学生数据仍禁止AI访问；后续聚合数据授权机制需单独审查。")
    st.button("AI 辅助撰写（后续开放）" if writing else "导出 Word（后续开放）", disabled=True)
    if st.button("返回分析并调整内容"):
        state.navigate("analysis")
        st.rerun()
    with st.expander("报告上下文与证据详情（聚合数据）"):
        st.json(state.context.model_dump(mode="json"))


def main(client_factory, render_report):
    st.set_page_config(page_title="学业分析报告工作台", page_icon="🎓", layout="wide")
    st.markdown("<style>.block-container{max-width:1180px;padding-top:2.2rem;}[data-testid=stMetric]{padding:.6rem 0;}"
                "[data-testid=stAppViewContainer]{background:#fcfdfd;} h1{letter-spacing:.02em;}"
                "[data-testid=stAppDeployButton],#MainMenu{display:none;}</style>", unsafe_allow_html=True)
    st.title("学业分析报告工作台")
    st.caption("本地优先 · 先核对数据，再形成报告")
    if "workflow" not in st.session_state:
        st.session_state.workflow = WorkflowState()
    state = st.session_state.workflow
    client = None
    connected = False
    try:
        client = client_factory()
        health = client.health()
        connected = True
        if state.confirmed:
            state.observe_dataset(client.dataset() if health.dataset_active else None)
    except DashboardError as exc:
        state.invalidate_analysis("暂时无法核实数据版本，旧统计已暂停显示。")
        st.error(str(exc))
        with st.expander("连接与运行详情"):
            st.code("& '.\\.venv\\Scripts\\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000", language="powershell")
    navigation(state)
    st.divider()
    if state.invalid_reason and state.step != "analysis":
        st.warning(state.invalid_reason)
    if state.step == "upload" and client is not None:
        show_upload(client, state)
    elif not connected:
        st.info("本地服务连接恢复后，可继续当前核对；已确认数据不会因返回或断线被删除。")
        if st.button("重试连接"):
            st.rerun()
    elif state.step == "review":
        show_review(client, state)
    elif state.step == "analysis":
        show_analysis(client, state, render_report)
    else:
        show_future(state)
    if connected:
        with st.expander("数据与运行详情"):
            st.caption(f"本地服务：{client.base_url}")
            if state.confirmed:
                st.json(state.confirmed.model_dump(mode="json"))
            else:
                st.caption("本会话尚未确认分析数据；已有本地数据不会自动进入本次报告。")
