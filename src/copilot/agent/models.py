"""Strict tool/message contracts and safe errors; no executable model output."""
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from copilot.models import ClassId, CourseId, DataError, GradeLevel, Major, Record, Term

ToolName = Literal["get_dataset_overview", "get_score_summary", "compare_classes", "get_catalog"]
CallId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,100}$")]


def strict_json(value: str):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = item
        return result

    def nonfinite(value):
        raise ValueError("Nonfinite JSON constant")

    return json.loads(value, object_pairs_hook=unique, parse_constant=nonfinite)


class OverviewArgs(Record):
    pass


class SessionScope(Record):
    """Confirmed UI scope; every entity is rechecked against the active catalog."""
    term: Term | None = None
    grade_level: GradeLevel | None = None
    major: Major | None = None
    class_id: ClassId | None = None
    course_id: CourseId | None = None


class SummaryArgs(Record):
    term: Term
    course_id: CourseId | None
    class_id: ClassId | None
    grade_level: GradeLevel | None
    major: Major | None = None


class ComparisonArgs(Record):
    term: Term
    course_id: CourseId
    class_ids: Annotated[list[ClassId], Field(min_length=2, max_length=10)]

    @field_validator("class_ids")
    @classmethod
    def distinct_classes(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Class IDs must be distinct")
        return values


class CatalogArgs(Record):
    grade_level: GradeLevel | None
    major: Major | None = None


class QueryRequest(Record):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    scope: SessionScope | None = None
    expected_version: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    execution_mode: Literal["configured", "local"] = "configured"


class FinalSelection(Record):
    status: Literal["completed", "needs_clarification", "unsupported"]
    evidence_ids: Annotated[list[Annotated[str, StringConstraints(pattern=r"^r[1-6]$")]], Field(max_length=6)]
    narrative: Annotated[list[Annotated[str, StringConstraints(min_length=1, max_length=360)]], Field(max_length=8)] = Field(default_factory=list)
    guidance: Annotated[list[Literal["review_score_bands", "check_sample_sizes", "review_missing_records"]], Field(max_length=3)] = Field(default_factory=list)


class FunctionProposal(Record):
    name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
    arguments: Annotated[str, StringConstraints(max_length=4096)]


class ToolCall(Record):
    id: CallId
    type: Literal["function"]
    function: FunctionProposal


class ModelMessage(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    role: Literal["assistant"]
    content: Annotated[str, StringConstraints(max_length=8000)] | None = None
    tool_calls: Annotated[list[ToolCall], Field(max_length=6)] = Field(default_factory=list)
    refusal: str | None = None

    @field_validator("tool_calls", mode="before")
    @classmethod
    def nullable_calls(cls, value):
        return [] if value is None else value


class Usage(Record):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)
    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]


class ModelTurn(Record):
    message: ModelMessage
    usage: Usage | None = None
    provider_response_id: str | None = None


class ModelTrace(Record):
    turn: int
    provider_response_id: str | None
    tool_call_count: int
    usage: Usage | None


class ToolTrace(Record):
    call_id: str
    tool: str
    arguments: dict | None
    status: Literal["ok", "error"]
    elapsed_ms: int
    evidence_id: str | None = None
    result_digest: str | None = None
    error_code: str | None = None


class Evidence(Record):
    evidence_id: str
    tool: ToolName
    dataset_version: str
    arguments: dict
    data: dict


class ExecutionStage(Record):
    """A completed server operation returned after the synchronous request."""
    code: Literal["recognize_question", "confirm_scope", "execute_tools", "validate_results", "generate_answer"]
    status: Literal["completed", "failed"] = "completed"
    elapsed_ms: Annotated[int, Field(ge=0)]


class AgentResponse(Record):
    request_id: str
    provider: str
    model: str
    simulated: bool
    dataset_version: str
    status: Literal["completed", "needs_clarification", "unsupported"]
    answer: str
    model_turns: int
    tool_calls: list[ToolTrace]
    evidence: list[Evidence]
    usage: Usage | None
    model_trace: list[ModelTrace] = Field(default_factory=list)
    answer_mode: Literal["grounded_narrative", "deterministic_fallback", "verified_localized", "notice"] = "notice"
    fallback_reason: Literal["missing_narrative", "invalid_narrative"] | None = None
    guidance: list[str] = Field(default_factory=list)
    stages: list[ExecutionStage] = Field(default_factory=list)
    resolved_scope: SessionScope | None = None


class AgentError(DataError):
    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message, code=code, status=status)
        self.request_id: str | None = None
        self.tool_calls: list[ToolTrace] = []
        self.model_trace: list[ModelTrace] = []
        self.stages: list[ExecutionStage] = []
