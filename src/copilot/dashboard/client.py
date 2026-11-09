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


class HealthStatus(Record):
    status: str
    dataset_active: bool
    version: str


class DashboardError(Exception):
    def __init__(self, code: str, message: str, *, request_id: str | None = None):
        super().__init__(message)
        self.code, self.request_id = code, request_id


ERROR_MESSAGES = {
    "unknown_entity": "所选学期、班级或课程已经不可用。请重新选择分析范围，再更新统计。",
    "invalid_request": "所选条件无法使用。请检查分析范围后重试；仍有问题时，请联系负责系统的同事。",
    "dataset_changed": "数据已经更新，本页结果已暂停。请返回核对页，重新解析并确认成绩单，再查看统计。",
    "no_dataset": "当前尚无已确认的数据，请先上传、核对并确认成绩单。",
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
    "course_conflict": "这门课程的资料与之前保存的不一致，本次没有导入。请核对原表的课程名称、学期和学时后重试。",
    "archive_unavailable": "本地原件留存暂不可写，本次没有提交。请检查本地存储空间和目录权限后重试。",
    "archive_cleanup_failed": "提交失败，本地暂存需检查；请先重新预览，勿重复确认。",
}


class DashboardClient:
    def __init__(self, base_url: str | None = None, *, transport=None):
        self.base_url = (base_url or os.environ.get("COPILOT_API_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        try:
            url = httpx.URL(self.base_url)
            if url.scheme not in {"http", "https"} or not url.host or url.username or url.password or url.query or url.fragment:
                raise ValueError
        except (httpx.InvalidURL, ValueError):
            raise DashboardError("configuration", "本机连接设置不正确。请联系负责启动系统的同事检查服务地址，然后重试。") from None
        self.transport = transport

    def _request(self, method, path, model, *, params=None, payload=None, files=None, data=None):
        try:
            # The backend permits an overall Agent deadline up to 120 seconds.
            with httpx.Client(base_url=self.base_url, timeout=130 if method == "POST" else 10,
                              trust_env=False, follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, path, params=params, json=payload, files=files, data=data)
        except httpx.TimeoutException:
            raise DashboardError("timeout", "本次处理时间较长，暂时没有完成。请稍后重试；已确认的数据仍会保留。") from None
        except httpx.TransportError:
            raise DashboardError("connection", "暂时无法连接本机分析服务。请确认服务已经启动，再点击“重试连接”；必要时联系负责系统的同事。") from None
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
            message = ERROR_MESSAGES.get(code, "暂时无法完成这项操作。请稍后重试；仍然失败时，请联系负责系统的同事。")
            raise DashboardError(code, message, request_id=request_id if isinstance(request_id, str) else None)
        try:
            return model.model_validate_json(response.content)
        except (ValidationError, ValueError):
            raise DashboardError("invalid_response", "收到的结果暂时无法使用。请联系负责系统的同事更新并重启服务，再重新核对数据。") from None

    def dataset(self) -> DatasetInfo:
        return self._request("GET", "/api/dataset", DatasetInfo)

    def health(self) -> HealthStatus:
        return self._request("GET", "/health", HealthStatus)

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
