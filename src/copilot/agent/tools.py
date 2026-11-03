"""Four read-only wrappers; statistics are delegated to AnalyticsEngine."""
import hashlib
import json

from pydantic import ValidationError

from copilot.agent.models import (AgentError, CatalogArgs, ComparisonArgs, Evidence, OverviewArgs,
                                  SummaryArgs, strict_json)
from copilot.analytics import AnalyticsEngine
from copilot.schemas import Scope
from copilot.storage import Database

TOOLS = {
    "get_dataset_overview": (OverviewArgs, "Counts of students, classes, grades, courses and course loads. No student records."),
    "get_score_summary": (SummaryArgs, "Deterministic mean, weighted mean, pass rate, missing/exempt counts, failure counts and distribution. Null filters mean all eligible records. Scores are over graded enrollments, not student means."),
    "compare_classes": (ComparisonArgs, "Compare 2-10 distinct classes on the SAME course and term, using existing exact mean calculations and sample sizes."),
    "get_catalog": (CatalogArgs, "Look up available course/class IDs and course names, optionally by grade. No student identities. Use before resolving an ambiguous course name."),
}


class ToolRegistry:
    def __init__(self, database: Database):
        self.database = database
        self.engine = AnalyticsEngine(database)

    @staticmethod
    def definitions():
        definitions = []
        for name, (args, description) in TOOLS.items():
            schema = args.model_json_schema()
            schema.setdefault("required", [])
            definitions.append({"type": "function", "function": {"name": name, "description": description,
                                 "strict": True, "parameters": schema}})
        return definitions

    @staticmethod
    def validate(name: str, arguments: str):
        if name not in TOOLS:
            raise AgentError("unknown_tool", "Model selected a tool outside the allowlist")
        if len(arguments.encode()) > 4096:
            raise AgentError("invalid_tool_arguments", "Tool arguments exceed their byte limit")
        try:
            return TOOLS[name][0].model_validate(strict_json(arguments))
        except (ValidationError, ValueError, TypeError, RecursionError):
            raise AgentError("invalid_tool_arguments", "Model tool arguments failed strict validation") from None

    def execute(self, name: str, args, *, version: str, evidence_id: str) -> Evidence:
        # Revalidate at the dispatch boundary, including programmatic callers.
        if name not in TOOLS or type(args) is not TOOLS[name][0]:
            raise AgentError("unknown_tool", "No dispatch target for this tool and argument type")
        try:
            args = TOOLS[name][0].model_validate(args.model_dump())
        except ValidationError:
            raise AgentError("invalid_tool_arguments", "Tool arguments failed dispatch validation") from None
        guard = {"expected_version": version, "generated_only": True}
        values = args.model_dump()
        if name == "get_dataset_overview":
            data = self.database.overview(**guard).model_dump(mode="json")
        elif name == "get_score_summary":
            data = self.engine.summary(Scope(**values), **guard).model_dump(mode="json")
        elif name == "compare_classes":
            data = self.engine.compare_classes(**values, **guard).model_dump(mode="json")
        else:
            catalog = self.database.catalog(**guard)
            level = args.grade_level
            data = {"dataset_version": catalog.dataset_version,
                    "classes": [r.model_dump() for r in catalog.classes if level is None or r.grade_level == level],
                    "courses": [r.model_dump() for r in catalog.courses if level is None or r.grade_level == level]}
        result = Evidence(evidence_id=evidence_id, tool=name, dataset_version=version, arguments=values, data=data)
        if len(result.model_dump_json().encode()) > 32768:
            raise AgentError("tool_result_too_large", "Aggregate tool result exceeds its output limit")
        return result


def digest(result: Evidence) -> str:
    return hashlib.sha256(json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
