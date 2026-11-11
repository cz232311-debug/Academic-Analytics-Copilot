"""Four read-only wrappers; statistics are delegated to AnalyticsEngine."""
import hashlib
import json

from pydantic import ValidationError

from copilot.agent.models import (AgentError, CatalogArgs, ComparisonArgs, Evidence, OverviewArgs,
                                  SummaryArgs, strict_json)
from copilot.analytics import AnalyticsEngine
from copilot.schemas import ReportScope, Scope
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
            # Explicit nullable defaults remain optional for local callers;
            # strict remote schemas require every declared parameter.
            schema["required"] = list(schema["properties"])
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

    def execute(self, name: str, args, *, version: str, evidence_id: str, generated_only=True) -> Evidence:
        # Revalidate at the dispatch boundary, including programmatic callers.
        if name not in TOOLS or type(args) is not TOOLS[name][0]:
            raise AgentError("unknown_tool", "No dispatch target for this tool and argument type")
        try:
            args = TOOLS[name][0].model_validate(args.model_dump())
        except ValidationError:
            raise AgentError("invalid_tool_arguments", "Tool arguments failed dispatch validation") from None
        guard = {"expected_version": version, "generated_only": generated_only}
        values = args.model_dump(exclude={"major"})
        major = getattr(args, "major", None)
        if major is not None:
            values["major"] = major
        if name == "get_dataset_overview":
            data = self.database.overview(**guard).model_dump(mode="json")
        elif name == "get_score_summary":
            if major is None:
                data = self.engine.summary(Scope(**values), **guard).model_dump(mode="json")
            else:
                # The reporting engine already implements the major filter and
                # the same exact aggregate calculation. Reuse that snapshot.
                full_scope = ReportScope(**values)
                report = self.engine.course_report(full_scope, **guard)
                data = report.summary.model_dump(mode="json")
                data["report_scope"] = full_scope.model_dump(mode="json")
        elif name == "compare_classes":
            data = self.engine.compare_classes(**values, **guard).model_dump(mode="json")
        else:
            catalog = self.database.catalog(**guard)
            level = args.grade_level
            classes = [r for r in catalog.classes if (level is None or r.grade_level == level)
                       and (major is None or r.major == major)]
            course_ids = None
            if major is not None:
                # Catalog membership follows actual enrollments, not an
                # inferred curriculum based on a course name or cohort alone.
                with self.database.connect() as connection:
                    connection.execute("BEGIN")
                    self.database.check_access(self.database.metadata(connection), **guard)
                    course_ids = {row[0] for row in connection.execute(
                        "SELECT DISTINCT e.course_id FROM enrollments e JOIN students s ON s.student_id=e.student_id "
                        "JOIN classes c ON c.class_id=s.class_id WHERE c.major=? AND (? IS NULL OR c.grade_level=?)",
                        (major, level, level))}
            data = {"dataset_version": catalog.dataset_version,
                    "classes": [r.model_dump(exclude={"major"}) for r in classes],
                    "courses": [r.model_dump(exclude={"course_type"}) for r in catalog.courses
                                if (level is None or r.grade_level == level) and (course_ids is None or r.course_id in course_ids)]}
        result = Evidence(evidence_id=evidence_id, tool=name, dataset_version=version, arguments=values, data=data)
        if len(result.model_dump_json().encode()) > 32768:
            raise AgentError("tool_result_too_large", "Aggregate tool result exceeds its output limit")
        return result


def digest(result: Evidence) -> str:
    return hashlib.sha256(json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
