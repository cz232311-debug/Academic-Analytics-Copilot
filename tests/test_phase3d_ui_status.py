"""Phase 3D: the UI must never imply a live model when the backend is offline.

All provider variants here are local fixtures. `LiveLikeProvider` declares
simulated=False so /api/agent/status and AgentResponse look like a configured
DeepSeek profile, but its replies are scripted in-process: zero network calls,
zero paid API calls, no credentials and no real student records.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from copilot.agent.models import ModelMessage, ModelTurn, Usage
from copilot.api import create_app
from copilot.dashboard.chat import STATE_LEGEND, _answer_mode_label
from copilot.dashboard.client import DashboardClient
from copilot.storage import Database
from tests.test_chat_app import ChatEnv, ENTRY, activate_synthetic, chat_env, control, submit  # noqa: F401
from tests.test_phase3c_analysis import valid_analysis


class LiveLikeProvider:
    """Offline stand-in for a configured real provider; no HTTP is performed."""

    name, model, simulated = "deepseek", "deepseek-flash", False

    def __init__(self, analysis=None):
        self.analysis = valid_analysis() if analysis is None else analysis
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append(messages)
        if messages[-1]["role"] == "tool":
            return ModelTurn(
                message=ModelMessage(role="assistant", content=json.dumps(
                    {"status": "completed", "evidence_ids": ["r1"], "analysis": self.analysis})),
                usage=Usage(prompt_tokens=1200, completion_tokens=180, total_tokens=1380))
        plan = json.loads(messages[-1]["content"].removeprefix("ACADEMIC_INTENT "))
        return ModelTurn(
            message=ModelMessage.model_validate({"role": "assistant", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": plan["tool"], "arguments": json.dumps(plan["arguments"])}}]}),
            usage=Usage(prompt_tokens=900, completion_tokens=60, total_tokens=960))


@pytest.fixture
def live_like_env(tmp_path, monkeypatch):
    store = Database(tmp_path / "phase3d-status.sqlite3")
    calls, provider = [], LiveLikeProvider()
    with TestClient(create_app(store.path, bootstrap_demo=False, agent_provider=provider)) as api:
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
        yield ChatEnv(client, store, calls, ENTRY), provider


def text(page, group):
    return "\n".join(item.value for item in group)


# --- 1. The status request is only made when a model could actually be used ---

def test_first_open_does_not_query_agent_status(chat_env):
    page = chat_env.page()
    assert not page.exception
    assert set(chat_env.paths()) == {"/health", "/api/dataset"}
    assert "/api/agent/status" not in chat_env.paths()


def test_uploaded_dataset_never_queries_agent_status(chat_env, tiny_dataset):
    chat_env.store.replace(tiny_dataset, source="uploaded_local_only")
    page = chat_env.page()
    assert not page.exception
    assert "/api/agent/status" not in chat_env.paths()
    body = text(page, page.markdown)
    assert "本地规则分析（上传数据）" in body
    assert "上传数据 + 本地规则" in body
    assert all(name in body for name, _ in STATE_LEGEND)


# --- 2. Sidebar truthfulness for Mock vs live-looking provider ---

def test_mock_synthetic_dataset_is_labelled_offline(chat_env):
    page = activate_synthetic(chat_env.page())
    assert not page.exception
    assert "/api/agent/status" in chat_env.paths()
    body = text(page, page.markdown)
    assert "离线 Mock 演示（合成数据，未调用 DeepSeek）" in body
    assert "DeepSeek AI 分析" not in body
    assert "合成数据 + 离线 Mock" in body
    assert "simulated" in body and "True" in body
    assert all(name in body for name, _ in STATE_LEGEND)


def test_live_like_provider_is_labelled_as_deepseek(live_like_env):
    env, _ = live_like_env
    page = activate_synthetic(env.page())
    assert not page.exception
    body = text(page, page.markdown)
    assert "DeepSeek AI 分析（deepseek-flash）" in body
    assert "合成数据 + 真实 DeepSeek" in body
    assert "simulated" in body and "False" in body
    assert "/api/agent/status" in env.paths()


def test_confirmation_copy_does_not_promise_deepseek(chat_env):
    page = chat_env.page()
    control(page, page.button, "chat_syn_preview_button").click().run()
    labels = [item.label for item in page.button]
    assert "确认并启用合成数据集" in labels
    assert not any("DeepSeek" in label for label in labels)
    assert any("选择数据来源" in item.label for item in page.radio)
    assert all("可体验 DeepSeek" not in option for option in page.radio[0].options)


def test_dataset_switch_drops_the_cached_provider_label(live_like_env):
    """A stale live-model label must not survive a dataset change."""
    env, _ = live_like_env
    page = activate_synthetic(env.page())
    assert "DeepSeek AI 分析（deepseek-flash）" in text(page, page.markdown)
    assert page.session_state.chat["agent_runtime"] is not None
    # Simulate the backend changing underneath the page.
    with env.store.connect() as connection:
        connection.execute("UPDATE dataset_metadata SET source='uploaded_local_only'")
    page.run()
    assert not page.exception
    assert page.session_state.chat["dataset"] is None
    assert page.session_state.chat["agent_runtime"] is None


# --- 3. The six per-answer states ---

def test_answer_mode_label_covers_all_observable_outcomes(chat_env):
    page = activate_synthetic(chat_env.page())
    submit(page, "分析体育 III 的成绩分布")
    base = page.session_state.chat["messages"][-1]["response"]
    cases = [
        dict(status="needs_clarification", provider="semantic", simulated=False,
             analysis_status="not_requested", answer_mode="notice"),
        dict(provider="local", simulated=False, analysis_status="not_requested",
             answer_mode="verified_localized"),
        dict(provider="mock", simulated=True, analysis_status="not_requested",
             answer_mode="deterministic_fallback"),
        dict(provider="mock", simulated=True, analysis_status="verified",
             answer_mode="evidence_analysis"),
        dict(provider="mock", simulated=True, analysis_status="fallback",
             answer_mode="deterministic_fallback"),
        dict(provider="deepseek", simulated=False, analysis_status="verified",
             answer_mode="evidence_analysis"),
        dict(provider="deepseek", simulated=False, analysis_status="fallback",
             answer_mode="deterministic_fallback"),
    ]
    labels = [_answer_mode_label(base.model_copy(update=case)) for case in cases]
    assert "未调用模型" in labels[0]                       # clarification / refusal
    assert "本地规则分析" in labels[1]                     # uploaded + local
    assert "离线 Mock 演示" in labels[2]                   # synthetic + Mock
    assert "证据分析夹具（非真实模型输出）" in labels[3]     # Mock verified analysis
    assert "Mock 不产出该协议，预期行为" in labels[4]        # Mock fallback
    assert "综合分析校验通过" in labels[5]                  # live model success
    assert "模型分析未通过校验，已回退" in labels[6]         # live model fallback
    assert len(set(labels)) == len(labels)


def test_clarification_is_labelled_as_no_model_call(chat_env):
    page = activate_synthetic(chat_env.page())
    submit(page, "分析体育课的成绩")
    assert not page.exception
    result = page.session_state.chat["messages"][-1]["response"]
    assert result.status == "needs_clarification" and result.model_turns == 0
    assert "未调用模型" in text(page, page.caption)


def test_mock_missing_analysis_is_described_as_expected_demo_behaviour(chat_env):
    page = activate_synthetic(chat_env.page())
    submit(page, "分析体育 III 的成绩分布")
    assert not page.exception
    result = page.session_state.chat["messages"][-1]["response"]
    assert result.simulated is True and result.analysis_fallback_reason == "missing_analysis"
    warnings = text(page, page.warning)
    assert "预期行为，不是真实模型失败" in warnings
    assert "模型没有返回可验证的综合分析" not in warnings


def test_verified_live_analysis_is_not_labelled_as_a_fixture(live_like_env):
    env, _ = live_like_env
    page = activate_synthetic(env.page())
    submit(page, "分析体育 III 的成绩分布")
    assert not page.exception
    result = page.session_state.chat["messages"][-1]["response"]
    assert result.simulated is False and result.analysis_status == "verified"
    body = text(page, page.markdown)
    assert "AI 综合分析（证据校验通过 · DeepSeek）" in body
    assert "离线 Mock 夹具" not in body
    assert "综合分析校验通过" in text(page, page.caption)
    assert "41.67%" in body and "样本较小" in body


def test_failed_live_analysis_shows_model_failure_fallback(live_like_env):
    env, provider = live_like_env
    provider.analysis = {**valid_analysis(), "new_value": 99.99}
    page = activate_synthetic(env.page())
    submit(page, "分析体育 III 的成绩分布")
    assert not page.exception
    result = page.session_state.chat["messages"][-1]["response"]
    assert result.simulated is False and result.analysis_status == "fallback"
    assert "模型分析未通过校验，已回退确定性统计" in text(page, page.caption)
    assert "未通过证据校验" in text(page, page.warning)
    assert "统计结论" in text(page, page.markdown)
    assert "99.99" not in text(page, page.markdown)


def test_technical_details_still_expose_full_ids_and_evidence(live_like_env):
    env, _ = live_like_env
    page = activate_synthetic(env.page())
    submit(page, "分析体育 III 的成绩分布")
    assert not page.exception
    result = page.session_state.chat["messages"][-1]["response"]
    assert any(item.label == "计算证据与技术详情" for item in page.expander)
    assert any("本次分析状态说明" in item.label for item in page.expander)
    assert any(item.value == result.dataset_version for item in page.code)
    assert result.evidence[0].arguments["course_id"] not in text(page, page.markdown)
