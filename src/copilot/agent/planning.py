"""Offline-only plan-validation prototype; no service/API imports this module.

This is not a free-question planner or executor. A future caller must first
obtain explicitly confirmed, catalog-bound intents. A model may then choose
and order only those approved nodes; the existing validators remain intact.
"""
import json
from typing import Annotated, Mapping, Literal

from pydantic import Field, StringConstraints, ValidationError

from copilot.agent.models import AgentError, ToolName, strict_json
from copilot.agent.semantics import ResolvedIntent, validate_intent_call
from copilot.agent.tools import ToolRegistry
from copilot.models import Record


class PlanNode(Record):
    node_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    tool: ToolName
    arguments: dict


class AnalysisPlan(Record):
    version: Literal["academic_plan_v1"]
    nodes: Annotated[list[PlanNode], Field(min_length=1, max_length=3)]


def validate_plan(raw: str | dict, approved_nodes: Mapping[str, ResolvedIntent], *, expected_version: str) -> AnalysisPlan:
    """Validate a scripted/model plan; deliberately do not execute any node."""
    try:
        plan = AnalysisPlan.model_validate(strict_json(raw) if isinstance(raw, str) else raw)
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise AgentError("invalid_plan", "分析计划结构不符合受控协议。", 422) from None
    ids = [node.node_id for node in plan.nodes]
    if len(ids) != len(set(ids)) or any(identifier not in approved_nodes for identifier in ids):
        raise AgentError("invalid_plan", "分析计划含未确认或重复的步骤。", 422)
    for node in plan.nodes:
        intent = approved_nodes[node.node_id]
        if intent.dataset_version != expected_version:
            raise AgentError("dataset_changed", "已确认计划的范围已过期，请重新确认。", 409)
        try:
            arguments = json.dumps(node.arguments, ensure_ascii=True, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise AgentError("invalid_plan", "分析计划参数不符合JSON协议。", 422) from None
        args = ToolRegistry.validate(node.tool, arguments)
        # Exactly the same structure + intent checks as the production loop.
        # No widening, course substitution, ad-hoc SQL or Python escape hatch.
        validate_intent_call(intent, node.tool, args)
    return plan
