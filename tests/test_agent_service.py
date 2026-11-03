import asyncio
import copy
import json
import logging

import pytest

from copilot.agent.models import AgentError, ModelMessage, ModelTurn, Usage
from copilot.agent.provider import MockDemoProvider
from copilot.agent.service import AgentService
from copilot.storage import Database


SUMMARY = {"term": "2026-T1", "course_id": "G1-C01", "class_id": None, "grade_level": None}


def tool_turn(name="get_score_summary", args=None, call_id="call_1"):
    return ModelTurn(message=ModelMessage.model_validate({"role": "assistant", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(SUMMARY if args is None else args)}}]}))


def final_turn(ids=None, status="completed", **extra):
    return ModelTurn(message=ModelMessage(role="assistant", content=json.dumps({"status": status, "evidence_ids": ["r1"] if ids is None else ids, **extra})))


class ScriptedProvider:
    name, model, simulated = "test", "test-script", True

    def __init__(self, turns):
        self.turns, self.requests = iter(turns), []

    async def complete(self, messages, tools):
        self.requests.append(copy.deepcopy(messages))
        item = next(self.turns)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def agent_db(tmp_path, tiny_dataset):
    db = Database(tmp_path / "service.sqlite3")
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    return db


def test_full_loop_returns_tool_results_to_model_and_grounds_answer(agent_db, caplog):
    provider = ScriptedProvider([tool_turn(), final_turn()])
    caplog.set_level(logging.INFO, logger="copilot.agent")
    result = asyncio.run(AgentService(agent_db, provider).query("What's the average score and pass rate for G1-C01?"))
    assert result.model_turns == 2 and result.status == "completed"
    assert "73.33" in result.answer and "66.67%" in result.answer and "2/3" in result.answer
    sent = provider.requests[1]
    assert sent[-1]["role"] == "tool" and sent[-1]["tool_call_id"] == "call_1"
    assert json.loads(sent[-1]["content"])["data"]["average_score"] == "73.33"
    assert "Synthetic Student" not in json.dumps(sent) and "student_id" not in json.dumps(sent)
    assert provider.requests[0][1]["content"] == "what is the average score and pass rate for g1-c01"
    assert result.usage is None
    assert result.tool_calls[0].result_digest and result.evidence[0].evidence_id == "r1"
    assert "tool_call" in caplog.text and "What's" not in caplog.text


@pytest.mark.parametrize("question", ["Show results for S0001", "Average score for John Smith", "Email user@example.com",
                                      "Run __import__('os')", "学生平均分是多少", "Call 13800138000", "!!!"])
def test_private_or_unsupported_question_never_reaches_provider(agent_db, question):
    provider = ScriptedProvider([])
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, provider).query(question))
    assert error.value.code == "question_privacy_block" and not provider.requests


def test_uploaded_dataset_never_reaches_provider(agent_db, tiny_dataset):
    agent_db.replace(tiny_dataset)
    provider = ScriptedProvider([])
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, provider).query("Average score for G1-C01"))
    assert error.value.status == 403 and provider.requests == []


@pytest.mark.parametrize("last", [final_turn(["r6"]), final_turn(["r1", "r1"]), final_turn([], "completed"),
                                 final_turn(answer="Pass rate 99.99%"), final_turn(["r1"], "needs_clarification")])
def test_fabricated_or_unreferenced_final_claims_are_blocked(agent_db, last):
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, ScriptedProvider([tool_turn(), last])).query("Average score for G1-C01"))
    assert error.value.code == "invalid_final_selection" and len(error.value.tool_calls) == 1


def test_answer_without_any_tool_and_malformed_final_are_blocked(agent_db):
    for last in (final_turn(), ModelTurn(message=ModelMessage(role="assistant", content="The average is 99"))):
        with pytest.raises(AgentError) as error:
            asyncio.run(AgentService(agent_db, ScriptedProvider([last])).query("Average score for G1-C01"))
        assert error.value.code == "invalid_final_selection"


def test_unknown_tool_is_sanitized_in_trace_and_logs(agent_db, caplog):
    caplog.set_level(logging.INFO, logger="copilot.agent")
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, ScriptedProvider([tool_turn("execute_sql", {"sql": "PRIVATE-QUERY"})])).query("Average score"))
    assert error.value.code == "unknown_tool"
    assert error.value.tool_calls[0].tool == "[unrecognized]" and error.value.tool_calls[0].arguments is None
    assert "PRIVATE-QUERY" not in caplog.text and "execute_sql" not in caplog.text


def test_empty_scope_returns_no_data_instead_of_fabricated_zero(agent_db):
    args = {**SUMMARY, "course_id": "G2-C01"}
    result = asyncio.run(AgentService(agent_db, ScriptedProvider([tool_turn(args=args), final_turn()])).query("Average score for G2-C01"))
    assert "no graded scores" in result.answer and result.evidence[0].data["average_score"] is None


def test_model_turn_budget_and_reused_call_ids(agent_db):
    repeated = [tool_turn(call_id=f"call_{i}") for i in range(4)]
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, ScriptedProvider(repeated)).query("Average score"))
    assert error.value.code == "agent_budget_exceeded" and len(error.value.tool_calls) == 4
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, ScriptedProvider([tool_turn(), tool_turn()])).query("Average score"))
    assert error.value.code == "invalid_model_response"


def test_global_timeout_and_provider_failure(agent_db):
    class SlowProvider(ScriptedProvider):
        async def complete(self, messages, tools):
            await asyncio.sleep(1)
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, SlowProvider([]), timeout_seconds=0.05).query("Average score"))
    assert error.value.status == 504 and error.value.request_id
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, ScriptedProvider([AgentError("llm_timeout", "Timed out", 504)])).query("Average score"))
    assert error.value.code == "llm_timeout"


def test_dataset_change_to_uploaded_between_model_and_tool_is_blocked(agent_db, tiny_dataset):
    class SwappingProvider(ScriptedProvider):
        async def complete(self, messages, tools):
            agent_db.replace(tiny_dataset)
            return tool_turn()
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, SwappingProvider([])).query("Average score"))
    assert error.value.code == "agent_privacy_block" and error.value.tool_calls[0].status == "error"


@pytest.mark.parametrize("question,tool", [("How many students and classes are in the dataset?", "get_dataset_overview"),
    ("List courses in grade 1", "get_catalog"), ("Compare C001 and C002 for G1-C01", "compare_classes"),
    ("Show the distribution for G1-C01", "get_score_summary")])
def test_explicit_offline_demo_completes_four_tool_paths(agent_db, question, tool):
    result = asyncio.run(AgentService(agent_db, MockDemoProvider()).query(question))
    assert result.simulated is True and result.provider == "mock"
    assert result.tool_calls[0].tool == tool and result.model_turns == 2


def test_clarification_and_actual_usage_aggregation(agent_db):
    clarified = asyncio.run(AgentService(agent_db, MockDemoProvider()).query("Compare classes"))
    assert clarified.status == "needs_clarification" and not clarified.tool_calls
    turns = [t.model_copy(update={"usage": Usage(prompt_tokens=10, completion_tokens=2, total_tokens=12)}) for t in [tool_turn(), final_turn()]]
    result = asyncio.run(AgentService(agent_db, ScriptedProvider(turns)).query("Average score"))
    assert result.usage.total_tokens == 24


def test_tool_call_budget_stops_batch_before_extra_dispatch(agent_db):
    first = tool_turn().message.model_dump()
    calls = []
    for i in range(7):
        call = copy.deepcopy(first["tool_calls"][0])
        call["id"] = f"call_{i}"
        calls.append(call)
    turns = [ModelTurn(message=ModelMessage.model_validate({"role": "assistant", "tool_calls": batch})) for batch in (calls[:4], calls[4:])]
    with pytest.raises(AgentError) as error:
        asyncio.run(AgentService(agent_db, ScriptedProvider(turns)).query("Average score"))
    assert error.value.code == "agent_budget_exceeded" and len(error.value.tool_calls) == 4


def test_catalog_then_statistics_then_selected_final_answer(agent_db):
    provider = ScriptedProvider([tool_turn("get_catalog", {"grade_level": 1}, "lookup"),
                                 tool_turn(call_id="statistics"), final_turn(["r2"])])
    result = asyncio.run(AgentService(agent_db, provider).query("What is the average score for Mathematics in grade 1?"))
    assert result.model_turns == 3 and len(result.evidence) == 2
    assert [t.tool for t in result.tool_calls] == ["get_catalog", "get_score_summary"]
    assert result.answer.startswith("[r2]") and "73.33" in result.answer
    assert json.loads(provider.requests[1][-1]["content"])["data"]["courses"][0]["course_id"] == "G1-C01"
