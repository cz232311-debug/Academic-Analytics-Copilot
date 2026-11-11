"""Explicit synthetic workbook generation; activation stays in the import flow."""
import streamlit as st
from pydantic import ValidationError

from copilot.dashboard.presentation import STAFF_MAJOR_LABELS
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook

MAJORS = ("software_engineering", "computer_science", "network_engineering")
PROFILES = {"balanced": "常规分布", "strong": "整体较好", "needs_support": "需要关注"}
SCENARIOS = {"normal": "标准成绩单 · 验证正常导入", "edge_cases": "边界与异常 · 验证空白、缺考和异常提示",
             "conflict": "重复学号冲突 · 验证提交拦截"}


def _open():
    st.session_state.synthetic_generator_open = True


def _close():
    st.session_state.synthetic_generator_open = False


def _next_batch():
    st.session_state.synthetic_seed = (st.session_state.get("synthetic_seed", 20261111) + 1) % (2**32)
    st.session_state.synthetic_generate_requested = True


def show_synthetic_panel(client, state, parse_selected):
    if not st.session_state.get("synthetic_generator_open", False):
        st.button("生成测试成绩单", on_click=_open,
                  help="自定义人数、班级与课程，生成虚构成绩单验证系统。")
        return

    with st.container(border=True):
        # Widget keys disappear when the user leaves Upload. Keep a separate
        # form snapshot so returning from Analysis restores the last settings.
        for key, value in st.session_state.get("synthetic_form_values", {}).items():
            if key not in st.session_state:
                st.session_state[key] = value
        st.subheader("自动生成测试成绩单")
        st.write("自动生成虚构中文姓名、SIM开头的学号、班级、课程和成绩。可下载Excel，也可直接进入现有核对流程。")
        st.write("这些数据只用于功能验证，不能当作真实学院的教学结论。生成或下载不会替换当前数据。")
        top = st.columns(3)
        classes = top[0].number_input("每个专业的班级数", min_value=1, max_value=4, value=2, step=1, key="synthetic_classes")
        students = top[1].number_input("每班学生人数", min_value=1, max_value=50, value=30, step=1, key="synthetic_students")
        courses = top[2].number_input("有效课程数", min_value=3, max_value=12, value=8, step=1, key="synthetic_courses")
        majors = st.multiselect("生成哪些专业", MAJORS, default=["software_engineering"],
                                format_func=STAFF_MAJOR_LABELS.get, key="synthetic_majors")
        dates = st.columns(3)
        cohort = dates[0].number_input("入学年级", min_value=2000, max_value=2099, value=2024, step=1, key="synthetic_cohort")
        year = dates[1].number_input("学年起始年", min_value=2000, max_value=2099, value=2025, step=1,
                                     help="例如2025表示2025–2026学年，不能早于入学年。", key="synthetic_year")
        semester = dates[2].selectbox("学期", [1, 2], format_func=lambda v: f"第{v}学期", key="synthetic_semester")
        choices = st.columns(2)
        profile = choices[0].selectbox("成绩分布", list(PROFILES), format_func=PROFILES.get, key="synthetic_profile")
        scenario = choices[1].selectbox("验证场景", list(SCENARIOS), format_func=SCENARIOS.get, key="synthetic_scenario")
        with st.expander("复现设置与生成规则"):
            seed = st.number_input("数据编号", min_value=0, max_value=2**32 - 1, value=20261111, step=1,
                                   help="相同编号和设置生成相同成绩单；“换一批数据”使用下一个编号。", key="synthetic_seed")
            st.write("分数综合虚构学生的学习水平、班级差异与课程难度生成，范围0–100分，保留两位小数。")
            st.write("边界场景包含0、59.99、60、79.99、80、100，以及空白、缺考、免修、待核验和一列全空课程。")
            st.write("冲突场景会故意重复一个学号且改变班级，用于检查系统是否阻止未经处理的提交。")

        st.session_state.synthetic_form_values = {
            "synthetic_classes": classes, "synthetic_students": students, "synthetic_courses": courses,
            "synthetic_majors": majors, "synthetic_cohort": cohort, "synthetic_year": year,
            "synthetic_semester": semester, "synthetic_profile": profile, "synthetic_scenario": scenario,
            "synthetic_seed": seed,
        }

        options = None
        try:
            options = SyntheticOptions(seed=seed, cohort=cohort, academic_year_start=year, semester=semester,
                majors=tuple(majors), classes_per_major=classes, students_per_class=students,
                course_count=courses, score_profile=profile, scenario=scenario)
        except ValidationError:
            st.error("请检查设置：至少选一个专业，学年不能早于入学年；边界场景至少需要2名学生，且人数×课程数不少于10。")

        ready = (st.session_state.get("synthetic_result") is not None and options is not None
                 and options == st.session_state.get("synthetic_generated_options"))
        generate = st.button("生成成绩单", type="secondary" if ready else "primary", disabled=options is None)
        requested = st.session_state.pop("synthetic_generate_requested", False)
        if (generate or requested) and options is not None:
            with st.spinner("正在生成虚构学生与课程成绩…"):
                generated = generate_college_workbook(options)
            st.session_state.synthetic_result = generated
            st.session_state.synthetic_generated_options = options
            st.rerun()

        result = st.session_state.get("synthetic_result")
        if result is not None:
            snapshot = st.session_state.synthetic_generated_options
            matches = options is not None and options == snapshot
            counts = result.summary["counts"]
            st.success(f"已生成：{counts['students']}名虚构学生、{counts['classes']}个班、{counts['courses']}门有效课程。")
            st.write(f"这份文件：{snapshot.cohort}级 · {snapshot.academic_year_start}–{snapshot.academic_year_start + 1}学年"
                     f"第{snapshot.semester}学期 · {SCENARIOS[snapshot.scenario]} · 数据编号{snapshot.seed}")
            if snapshot.scenario == "edge_cases":
                st.info("另含一列全空课程。核对时应提示跳过，未知状态也应出现提醒；空白不能算作零分。")
            elif snapshot.scenario == "conflict":
                st.warning("这份文件故意含重复学号冲突。进入核对后，系统应先阻止提交。")
            if not matches:
                st.warning("设置已改变，下面仍是上一份文件。请重新生成，再下载或用于核对。")
            st.download_button("下载合成成绩单（XLSX）", result.content, file_name=result.filename,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               disabled=not matches, on_click="ignore")
            st.button("换一批数据", on_click=_next_batch, disabled=options is None)
            if st.button("使用这份数据进行核对", disabled=not matches, type="primary" if matches else "secondary") and matches:
                state.select_upload(result.filename, result.content, source="example", force=True)
                st.session_state.college_upload_epoch = st.session_state.get("college_upload_epoch", 0) + 1
                parse_selected(client, state)
                st.rerun()
            st.write("下一步仍需核对并勾选确认，才会替换当前学生和成绩。导入后姓名与学号使用系统内部别名，外部AI仍禁用。")
        st.button("收起生成器", on_click=_close)
