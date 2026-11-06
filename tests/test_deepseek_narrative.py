import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from copilot.agent.models import AgentError
from copilot.agent.provider import LLMSettings, OpenAICompatibleProvider
from copilot.agent.service import AgentService
from copilot.api import create_app
from tests.test_agent_service import SUMMARY, ScriptedProvider, agent_db, final_turn, tool_turn
from tests.test_llm_provider import completion

QUESTION = "What is the average score and pass rate for G1-C01?"
NARRATIVE = ["Looking at {{r1.scope}}, {{r1.mean}}, while {{r1.pass_rate}}.",
             "In {{r1.scope}}, {{r1.largest_band}}."]


def test_deepseek_env_defaults_and_key_repr(monkeypatch):
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("COPILOT_LLM_API_KEY", "fake-deepseek-key")
    monkeypatch.delenv("COPILOT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("COPILOT_LLM_MODEL", raising=False)
    settings = LLMSettings.from_env()
    assert settings.base_url == "https://api.deepseek.com" and settings.model == "deepseek-flash"
    assert "fake-deepseek-key" not in repr(settings)
    monkeypatch.setenv("COPILOT_LLM_BASE_URL", "https://wrong.example")
    with pytest.raises(AgentError, match="configuration"):
        LLMSettings.from_env()


@pytest.mark.parametrize("status,code", [(401, "llm_authentication_error"), (402, "llm_insufficient_balance")])
def test_deepseek_http_errors_are_sanitized(status, code):
    settings = LLMSettings(provider="deepseek", model="deepseek-flash", base_url="https://api.deepseek.com", api_key=SecretStr("fake-deepseek-key"))
    provider = OpenAICompatibleProvider(settings, transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private vendor body")))
    with pytest.raises(AgentError) as error:
        asyncio.run(provider.complete([], []))
    assert error.value.code == code and "private vendor body" not in str(error.value)


def test_deepseek_chat_completions_full_api_round_trip(agent_db, caplog):
    sent = []

    def transport(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer fake-deepseek-key"
        payload = json.loads(request.content)
        sent.append(payload)
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["max_tokens"] == 2048 and payload["response_format"] == {"type": "json_object"}
        assert not {"store", "max_completion_tokens", "parallel_tool_calls"} & payload.keys()
        assert len(payload["tools"]) == 4
        assert all("strict" not in item["function"] for item in payload["tools"])
        if len(sent) == 1:
            turn, reason = tool_turn(), "tool_calls"
        else:
            prior, result = payload["messages"][-2:]
            assert prior["content"] is None and prior["tool_calls"][0]["id"] == result["tool_call_id"]
            assert result["role"] == "tool"
            evidence = json.loads(result["content"])
            assert evidence["evidence_id"] == "r1" and evidence["data"]["average_score"] == "73.33"
            assert "{{r1.mean}}" in evidence["narrative_slots"]
            turn, reason = final_turn(narrative=NARRATIVE, guidance=["review_score_bands"]), "stop"
        return httpx.Response(200, json=completion(turn.message.model_dump(), reason, id=f"ds-mock-{len(sent)}",
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "prompt_cache_hit_tokens": 40}))

    settings = LLMSettings(provider="deepseek", model="deepseek-flash", base_url="https://api.deepseek.com", api_key=SecretStr("fake-deepseek-key"))
    provider = OpenAICompatibleProvider(settings, transport=httpx.MockTransport(transport))
    with TestClient(create_app(agent_db.path, bootstrap_demo=False, agent_provider=provider)) as client:
        response = client.post("/api/agent/query", json={"question": QUESTION})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["provider"] == "deepseek" and len(sent) == 2
    assert result["answer_mode"] == "grounded_narrative" and result["fallback_reason"] is None
    assert result["answer"].startswith("Looking at course G1-C01")
    assert "73.33 points" in result["answer"] and "66.67%" in result["answer"] and "tied for" in result["answer"]
    assert "[r1]" in result["answer"] and result["guidance"]
    assert result["usage"]["total_tokens"] == 240
    assert [entry["provider_response_id"] for entry in result["model_trace"]] == ["ds-mock-1", "ds-mock-2"]
    assert all(entry["usage"]["total_tokens"] == 120 for entry in result["model_trace"])
    assert "fake-deepseek-key" not in json.dumps(sent) + response.text + caplog.text
    assert '"student_id"' not in json.dumps(sent) and "Synthetic Student" not in json.dumps(sent)


@pytest.mark.parametrize("bad_sentence", [
    "For {{r1.scope}}, the average was 99.99.",
    "For {{r1.scope}}, the average was ninety nine.",
    "For {{r1.scope}}, {{r2.mean}}.",
    "For {{r1.scope}}, {{r1.mean}} because teaching was better.",
    "For {{r1.scope}}, {{r1.mean}} and {{r1.leaders}}.",
    "For {{r1.scope}}, {{r1.C001.mean}}.",
])
def test_unverified_model_prose_falls_back_with_trace(agent_db, bad_sentence):
    provider = ScriptedProvider([tool_turn(), final_turn(narrative=[bad_sentence])])
    result = asyncio.run(AgentService(agent_db, provider).query(QUESTION))
    assert result.answer_mode == "deterministic_fallback" and result.fallback_reason == "invalid_narrative"
    assert "73.33" in result.answer and "99.99" not in result.answer and "teaching" not in result.answer
    assert result.tool_calls[0].result_digest and result.evidence[0].dataset_version == result.dataset_version


def test_incompatible_guidance_is_discarded(agent_db):
    result = asyncio.run(AgentService(agent_db, ScriptedProvider([
        tool_turn(), final_turn(narrative=NARRATIVE, guidance=["check_sample_sizes"])
    ])).query(QUESTION))
    assert result.answer_mode == "deterministic_fallback" and result.guidance == []


def test_comparison_narrative_uses_exact_rank_and_class_scope(agent_db):
    args = {"term": "2026-T1", "course_id": "G1-C01", "class_ids": ["C001", "C002"]}
    narrative = ["For {{r1.scope}}, {{r1.leaders}}.", "In {{r1.C002.scope}}, {{r1.C002.mean}} and {{r1.C002.sample}}."]
    result = asyncio.run(AgentService(agent_db, ScriptedProvider([
        tool_turn("compare_classes", args), final_turn(narrative=narrative, guidance=["check_sample_sizes"])
    ])).query("Compare C001 and C002 for G1-C01"))
    assert result.answer_mode == "grounded_narrative"
    assert "C002 had the highest unrounded mean among the selected classes" in result.answer
    assert "100.00 points" in result.answer and result.guidance


def test_empty_scope_cannot_publish_mean_slot(agent_db):
    provider = ScriptedProvider([tool_turn(args={**SUMMARY, "course_id": "G2-C01"}), final_turn(narrative=NARRATIVE)])
    result = asyncio.run(AgentService(agent_db, provider).query("Average score for G2-C01"))
    assert result.answer_mode == "deterministic_fallback" and "no graded scores" in result.answer


def test_final_turn_data_change_invalidates_answer(agent_db, tiny_dataset):
    class SwapAfterAnswer(ScriptedProvider):
        async def complete(self, messages, tools):
            reply = await super().complete(messages, tools)
            if len(self.requests) == 2:
                agent_db.replace(tiny_dataset)
            return reply

    provider = SwapAfterAnswer([tool_turn(), final_turn(narrative=NARRATIVE)])
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, provider).query(QUESTION))
    assert error.value.status == 403 and len(error.value.model_trace) == 2
