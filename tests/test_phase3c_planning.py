"""Small scripted Mock evaluation of the offline plan-validation prototype."""
import copy
import json

import pytest

from copilot.agent.models import AgentError, SessionScope
from copilot.agent.planning import validate_plan
from copilot.agent.semantics import resolve_question
from tests.test_agent_service import agent_db


def approved(db):
    catalog = db.reporting_catalog()
    scope = SessionScope(term="2026-T1")
    intents = {
        "summary": resolve_question("分析 G1-C01 的平均分", catalog, scope),
        "comparison": resolve_question("比较 G1-C01 在 C001 和 C002 的成绩", catalog, scope),
        "overview": resolve_question("数据概览", catalog),
        "catalog": resolve_question("课程目录", catalog),
    }
    assert all(intent.status == "ready" for intent in intents.values())
    return intents, catalog.dataset_version


def node(intents, identifier):
    intent = intents[identifier]
    return {"node_id": identifier, "tool": intent.tool, "arguments": copy.deepcopy(intent.arguments)}


def plan(*nodes):
    return {"version": "academic_plan_v1", "nodes": list(nodes)}


@pytest.mark.parametrize("identifiers", [("summary",), ("comparison", "summary"), ("catalog", "overview", "summary")])
def test_offline_plan_accepts_only_preapproved_nodes_in_a_selected_order(agent_db, identifiers):
    intents, version = approved(agent_db)
    result = validate_plan(json.dumps(plan(*(node(intents, identifier) for identifier in identifiers))), intents, expected_version=version)
    assert [item.node_id for item in result.nodes] == list(identifiers)
    # Validation is pure: the database snapshot/version stays unchanged.
    assert agent_db.overview().dataset_version == version


@pytest.mark.parametrize("mutation,code", [
    (lambda value: value["nodes"][0].update(node_id="unapproved"), "invalid_plan"),
    (lambda value: value["nodes"].append(copy.deepcopy(value["nodes"][0])), "invalid_plan"),
    (lambda value: value["nodes"][0].update(tool="execute_sql", arguments={"sql": "SELECT * FROM students"}), "invalid_plan"),
    (lambda value: value["nodes"][0]["arguments"].update(course_id="G1-C02"), "intent_mismatch"),
    (lambda value: value["nodes"][0]["arguments"].update(course_id=None), "intent_mismatch"),
    (lambda value: value["nodes"][0]["arguments"].update(term="2025-T1"), "intent_mismatch"),
    (lambda value: value["nodes"][0]["arguments"].update(class_id="C002"), "intent_mismatch"),
    (lambda value: value["nodes"][0]["arguments"].update(python="print(1)"), "invalid_tool_arguments"),
    (lambda value: value.update(explanation="invented cause"), "invalid_plan"),
    (lambda value: value["nodes"].extend([copy.deepcopy(value["nodes"][0]) for _ in range(3)]), "invalid_plan"),
])
def test_scripted_mock_invalid_plans_are_rejected_before_any_execution(agent_db, mutation, code):
    intents, version = approved(agent_db)
    value = plan(node(intents, "summary"))
    mutation(value)
    with pytest.raises(AgentError) as error:
        validate_plan(value, intents, expected_version=version)
    assert error.value.code == code


def test_plan_cannot_use_an_expired_confirmed_scope(agent_db):
    intents, _ = approved(agent_db)
    with pytest.raises(AgentError) as error:
        validate_plan(plan(node(intents, "summary")), intents, expected_version="0" * 64)
    assert error.value.code == "dataset_changed" and error.value.status == 409


def test_unresolved_scope_cannot_be_smuggled_in_as_an_approved_node(agent_db):
    intents, version = approved(agent_db)
    intents["summary"] = intents["summary"].model_copy(update={"status": "needs_clarification"})
    with pytest.raises(AgentError) as error:
        validate_plan(plan(node(intents, "summary")), intents, expected_version=version)
    assert error.value.code == "intent_mismatch"


def test_duplicate_json_keys_in_a_plan_are_rejected(agent_db):
    intents, version = approved(agent_db)
    raw = '{"version":"academic_plan_v1","nodes":[],"nodes":[]}'
    with pytest.raises(AgentError) as error:
        validate_plan(raw, intents, expected_version=version)
    assert error.value.code == "invalid_plan"
