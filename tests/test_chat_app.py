"""Phase 3B Chat-first UI acceptance, driven by Streamlit AppTest.

The page talks to a real FastAPI app through DashboardClient + MockTransport.
The LLM provider is forced to Mock by tests/conftest.py; no paid call is made.
No Streamlit server or browser is required for these reproducible UI checks.
"""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from copilot.api import create_app
from copilot.dashboard.client import DashboardClient, DashboardError
from copilot.storage import Database
from tests.college_fixture import college_workbook

ENTRY = Path(__file__).resolve().parents[1] / "chat_app.py"
SYNTHETIC = "合成示例数据（可体验 DeepSeek）"
UPLOAD = "上传 Excel 成绩单（仅本地分析）"


class ChatEnv:
    def __init__(self, client, store, calls, entry):
        self.client, self.store, self.calls, self.entry = client, store, calls, entry

    def page(self, timeout=90):
        return AppTest.from_file(str(self.entry), default_timeout=timeout).run()

    def queries(self):
        return [body for method, path, body, _ in self.calls if path == "/api/agent/query"]

    def paths(self):
        return [path for _, path, _, _ in self.calls]


@pytest.fixture
def chat_env(tmp_path, monkeypatch):
    store = Database(tmp_path / "chat-ui.sqlite3")
    calls = []
    with TestClient(create_app(store.path, bootstrap_demo=False)) as api:
        def transport(request):
            body = None
            if request.content and "application/json" in request.headers.get("content-type", ""):
                try:
                    body = json.loads(request.content)
                except (ValueError, UnicodeDecodeError):
                    body = None
            calls.append((request.method, request.url.path, body, dict(request.url.params)))
            response = api.request(request.method, str(request.url), content=request.content, headers=request.headers)
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)
        client = DashboardClient("http://testserver", transport=httpx.MockTransport(transport))
        monkeypatch.setattr("copilot.dashboard.chat.DashboardClient", lambda: client)
        yield ChatEnv(client, store, calls, ENTRY)


def control(page, group, key):
    return next(item for item in group if item.key == key)


def examples(page):
    return [item for item in page.button if item.key and item.key.startswith("chat_example_")]


def activate_synthetic(page):
    control(page, page.button, "chat_syn_preview_button").click().run()
    control(page, page.checkbox, "chat_syn_check").check().run()
    control(page, page.button, "chat_syn_confirm").click().run()
    return page


def submit(page, question):
    page.chat_input[0].set_value(question).run()
    return page


# --- 1. First open is understandable and does not auto-activate a dataset ---

def test_first_open_explains_scope_and_disables_input(chat_env):
    page = chat_env.page()
    assert not page.exception, [item.message for item in page.exception]
    assert any("Academic Analytics Copilot" in item.value for item in page.title)
    for label in ("数据集概览", "单课程成绩分析", "班级间成绩比较", "课程及班级目录查询"):
        assert any(label in item.value for item in page.markdown)
    assert len(page.chat_input) == 1 and page.chat_input[0].proto.disabled is True
    assert not chat_env.store.has_dataset()
    assert set(chat_env.paths()) == {"/health", "/api/dataset"}
    assert not any(path.startswith("/api/datasets/") for path in chat_env.paths())


# --- 1b. A confirmed upload stays active until the operator changes it ---

def test_confirmed_upload_is_restored_on_a_fresh_page(chat_env, tiny_dataset):
    chat_env.store.replace(tiny_dataset, source="uploaded_local_only")
    page = chat_env.page()
    assert not page.exception, [item.message for item in page.exception]
    assert page.session_state.chat["dataset"] is not None
    assert page.chat_input[0].proto.disabled is False
    assert any("已自动沿用" in item.value for item in page.info)
    assert any("本地规则分析（上传数据）" in item.value for item in page.markdown)
    # It must still be an explicit, visible provenance decision, not a silent one.
    assert any("重新选择数据" in item.label for item in page.button)
    assert set(chat_env.paths()) == {"/health", "/api/dataset", "/api/reports/catalog", "/api/agent/resolve"}


def test_no_session_scope_controls_and_user_messages_align_right(chat_env):
    page = chat_env.page()
    assert not any("会话范围" in item.value for item in list(page.markdown) + list(page.caption))
    assert not any(item.key in {"chat_reuse_scope", "chat_clear_scope"} for item in page.checkbox)
    assert any("row-reverse" in item.value for item in page.markdown), "user bubbles must be right-aligned"


# --- 2. Synthetic activation goes through server preview + explicit confirm ---

def test_synthetic_activation_requires_preview_and_checkbox(chat_env):
    page = chat_env.page()
    control(page, page.button, "chat_syn_preview_button").click().run()
    assert not page.exception
    assert any("预览：" in item.value and "名学生" in item.value for item in page.markdown)
    assert "/api/datasets/synthetic/preview" in chat_env.paths()
    # The preview must show the full, coordinate-located review instead of a
    # handful of re-numbered aliases that cannot be checked against the XLSX.
    assert any("课程清单" in item.value for item in page.markdown)
    assert any("需要核对的问题" in item.value for item in page.markdown)
    assert any("原表单元格核对" in item.value for item in page.markdown)
    tables = [table.value for table in page.dataframe]
    assert any({"课程", "学分", "列"} <= set(table.columns) for table in tables)
    samples = next(table for table in tables if "解析值" in table.columns)
    assert not samples.empty and {"原表行", "列", "状态", "需修正"} <= set(samples.columns)
    assert all(str(value).isdigit() for value in samples["原表行"])
    assert "学生" not in samples.columns
    assert not chat_env.store.has_dataset()
    assert control(page, page.button, "chat_syn_confirm").disabled
    control(page, page.checkbox, "chat_syn_check").check().run()
    assert not control(page, page.button, "chat_syn_confirm").disabled
    control(page, page.button, "chat_syn_confirm").click().run()
    assert not page.exception, [item.message for item in page.exception]
    assert "/api/datasets/synthetic/commit" in chat_env.paths()
    assert chat_env.store.overview().source == "generated"
    assert chat_env.store.overview().external_llm_allowed is True
    assert any("DeepSeek AI 分析（合成数据）" in item.value for item in page.markdown)
    assert page.chat_input[0].proto.disabled is False


def test_example_questions_are_catalog_verified_and_readable(chat_env):
    page = activate_synthetic(chat_env.page())
    buttons = examples(page)
    assert 4 <= len(buttons) <= 6
    assert all("I0" not in item.label and len(item.label) < 40 for item in buttons)
    assert len([call for call in chat_env.calls if call[1] == "/api/agent/resolve"]) >= len(buttons)
    assert any("有哪些课程" in item.label or "平均分" in item.label for item in buttons)


# --- 3. New UI always uses grounded for synthetic data, with real evidence ---

def test_new_ui_uses_grounded_mode_and_displays_verifiable_evidence(chat_env):
    page = activate_synthetic(chat_env.page())
    examples(page)[0].click().run()
    assert not page.exception, [item.message for item in page.exception]
    bodies = chat_env.queries()
    assert len(bodies) == 1
    assert bodies[0]["execution_mode"] == "grounded"
    assert bodies[0]["expected_version"] == chat_env.store.overview().dataset_version
    assert any("统计结论" in item.value for item in page.markdown)
    assert any("名学生" in item.value or "平均分" in item.value for item in page.markdown)
    assert any(item.label == "计算证据与技术详情" for item in page.expander)
    assert any("离线 Mock 演示" in item.value or "DeepSeek AI 分析" in item.value for item in page.caption)
    messages = page.session_state.chat["messages"]
    assert len(messages) == 1 and messages[0]["response"].status == "completed"
    assert messages[0]["response"].evidence[0].dataset_version == bodies[0]["expected_version"]


def test_chinese_course_and_class_questions_show_statistics_and_charts(chat_env):
    page = activate_synthetic(chat_env.page())
    catalog = chat_env.client.catalog()
    course = catalog.courses[0].course_name
    page = submit(page, f"分析{course}的平均分和及格率")
    assert not page.exception, [item.message for item in page.exception]
    assert any("平均分" in item.value and "及格率" in item.value for item in page.markdown)
    assert any("成绩分布" in item.value for item in page.caption)
    assert page.get("arrow_bar_chart") or any("成绩分布" in item.value for item in page.caption)
    messages = page.session_state.chat["messages"]
    assert messages[-1]["response"].evidence[0].tool == "get_score_summary"
    body = chat_env.queries()[-1]
    assert body["execution_mode"] == "grounded" and body["question"].startswith("分析")
    assert all("uploaded" not in json.dumps(body) for body in chat_env.queries())


def test_class_comparison_question_is_accepted(chat_env):
    page = activate_synthetic(chat_env.page())
    catalog = chat_env.client.catalog()
    course = catalog.courses[0].course_name
    left, right = catalog.classes[0].class_id, catalog.classes[1].class_id
    page = submit(page, f"比较 {left} 和 {right} 的{course}成绩")
    assert not page.exception, [item.message for item in page.exception]
    assert chat_env.queries()[-1]["execution_mode"] == "grounded"
    messages = page.session_state.chat["messages"]
    assert messages[-1]["response"].evidence[0].tool == "compare_classes"
    assert any("班级比较" in item.value or "平均分最高" in item.value for item in page.markdown)


# --- 4. Uploaded XLSX uses local mode only and is labelled honestly ---

def test_upload_review_confirm_then_local_only_queries(chat_env, monkeypatch):
    page = chat_env.page()
    control(page, page.radio, "chat_source_choice").set_value(UPLOAD).run()

    class Upload:
        name = "college.xlsx"

        def getvalue(self):
            return college_workbook()

    monkeypatch.setattr("copilot.dashboard.chat.st.file_uploader", lambda *args, **kwargs: Upload())
    page.run()
    assert not page.exception, [item.message for item in page.exception]
    assert any("预览：" in item.value for item in page.markdown)
    assert any("需要核对的问题" in item.value for item in page.markdown)
    assert any("原表单元格核对" in item.value for item in page.markdown)
    assert any("解析值" in table.value.columns for table in page.dataframe)
    assert "/api/datasets/college/preview" in chat_env.paths()
    assert any("不会发送给 DeepSeek" in item.value for item in list(page.markdown) + list(page.caption))
    assert control(page, page.button, "chat_upload_confirm").disabled
    control(page, page.checkbox, "chat_upload_check").check().run()
    control(page, page.button, "chat_upload_confirm").click().run()
    assert not page.exception, [item.message for item in page.exception]
    assert chat_env.store.overview().source == "uploaded_local_only"
    assert any("本地规则分析（上传数据）" in item.value for item in page.markdown)
    buttons = examples(page)
    assert buttons
    buttons[0].click().run()
    assert not page.exception, [item.message for item in page.exception]
    bodies = chat_env.queries()
    assert bodies and all(body["execution_mode"] == "local" for body in bodies)
    assert not any(body.get("execution_mode") == "grounded" for body in bodies)
    assert any("本地规则分析" in item.value for item in page.caption)
    assert page.session_state.chat["messages"][-1]["response"].provider == "local"


# --- 5. Clarification, unsupported and provider errors are user-facing ---

def test_ambiguous_and_unsupported_questions_get_friendly_guidance(chat_env):
    page = activate_synthetic(chat_env.page())
    page = submit(page, "比较 2 班和 3 班的成绩")
    assert not page.exception, [item.message for item in page.exception]
    assert any("下一步" in item.value and "补充课程" in item.value for item in page.markdown)
    assert page.session_state.chat["messages"][-1]["response"].status == "needs_clarification"
    page = submit(page, "查一下张三的学号和个人成绩")
    assert not page.exception, [item.message for item in page.exception]
    assert any("只支持" in item.value or "支持" in item.value
               for item in list(page.markdown) + list(page.warning))
    assert page.session_state.chat["messages"][-1]["response"].status == "unsupported"
    assert not any("Traceback" in item.value for item in page.error)


def test_llm_configuration_error_is_not_blamed_on_the_user(chat_env, monkeypatch):
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("COPILOT_LLM_API_KEY", raising=False)
    page = activate_synthetic(chat_env.page())
    examples(page)[0].click().run()
    assert not page.exception, [item.message for item in page.exception]
    assert any("后端 AI 服务配置" in item.value for item in page.error)
    assert any("不是你的问题" in item.value for item in page.info)
    assert not any("Traceback" in item.value for item in page.error)


def test_connection_failure_shows_start_command_and_retry(monkeypatch):
    class OfflineClient:
        def health(self):
            raise DashboardError("connection", "暂时无法连接本机分析服务。请确认服务已经启动，再点击重试连接。")

    monkeypatch.setattr("copilot.dashboard.chat.DashboardClient", OfflineClient)
    page = AppTest.from_file(str(ENTRY), default_timeout=30).run()
    assert not page.exception, [item.message for item in page.exception]
    assert any("重试" in item.value for item in page.error)
    assert len(page.code) == 1
    assert any(item.label == "重试连接" for item in page.button)
    assert any("Academic Analytics Copilot" in item.value for item in page.title)


# --- 6. Version changes invalidate chat history and scope ---

def test_dataset_version_switch_clears_chat_history_and_scope(chat_env, tiny_dataset):
    page = activate_synthetic(chat_env.page())
    examples(page)[0].click().run()
    assert len(page.session_state.chat["messages"]) == 1
    chat_env.store.replace(tiny_dataset, source="generated", seed=99)
    page.run()
    assert not page.exception, [item.message for item in page.exception]
    assert page.session_state.chat["dataset"] is None
    assert page.session_state.chat["messages"] == []
    assert page.session_state.chat["scope"] is None
    assert page.chat_input[0].proto.disabled is True
    assert any("版本" in item.value and "清空" in item.value for item in page.warning)


# --- 7. Existing dashboard API contract remains intact ---

def test_original_dashboard_client_contracts_still_hold(chat_env):
    status = chat_env.client.agent_status()
    assert set(status.model_dump()) == {"provider", "model", "simulated", "tools", "max_model_turns",
                                        "max_tool_calls", "request_timeout_seconds", "question_policy", "streaming"}
    info = chat_env.client.dataset() if chat_env.store.has_dataset() else None
    if info is not None:
        assert info.dataset_version == chat_env.store.overview().dataset_version
    # The legacy one-field query contract is exercised in test_phase3b_backend.py.
    assert callable(chat_env.client.query) and callable(chat_env.client.resolve)
