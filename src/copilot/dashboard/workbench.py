"""Upload-first report workspace; no statistical computation or model calls."""
import streamlit as st

from copilot.dashboard.audit_export import build_analysis_archive
from copilot.dashboard.client import DashboardError
from copilot.dashboard.import_panel import show_review, show_upload
from copilot.dashboard.presentation import STAFF_MAJOR_LABELS, STAFF_TYPE_LABELS, course_option_labels, term_text
from copilot.dashboard.workflow import SECTION_LABELS, VIEW_LABELS, WorkflowError, WorkflowState
from copilot.schemas import ReportScope

STEP_LABELS = {"upload": "1 上传", "review": "2 核对", "analysis": "3 分析", "writing": "4 撰写", "export": "5 导出"}


def navigation(state):
    current = {"upload": "第1步 · 上传Excel", "review": "第2步 · 核对与确认", "analysis": "第3步 · 统计与图表预览"}
    st.markdown(f"**当前步骤：{current.get(state.step, '后续功能说明')}**")
    for column, (step, label) in zip(st.columns(3), list(STEP_LABELS.items())[:3]):
        allowed = step == "upload" or step == "review" and state.upload is not None and state.preview is not None
        allowed |= step == "analysis" and state.confirmed_matches_upload
        if column.button(label, disabled=not allowed, width="stretch"):
            try:
                state.navigate(step)
                st.rerun()
            except WorkflowError as exc:
                st.warning(str(exc))
        status = "正在进行" if step == state.step else (
            "已上传" if step == "upload" and state.upload else
            "已确认" if step == "review" and state.confirmed_matches_upload else
            "可查看" if step == "analysis" and state.confirmed_matches_upload else "待完成")
        column.write(status)


def _index(options, selected):
    return options.index(selected) if selected in options else 0


def show_analysis(client, state, render_report):
    if not state.confirmed_matches_upload:
        st.warning("当前工作簿还没有确认，不能复用此前的分析。")
        return
    st.subheader("统计与图表预览")
    counts = state.confirmed.imported_counts or state.confirmed.counts
    st.success(f"数据已确认：{counts.get('students', 0)}名学生，{counts.get('grade_records', 0)}条成绩记录。下面可以查看统计与图表。")
    if state.confirmed.input_source == "example":
        st.write("当前使用合成示例，所有学生与成绩均为虚构。")
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
    course_labels = course_option_labels(courses)
    with st.expander("更改分析范围（学期、班级或课程）", expanded=bool(state.invalid_reason)):
        st.write("默认分析全部已确认数据。只看某个班级或课程时，在这里调整，再点击“更新统计预览”。")
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
                                       format_func=lambda v: "全部课程" if v is None else course_labels[v],
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
    # Read already-updated widget state so the optional settings can follow the
    # results without showing stale selections for a rerun.
    selected_views = st.session_state.get("wf_views_" + suffix, chosen)
    class_name = classes[state.scope.class_id].class_label if state.scope.class_id in classes else "全部班级"
    st.write("班级：" + class_name)
    render_report(state.report, selected_views=selected_views)
    with st.expander("后续报告内容设置（可选）"):
        st.write("可以先选择需要的图表与章节。报告撰写和Word导出仍在开发中；这里的选择用于保留本次分析安排。")
        views = st.multiselect("纳入报告的内容", list(VIEW_LABELS), default=chosen, format_func=VIEW_LABELS.get,
                               key="wf_views_" + suffix, on_change=state.invalidate_content)
        sections = st.multiselect("报告章节", list(SECTION_LABELS),
                                  default=state.context.sections if state.has_current_context else tuple(SECTION_LABELS),
                                  format_func=SECTION_LABELS.get, key="wf_sections_" + suffix, on_change=state.invalidate_content)
    if views and sections and state.report.rows:
        try:
            state.build_context(views, sections)
        except WorkflowError as exc:
            state.invalidate_content()
            st.warning(str(exc))
    else:
        state.invalidate_content()
        st.info("请选择至少一项图表和一个章节；无成绩的范围暂不能进入后续报告结构。")
    st.info("当前可完成到统计分析。报告撰写与审阅、Word导出正在开发中。")
    with st.expander("分析资料与明细下载"):
        st.write("需要核对数字或留存结果时，可下载当前范围的统计、图表数据、计算说明和清洗摘要。包内不含学生名单或成绩单原件。")
        try:
            st.download_button("下载完整分析与数据明细", build_analysis_archive(state),
                               file_name="学业分析与数据明细.zip", mime="application/zip", on_click="ignore")
        except WorkflowError:
            st.warning("本次统计已发生变化，请先点击“更新统计预览”再下载。")


def show_future(state):
    writing = state.step == "writing"
    st.subheader("AI 辅助报告撰写" if writing else "Word 导出")
    if not state.has_current_context:
        st.warning("原统计或报告内容已失效，请返回分析重新生成。")
        return
    st.info("本步骤为后续功能预留。本轮未实现最终报告生成或DOCX导出，不会调用外部模型。")
    st.write("已选内容：" + "、".join(VIEW_LABELS[v] for v in state.context.selected_views))
    st.write("已选章节：" + "、".join(SECTION_LABELS[s] for s in state.context.sections))
    st.write("该功能尚未开放，目前可以完成上传、核对和统计分析。")
    if st.button("返回分析并调整内容"):
        state.navigate("analysis")
        st.rerun()


def main(client_factory, render_report):
    st.set_page_config(page_title="学业分析报告工作台", page_icon="🎓", layout="wide")
    # Colors and typography come from native light/dark themes. Only constrain
    # reading width and let supporting text inherit the current text color.
    st.markdown("<style>.block-container{max-width:1360px;padding-top:2rem;}"
                "[data-testid=stCaptionContainer] p{font-size:1rem;color:inherit;}</style>", unsafe_allow_html=True)
    st.title("学业分析报告工作台")
    st.write("上传学院成绩单，核对数据，查看各班和课程的成绩情况。")
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
            st.write("请联系负责启动系统的同事，检查本机分析服务是否开启。恢复后点击“重试连接”，此前已确认的数据仍会保留。")
            st.code("& '.\\.venv\\Scripts\\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000", language="powershell")
        if st.button("重试连接", type="primary"):
            st.rerun()
    navigation(state)
    st.divider()
    if state.invalid_reason and state.step != "analysis":
        st.warning(state.invalid_reason)
    if state.step == "upload" and client is not None:
        show_upload(client, state)
    elif not connected:
        st.info("本地服务连接恢复后，可继续当前核对；已确认数据不会因返回或断线被删除。")
    elif state.step == "review":
        show_review(client, state)
    elif state.step == "analysis":
        show_analysis(client, state, render_report)
    else:
        show_future(state)
