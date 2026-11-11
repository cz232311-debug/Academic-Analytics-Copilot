"""Chat-first academic analytics UI.

This module renders only. It never opens the database, computes a statistic,
calls a model or decides data provenance. Every number displayed comes from an
AgentResponse evidence record produced by the AnalyticsEngine on the server.

Two provenance modes are explicit and never mixed:
  * server-generated synthetic data -> execution_mode="grounded" (DeepSeek)
  * uploaded XLSX                    -> execution_mode="local" (no external call)

The legacy five-step report workspace remains available in workbench_app.py;
this app is the recommended first screen (see scripts/start_dashboard.ps1).
"""
import hashlib

import pandas as pd
import streamlit as st

from copilot.dashboard.client import DashboardClient, DashboardError
from copilot.dashboard.import_panel import IMPORTANT_CODES, issue_table
from copilot.dashboard.presentation import BAND_LABELS, STAFF_MAJOR_LABELS, term_text
from copilot.data.synthetic_college import SyntheticOptions

TOOL_LABELS = {"get_dataset_overview": "数据集概览", "get_score_summary": "课程成绩统计",
               "compare_classes": "班级成绩比较", "get_catalog": "课程与班级目录"}

PREVIEW_STATUS_LABELS = {"graded": "有成绩", "missing": "缺考/缺失", "exempt": "免修"}

DEFAULT_NEXT = "请换一个更明确的问题，或在左侧“数据来源”重新确认数据集后重试。"

NEXT_STEPS = {
    "no_dataset": "请先在左侧选择并确认一份数据集，然后才能提问。",
    "dataset_changed": "数据版本已经变化，旧对话已清空。请在左侧重新确认数据集，再重新提问。",
    "agent_privacy_block": "当前上传的成绩单只能本地分析。若需要体验 DeepSeek，请切换到合成示例数据。",
    "question_privacy_block": "请只提出聚合统计问题，不要写姓名、学号或个人成绩。",
    "intent_mismatch": "系统拦截了一次与问题不一致的统计。请把课程名或课程编号写完整后重试。",
    "unknown_entity": "所选课程、班级或学期不在当前数据集中，请从上方示例问题或目录查询开始。",
    "llm_configuration_error": "这是后端 AI 服务配置问题，不是你的问题。请检查服务窗口；上传数据仍可本地分析。",
    "llm_authentication_error": "DeepSeek 拒绝了后端当前使用的 key。请在后端窗口运行 .\\scripts\\check_deepseek_key.ps1 验证，必要时换新 key 并用 -ForcePrompt 重启；上传数据仍可本地分析。",
    "llm_insufficient_balance": "AI 服务账户暂时不可用。请改用上传数据的本地分析，或稍后再试。",
    "llm_timeout": "AI 服务响应超时，请稍后重试；已确认的数据不会丢失。",
    "llm_connection_error": "暂时连不上 AI 服务，请稍后重试；上传数据仍可本地分析。",
    "llm_rate_limited": "AI 服务繁忙，请稍后再试。",
    "agent_timeout": "本次分析超时，请把问题缩小到一门课程或一个班级。",
    "connection": "请确认后端分析服务已经启动，然后点击“重试连接”。",
    "timeout": "本次处理时间较长，请稍后重试；已确认的数据仍会保留。",
}

SUPPORTED = (
    ("① 数据集概览", "学生、班级、课程和成绩记录的整体规模"),
    ("② 单课程成绩分析", "平均分、及格率、不及格率与成绩分布"),
    ("③ 班级间成绩比较", "同一课程、同一学期下多个班级的统计对比"),
    ("④ 课程及班级目录查询", "当前数据集中实际存在的课程和班级"),
)


# --------------------------------------------------------------------------
# Session state: a small, explicit chat context (no hidden multi-turn memory)
# --------------------------------------------------------------------------

def _state():
    if "chat" not in st.session_state:
        st.session_state.chat = {"messages": [], "dataset": None, "scope": None,
                                 "catalog": None, "examples": None, "notice": None}
    return st.session_state.chat


def _reset(state):
    state["messages"] = []
    state["scope"] = None
    state["catalog"] = None
    state["examples"] = None
    for key in ("pending_question", "chat_syn_preview", "chat_syn_options",
                "chat_upload_preview", "chat_upload_name", "chat_upload_bytes", "chat_upload_digest"):
        st.session_state.pop(key, None)


def _activate(state, dataset):
    _reset(state)
    state["dataset"] = dataset
    state["notice"] = None


def _mode(state):
    return "grounded" if state["dataset"].source == "generated" else "local"


def _mode_label(state):
    return ("DeepSeek AI 分析（合成数据）" if state["dataset"].source == "generated"
            else "本地规则分析（上传数据）")


def _observe_dataset(client, state):
    """Invalidate chat history when the server's active version changes."""
    try:
        info = client.dataset()
    except DashboardError as exc:
        if exc.code != "no_dataset":
            raise
        info = None
    if state["dataset"] is not None and (info is None or info.dataset_version != state["dataset"].dataset_version):
        _reset(state)
        state["dataset"] = None
        state["notice"] = "服务端的数据版本已经变化，之前的对话和范围已清空。请重新确认数据集后再提问。"
    return info


# --------------------------------------------------------------------------
# Deterministic Chinese rendering of verified evidence (never model wording)
# --------------------------------------------------------------------------

def _num(value):
    return "—" if value is None else str(value)


def _scope_cn(scope, labels=None):
    parts = []
    if scope.get("term"):
        parts.append(term_text(scope["term"]))
    if scope.get("course_id"):
        identifier = scope["course_id"]
        name = (labels or {}).get(identifier)
        parts.append(f"课程 {name}" if name else f"课程 {identifier}" if len(identifier) <= 12 else "所选课程")
    if scope.get("class_id"):
        parts.append(f"班级 {scope['class_id']}")
    if scope.get("grade_level") is not None:
        parts.append(f"{scope['grade_level']}级")
    return " · ".join(parts) or "当前范围"


def _bins_cn(distribution):
    return "、".join(f"{BAND_LABELS.get(row['label'], row['label'])} {row['count']}条"
                    for row in distribution) or "暂无分段数据"


def _statistics_cn(data, labels=None):
    context = _scope_cn(data.get("scope", {}), labels)
    counts = (f"共 {data.get('enrollment_count', 0)} 条成绩记录，其中缺考/缺失 {data.get('missing_count', 0)} 条、"
              f"免修 {data.get('exempt_count', 0)} 条。")
    if not data.get("graded_count"):
        return f"{context}：没有有效成绩，平均分与及格率无法计算（空白不按零分处理）。{counts}"
    return (f"{context}：{counts}有效成绩 {data['graded_count']} 条；"
            f"平均分 {_num(data.get('average_score'))} 分，学分加权平均分 {_num(data.get('weighted_average_score'))} 分；"
            f"及格率 {_num(data.get('pass_rate_percent'))}%（{data.get('passed_count', 0)}/{data['graded_count']}），"
            f"不及格 {data.get('failed_count', 0)} 条。成绩分布：{_bins_cn(data.get('distribution', []))}。")


def _evidence_cn(result, catalog=None):
    # Show human course names in the conclusion. Internal long IDs stay inside
    # the expandable evidence panel, not in the first thing a staff member reads.
    labels = {row.course_id: row.course_name for row in catalog.courses} if catalog else {}
    lines = []
    for item in result.evidence:
        data = item.data
        if item.tool == "get_score_summary":
            lines.append(_statistics_cn(data, labels))
        elif item.tool == "get_dataset_overview":
            counts = data.get("counts", {})
            lines.append(f"当前数据集包含 {counts.get('students', 0)} 名学生、{counts.get('classes', 0)} 个班级、"
                         f"{counts.get('courses', 0)} 门课程，共 {counts.get('enrollments', 0)} 条选课记录。"
                         f"每名学生每学期选课 {data.get('minimum_courses_per_student', 0)}–"
                         f"{data.get('maximum_courses_per_student', 0)} 门。")
        elif item.tool == "compare_classes":
            lines.append(f"同一课程 {data.get('course_id', '—')} · {term_text(data['term']) if data.get('term') else '当前学期'} 的班级比较：")
            for row in data.get("results", []):
                lines.append(f"- {_statistics_cn(row, labels)}")
            leaders = data.get("highest_mean_class_ids") or []
            if len(leaders) == 1:
                lines.append(f"按未四舍五入的精确均分比较，{leaders[0]} 的平均分最高。")
            elif leaders:
                lines.append("按未四舍五入的精确均分比较，" + "、".join(leaders) + " 并列最高。")
        elif item.tool == "get_catalog":
            courses, classes = data.get("courses", []), data.get("classes", [])
            lines.append(f"当前目录共有 {len(courses)} 门课程、{len(classes)} 个班级。")
            if courses:
                names = "、".join(f"{row['course_name']}（{row['course_id']}）" for row in courses[:12])
                lines.append("课程：" + names + ("…" if len(courses) > 12 else ""))
            if classes:
                names = "、".join(str(row.get("class_label") or row["class_id"]) for row in classes[:12])
                lines.append("班级：" + names + ("…" if len(classes) > 12 else ""))
    return "\n\n".join(lines) if lines else "本次没有产生可核验的统计证据，请调整问题后重试。"


def _render_charts(result):
    for item in result.evidence:
        if item.tool == "get_score_summary" and item.data.get("distribution"):
            rows = [{"分数段": BAND_LABELS.get(row["label"], row["label"]), "人数": row["count"]}
                    for row in item.data["distribution"]]
            st.caption("成绩分布（仅统计有分数的成绩记录）")
            st.bar_chart(pd.DataFrame(rows).set_index("分数段"))
        elif item.tool == "compare_classes" and item.data.get("results"):
            rows = [{"班级": row.get("scope", {}).get("class_id", "—"),
                     "平均分": float(row["average_score"]) if row.get("average_score") is not None else 0.0}
                    for row in item.data["results"]]
            st.caption("各班平均分（使用未四舍五入的精确均分）")
            st.bar_chart(pd.DataFrame(rows).set_index("班级"))


def _answer_mode_label(result):
    if result.status != "completed":
        return "本地业务语义解析（未调用模型）"
    if result.provider == "local":
        return "本地规则分析（上传数据）"
    if result.simulated:
        return "离线 Mock 演示（合成数据，未调用 DeepSeek）"
    return "DeepSeek AI 分析（合成数据）"


def _render_message(message, state):
    if message["error"] is not None:
        error = message["error"]
        st.error(str(error))
        st.info("下一步：" + NEXT_STEPS.get(error.code, DEFAULT_NEXT))
        return
    result = message["response"]
    st.caption(f"分析方式：{_answer_mode_label(result)}")
    if result.status == "needs_clarification":
        st.info(result.answer)
        st.write("下一步：请补充课程、班级或学期后重新提问，也可以直接点击上方的示例问题。")
        return
    if result.status == "unsupported":
        st.warning(result.answer)
        st.write("下一步：目前只支持数据概览、单课程成绩分析、同课程班级比较和课程/班级目录查询，请重新编辑问题。")
        return
    if state["dataset"] is not None and result.dataset_version != state["dataset"].dataset_version:
        st.warning("这条回答来自不同的数据版本，数值不能与当前数据集直接比较。请重新提问。")
    st.markdown("**统计结论**")
    st.write(_evidence_cn(result, state["catalog"]))
    st.caption("以上中文结论由页面依据后端计算证据中的数值生成，不包含模型措辞，也不推断成绩差异的原因。")
    _render_charts(result)
    with st.expander("计算证据与技术详情"):
        st.caption(f"数据版本：{result.dataset_version[:12]}…（回答与证据版本一致）")
        if not result.evidence:
            st.info("本次没有统计证据。")
        for item in result.evidence:
            st.markdown(f"**证据 {item.evidence_id} · {TOOL_LABELS.get(item.tool, item.tool)}**")
            st.json({"调用参数": item.arguments, "计算结果": item.data})
        with st.expander("执行记录（供排查）"):
            st.json({"request_id": result.request_id, "provider": result.provider, "model": result.model,
                     "simulated": result.simulated, "answer_mode": result.answer_mode,
                     "model_turns": result.model_turns, "dataset_version": result.dataset_version,
                     "tool_calls": [trace.model_dump(mode="json") for trace in result.tool_calls],
                     "server_answer": result.answer})


# --------------------------------------------------------------------------
# Data source panels (sidebar): server-derived synthetic vs uploaded XLSX
# --------------------------------------------------------------------------

def _show_error(exc):
    st.error(str(exc))
    st.info("下一步：" + NEXT_STEPS.get(exc.code, DEFAULT_NEXT))


def _preview_overview(state):
    """Render the preview catalog and de-identified rows in the wide main area.

    The sidebar is too narrow for tables. The preview API returns only
    aggregates, a detected course catalog and at most eight de-identified rows,
    so no student identity or full grade matrix can ever appear here.
    """
    choice = st.session_state.get("chat_source_choice", "")
    key = "chat_upload_preview" if choice.startswith("上传") else "chat_syn_preview"
    preview = st.session_state.get(key)
    if preview is None:
        return
    counts = preview.counts
    st.markdown("### 数据预览（确认前核对）")
    st.write(f"预览：{counts.get('students', 0)} 名学生 · {counts.get('classes', 0)} 个班级 · "
             f"{counts.get('enrollments', 0)} 条成绩记录。")
    summary = []
    if preview.terms:
        summary.append("学期：" + "、".join(term_text(term) for term in preview.terms[:3]))
    if preview.classes:
        summary.append("班级：" + "、".join(str(label) for label in preview.classes[:12]))
    if summary:
        st.caption(" · ".join(summary))
    left, right = st.columns(2)
    with left:
        st.markdown("**课程清单（%d 门）**" % len(preview.courses))
        if preview.courses:
            st.dataframe([{"课程": c.name, "学期": c.term or "—", "学分": c.credits or "—",
                           "处理": c.reason if c.excluded else "保留"} for c in preview.courses],
                         hide_index=True, width="stretch")
    with right:
        st.markdown("**成绩记录样本（前 %d 条，已脱敏）**" % len(preview.sample))
        if preview.sample:
            st.dataframe([{"学生": row.get("student", "—"), "课程": row.get("course", "—"),
                           "状态": PREVIEW_STATUS_LABELS.get(row.get("status"), row.get("status", "—")),
                           "分数": row.get("score", "—")} for row in preview.sample],
                         hide_index=True, width="stretch")
    st.caption("预览按设计只返回汇总、课程目录和最多 8 条脱敏样本，不返回完整成绩明细，"
               "避免把学生行显示在页面上。样本中的学生列是脱敏编号，不含姓名或学号；确认后可提问查看统计结果。")
    st.info("核对无误后，请在左侧「数据来源」勾选确认框并点击确认按钮；确认后即可提问。")


def _synthetic_panel(client, state):
    st.caption("由服务端生成虚构成绩单；页面只提交生成参数，不上传文件，也不声明数据来源。")
    with st.expander("生成设置（可选）"):
        classes = st.number_input("每个专业的班级数", 1, 4, 2, key="chat_syn_classes")
        students = st.number_input("每班学生人数", 2, 50, 6, key="chat_syn_students")
        courses = st.number_input("课程数", 3, 12, 6, key="chat_syn_courses")
        seed = st.number_input("数据编号", 0, 2**32 - 1, 20261111, key="chat_syn_seed")
    options = SyntheticOptions(classes_per_major=int(classes), students_per_class=int(students),
                               course_count=int(courses), seed=int(seed))
    if st.button("预览合成示例数据", key="chat_syn_preview_button", type="primary"):
        try:
            with st.spinner("正在由服务端生成并核对示例数据…"):
                st.session_state.chat_syn_preview = client.synthetic_preview(options)
            st.session_state.chat_syn_options = options
        except DashboardError as exc:
            _show_error(exc)
    preview = st.session_state.get("chat_syn_preview")
    if preview is None:
        return
    counts = preview.counts
    st.write(f"预览：{counts.get('students', 0)} 名学生 · {counts.get('classes', 0)} 个班级 · "
             f"{counts.get('enrollments', 0)} 条成绩记录。")
    st.caption("课程清单与脱敏样本已在主区展示，请核对后在下方勾选确认。")
    errors = [item for item in preview.issues if item.severity == "error"]
    important = [item for item in preview.issues if item.severity != "error" and item.code in IMPORTANT_CODES]
    if errors:
        st.error("这份示例还有阻止导入的问题，请调整生成设置后重新预览。")
        st.dataframe(issue_table(errors), hide_index=True)
    if important:
        with st.expander("需要核对的异常说明"):
            st.dataframe(issue_table(important), hide_index=True)
    if not preview.can_import:
        st.warning("这份预览暂不能导入，请调整设置后重新预览。")
        return
    st.caption("全部姓名、学号与成绩均为虚构。确认后可使用 DeepSeek AI 分析。")
    checked = st.checkbox("我已核对，确认使用这份合成示例数据", key="chat_syn_check")
    if st.button("确认并启用 DeepSeek 分析", key="chat_syn_confirm", disabled=not checked, type="primary"):
        try:
            with st.spinner("正在激活合成数据集…"):
                result = client.synthetic_commit(st.session_state.chat_syn_options, preview.preview_token)
            if result.dataset.source != "generated":
                raise DashboardError("agent_privacy_block", "数据来源不是服务端生成的合成数据，已拒绝启用 AI 分析。")
            _activate(state, result.dataset)
            st.rerun()
        except DashboardError as exc:
            _show_error(exc)


def _upload_panel(client, state):
    st.caption("复用现有 Excel Adapter：上传后先核对关键异常，由你明确确认才会导入。")
    uploaded = st.file_uploader("选择学院成绩单（.xlsx）", type=["xlsx"], key="chat_upload")
    if uploaded is not None:
        content = uploaded.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        if st.session_state.get("chat_upload_digest") != digest:
            try:
                with st.spinner("正在识别课程并检查异常…"):
                    st.session_state.chat_upload_preview = client.college_preview(uploaded.name, content)
                st.session_state.chat_upload_digest = digest
                st.session_state.chat_upload_name = uploaded.name
                st.session_state.chat_upload_bytes = content
            except DashboardError as exc:
                _show_error(exc)
    preview = st.session_state.get("chat_upload_preview")
    if preview is None:
        return
    counts = preview.counts
    st.write(f"预览：{counts.get('students', 0)} 名学生 · {counts.get('classes', 0)} 个班级 · "
             f"{counts.get('enrollments', 0)} 条成绩记录。")
    st.caption("课程清单与脱敏样本已在主区展示，请核对后在下方勾选确认。")
    errors = [item for item in preview.issues if item.severity == "error"]
    important = [item for item in preview.issues if item.severity != "error" and item.code in IMPORTANT_CODES]
    if errors:
        st.error("这份成绩单还有需要修正的问题，暂时不能导入。")
        st.dataframe(issue_table(errors), hide_index=True)
    if important:
        with st.expander("需要人工核对的异常与排除项", expanded=True):
            st.dataframe(issue_table(important), hide_index=True)
    if not preview.can_import:
        st.warning("请先处理上述阻止项，或修正原表后重新上传。")
        return
    st.caption("原件只保留在本机。上传数据只会进行本地规则分析，不会发送给 DeepSeek。")
    checked = st.checkbox("我已核对清洗结果与异常项，确认导入", key="chat_upload_check")
    if st.button("确认导入并启用本地分析", key="chat_upload_confirm", disabled=not checked, type="primary"):
        try:
            with st.spinner("正在确认并导入…"):
                result = client.college_commit(st.session_state.chat_upload_name,
                                               st.session_state.chat_upload_bytes,
                                               preview_token=preview.preview_token)
            _activate(state, result.dataset)
            st.rerun()
        except DashboardError as exc:
            _show_error(exc)


def _scope_panel(state):
    st.divider()
    st.subheader("会话范围")
    scope = state["scope"]
    if scope is None:
        st.caption("尚未设定范围。每次提问请在问题里写明课程、班级或学期。")
    else:
        values = scope.model_dump(exclude_none=True)
        parts = []
        if values.get("term"):
            parts.append(term_text(values["term"]))
        if values.get("grade_level") is not None:
            parts.append(f"{values['grade_level']}级")
        if values.get("major"):
            parts.append(STAFF_MAJOR_LABELS.get(values["major"], values["major"]))
        if values.get("class_id"):
            parts.append(f"班级 {values['class_id']}")
        if values.get("course_id"):
            parts.append(f"课程 {values['course_id']}")
        st.caption("上一题确认的范围：" + (" · ".join(parts) or "全部范围"))
    reuse = st.checkbox("后续问题沿用上一题的范围", key="chat_reuse_scope",
                        help="开启后，未明确写出的学期等条件会沿用上次结果；你在问题里明确写出的条件始终优先。")
    if st.button("清除会话范围", key="chat_clear_scope"):
        state["scope"] = None
        st.rerun()
    return reuse


def _data_source(client, state, info):
    with st.sidebar:
        st.header("数据来源")
        if state["dataset"] is not None:
            dataset = state["dataset"]
            st.success("数据集已确认")
            st.write("来源：" + ("合成示例数据（全部虚构）" if dataset.source == "generated" else "上传的 Excel 成绩单"))
            st.write("当前分析方式：**" + _mode_label(state) + "**")
            st.write(f"学生 {dataset.counts.get('students', 0)} 人 · 课程 {dataset.counts.get('courses', 0)} 门 · "
                     f"班级 {dataset.counts.get('classes', 0)} 个")
            if dataset.terms:
                st.write("学期：" + "、".join(term_text(term) for term in dataset.terms[:3]))
            with st.expander("版本与来源详情"):
                st.json({"dataset_version": dataset.dataset_version, "source": dataset.source,
                         "external_llm_allowed": dataset.external_llm_allowed, "counts": dataset.counts})
            st.caption("切换数据来源会清空当前对话，避免混用不同版本的结果。")
            if st.button("重新选择数据", key="chat_reset_data"):
                _reset(state)
                state["dataset"] = None
                st.rerun()
        else:
            st.info("还没有确认数据集。请先选择来源并确认，之后才能提问。")
            if info is not None:
                st.caption("服务端已有数据，但需要你在本页明确确认后才会启用（不会默认激活）。")
            choice = st.radio("选择数据来源", ["合成示例数据（可体验 DeepSeek）", "上传 Excel 成绩单（仅本地分析）"],
                              key="chat_source_choice")
            if choice.startswith("合成"):
                _synthetic_panel(client, state)
            else:
                _upload_panel(client, state)
        return _scope_panel(state)


# --------------------------------------------------------------------------
# Example questions are verified against the live catalog before being shown
# --------------------------------------------------------------------------

def _candidate_questions(catalog):
    """Prefer readable Chinese course names; never show internal long IDs."""
    questions = ["当前数据集的概览是什么？", "查询当前数据集有哪些课程"]
    courses, classes = catalog.courses, catalog.classes
    if courses:
        first = courses[0]
        questions.append(f"分析{first.course_name}的平均分和及格率")
        if len(classes) >= 2:
            questions.append(f"比较 {classes[0].class_id} 和 {classes[1].class_id} 的{first.course_name}成绩")
        if len(courses) > 1:
            questions.append(f"分析{courses[-1].course_name}的成绩分布")
    if catalog.majors:
        label = STAFF_MAJOR_LABELS.get(catalog.majors[0], catalog.majors[0])
        questions.append(f"查询{label}有哪些课程")
    return questions


def _examples(client, state):
    if state["examples"] is not None:
        return state["examples"]
    try:
        catalog = client.catalog()
    except DashboardError as exc:
        _show_error(exc)
        state["examples"] = []
        return state["examples"]
    state["catalog"] = catalog
    verified = []
    for question in _candidate_questions(catalog):
        try:
            intent = client.resolve(question, execution_mode=_mode(state),
                                    expected_version=state["dataset"].dataset_version)
        except DashboardError:
            continue
        if intent.status == "ready":
            verified.append(question)
        if len(verified) >= 6:
            break
    state["examples"] = verified
    return verified


def _pick_question(question):
    st.session_state.pending_question = question


def _ask(client, state, question, reuse):
    mode = _mode(state)
    scope = state["scope"] if reuse else None
    version = state["dataset"].dataset_version
    try:
        with st.spinner("正在分析问题、执行统计并核验结果…"):
            result = client.query(question, execution_mode=mode, scope=scope, expected_version=version)
        state["messages"].append({"question": question, "response": result, "error": None, "mode": mode})
        if result.resolved_scope is not None:
            state["scope"] = result.resolved_scope
    except DashboardError as exc:
        state["messages"].append({"question": question, "response": None, "error": exc, "mode": mode})
        if exc.code in {"dataset_changed", "no_dataset"}:
            # Keep the error visible and keep the old history marked as stale,
            # but stop accepting new questions until the data is confirmed again.
            state["dataset"] = None
            state["scope"] = None
            state["examples"] = None
            state["notice"] = "数据已经变化，分析入口已暂停。请在左侧重新确认数据集后再提问。"


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

def _style():
    st.markdown("<style>"
                ".block-container{max-width:1120px;padding-top:2rem;}"
                "[data-testid=stCaptionContainer] p{font-size:0.95rem;color:inherit;}"
                "[data-testid=stChatMessage] p{font-size:1.02rem;line-height:1.7;}"
                "</style>", unsafe_allow_html=True)


def _intro(state):
    st.title("Academic Analytics Copilot — AI 学业数据分析助手")
    st.write("用中文提出学业统计问题。系统先核对数据目录，再由可信统计工具计算，最后给出能查到计算证据的结果。")
    with st.container(border=True):
        st.markdown("**目前支持的分析范围**")
        for title, detail in SUPPORTED:
            st.write(f"{title}：{detail}")
        st.caption("暂不支持个人成绩查询、名单、排名、预测或成绩差异的原因分析。"
                   "统计数值全部来自后端统计引擎，AI 只负责选择工具与组织语言，不能自行写入数字。")
    if state["dataset"] is None:
        st.info("还没有确认数据集。请在左侧「数据来源」选择合成示例数据或上传成绩单，确认后才能开始提问。")
    else:
        st.success("当前：" + _mode_label(state) + "。每条回答都会标注它实际使用的分析方式。")
        st.caption("页面保留聊天记录展示；每次提问都会独立解析，不会自动引用上一轮对话内容。"
                   "需要复用范围时，请在左侧勾选「后续问题沿用上一题的范围」。")


def main(client_factory=None):
    st.set_page_config(page_title="Academic Analytics Copilot — AI 学业数据分析助手",
                       page_icon="🎓", layout="wide")
    _style()
    state = _state()
    try:
        client = (client_factory or DashboardClient)()
        client.health()
        info = _observe_dataset(client, state)
    except DashboardError as exc:
        _show_error(exc)
        with st.expander("启动与排障"):
            st.write("请保持后端与页面两个服务窗口都开启，然后点击「重试连接」。")
            st.code("& '.\\.venv\\Scripts\\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000",
                    language="powershell")
        if st.button("重试连接", key="chat_retry"):
            st.rerun()
        _intro(state)
        return

    reuse = _data_source(client, state, info)
    _intro(state)
    if state["dataset"] is None:
        _preview_overview(state)
    if state["notice"]:
        st.warning(state["notice"])
        state["notice"] = None

    if state["dataset"] is not None:
        examples = _examples(client, state)
        if examples:
            st.markdown("**可以直接试试这些问题**（均按当前数据集目录校验过）")
            columns = st.columns(2)
            for index, question in enumerate(examples):
                columns[index % 2].button(question, key=f"chat_example_{index}",
                                          on_click=_pick_question, args=(question,), width="stretch")
        else:
            st.caption("当前数据集暂未生成可执行的示例问题，可以直接在下方输入。")

    pending = st.session_state.pop("pending_question", None)
    submitted = st.chat_input("用中文输入学业分析问题…", key="chat_question",
                              disabled=state["dataset"] is None)
    question = submitted or pending
    if question and question.strip() and state["dataset"] is not None:
        _ask(client, state, question.strip(), reuse)

    for index, message in enumerate(state["messages"]):
        with st.chat_message("user"):
            st.write(message["question"])
        with st.chat_message("assistant"):
            _render_message(message, state)
