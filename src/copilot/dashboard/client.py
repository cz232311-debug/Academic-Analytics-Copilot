"""Typed HTTP boundary for Streamlit. This module never opens the database."""
import os

import httpx
from pydantic import ValidationError

from copilot.agent.models import AgentResponse
from copilot.data.college import CollegeCommitResult, CollegePreview
from copilot.models import Record
from copilot.schemas import CourseReport, DatasetInfo, ReportScope, ReportingCatalog


class AgentStatus(Record):
    provider: str
    model: str
    simulated: bool
    tools: list[str]
    max_model_turns: int
    max_tool_calls: int
    request_timeout_seconds: float
    question_policy: str
    streaming: bool


class DashboardError(Exception):
    def __init__(self, code: str, message: str, *, request_id: str | None = None):
        super().__init__(message)
        self.code, self.request_id = code, request_id


ERROR_MESSAGES = {
    "unknown_entity": "A selected term or ID is unavailable. Refresh the page and select from the current catalog.",
    "invalid_request": "The selected filters or question are invalid. Review the values and try again.",
    "dataset_changed": "The dataset changed while loading. Refresh the page and load the analysis again.",
    "no_dataset": "The API has no active dataset. Start it with the generated demonstration database.",
    "agent_privacy_block": "AI queries are available only for generated synthetic data. Uploaded datasets stay local.",
    "question_privacy_block": "Use a supported English aggregate question with course or class IDs. Omit names and personal information.",
    "provider_timeout": "The model timed out. Your statistics remain available. Try the question again later.",
    "agent_timeout": "The AI query exceeded its time limit. Try a simpler aggregate question.",
    "invalid_model_response": "The model response failed validation. No unverified answer was displayed.",
    "llm_not_configured": "The backend LLM configuration is incomplete. Check its service terminal or use Mock mode.",
    "provider_authentication": "The backend could not authenticate with its model provider. Check its environment configuration.",
    "provider_rate_limited": "The model provider is busy or rate limited. Try again later.",
    "llm_configuration_error": "The backend LLM configuration is incomplete. Check its service terminal or use Mock mode.",
    "llm_authentication_error": "The backend could not authenticate with DeepSeek. Check the API key in the backend terminal.",
    "llm_insufficient_balance": "The model provider reports insufficient account balance. Local statistics remain available.",
    "llm_rate_limited": "The model provider is busy or rate limited. Try again later.",
    "llm_timeout": "The model timed out. Your statistics remain available. Try the question again later.",
    "llm_connection_error": "The backend could not reach its model provider. Local statistics remain available.",
    "model_refused": "The model declined this query. Try a supported English aggregate academic question.",
    "invalid_final_selection": "The model selected unsupported evidence. No unverified answer was displayed.",
    "agent_budget_exceeded": "The AI query reached its tool or model-call limit. Try a simpler aggregate question.",
    "college_structure": "无法识别学院成绩表。请核对单工作表、学号/班级字段及含学分、学时、学期的课程表头。",
    "college_limits": "成绩表超过安全限制：5 MiB、256列、5000学生或50000条选课成绩。",
    "confirmation_required": "请先核对预览并明确确认导入。",
    "preview_invalid": "文件或预览凭据已改变，请重新解析预览。",
    "preview_expired": "预览已过期或后端已重启，请重新解析预览。",
    "college_not_ready": "成绩表仍有阻止提交的问题，请修正后重新预览。",
    "course_conflict": "同一课程标识与已有目录定义冲突，数据未覆盖。请核对课程元数据。",
}


class DashboardClient:
    def __init__(self, base_url: str | None = None, *, transport=None):
        self.base_url = (base_url or os.environ.get("COPILOT_API_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        try:
            url = httpx.URL(self.base_url)
            if url.scheme not in {"http", "https"} or not url.host or url.username or url.password or url.query or url.fragment:
                raise ValueError
        except (httpx.InvalidURL, ValueError):
            raise DashboardError("configuration", "COPILOT_API_BASE_URL must be an HTTP(S) service URL without credentials.") from None
        self.transport = transport

    def _request(self, method, path, model, *, params=None, payload=None, files=None, data=None):
        try:
            # The backend permits an overall Agent deadline up to 120 seconds.
            with httpx.Client(base_url=self.base_url, timeout=130 if method == "POST" else 10,
                              trust_env=False, follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, path, params=params, json=payload, files=files, data=data)
        except httpx.TimeoutException:
            raise DashboardError("timeout", "The API request timed out. Check the backend terminal and try again.") from None
        except httpx.TransportError:
            raise DashboardError("connection", "Cannot reach FastAPI. Start the backend on port 8000 and keep its terminal open.") from None
        if not response.is_success:
            code, request_id = "api_error", None
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = body.get("error", {})
                    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                        code = detail["code"]
                    request_id = body.get("request_id")
            except ValueError:
                pass
            message = ERROR_MESSAGES.get(code, f"The API could not complete this request (HTTP {response.status_code}). Check the backend terminal.")
            raise DashboardError(code, message, request_id=request_id if isinstance(request_id, str) else None)
        try:
            return model.model_validate_json(response.content)
        except (ValidationError, ValueError):
            raise DashboardError("invalid_response", "The API response does not match the expected contract. Restart matching frontend and backend versions.") from None

    def dataset(self) -> DatasetInfo:
        return self._request("GET", "/api/dataset", DatasetInfo)

    def catalog(self) -> ReportingCatalog:
        return self._request("GET", "/api/reports/catalog", ReportingCatalog)

    def report(self, scope: ReportScope, *, expected_version=None) -> CourseReport:
        params = scope.model_dump(exclude_none=True)
        if expected_version is not None:
            params["expected_version"] = expected_version
        return self._request("GET", "/api/reports/courses", CourseReport, params=params)

    def agent_status(self) -> AgentStatus:
        return self._request("GET", "/api/agent/status", AgentStatus)

    def query(self, question: str) -> AgentResponse:
        return self._request("POST", "/api/agent/query", AgentResponse, payload={"question": question})

    def college_preview(self, filename: str, content: bytes, *, duplicate_policy="block") -> CollegePreview:
        return self._request("POST", "/api/datasets/college/preview", CollegePreview,
                             files={"workbook": (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                             data={"duplicate_policy": duplicate_policy})

    def college_commit(self, filename: str, content: bytes, *, preview_token: str) -> CollegeCommitResult:
        return self._request("POST", "/api/datasets/college/commit", CollegeCommitResult,
                             files={"workbook": (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                             data={"preview_token": preview_token, "confirmed": "true"})
