"""Typed HTTP boundary for Streamlit. This module never opens the database."""
import os

import httpx
from pydantic import ValidationError

from copilot.agent.models import AgentResponse
from copilot.agent.semantics import ResolvedIntent
from copilot.data.college import CollegeCommitResult, CollegePreview
from copilot.data.synthetic_college import SyntheticOptions
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
    # Chat-first user-facing text. All messages are safe to show in the UI:
    # no exception text, no server internals, no request payload is echoed.
    "unknown_entity": "所选学期、班级或课程已经不可用。请重新选择分析范围，再更新统计。",
    "invalid_request": "所选条件无法使用。请检查分析范围后重试；仍有问题时，请联系负责系统的同事。",
    "dataset_changed": "数据已经更新，本次结果已暂停。请在“数据来源”中重新确认数据集，再重新提问。",
    "no_dataset": "当前还没有已确认的数据集。请先选择合成示例数据，或上传并确认一份成绩单。",
    "agent_privacy_block": "上传的成绩单只允许本地规则分析，不能发送给外部 AI。若要体验 DeepSeek 分析，请切换到合成示例数据。",
    "question_privacy_block": "请使用当前数据集范围内的学业统计问题，不要包含姓名、学号或个人成绩。",
    "intent_mismatch": "系统检测到模型选择的课程、班级或学期与你的问题不一致，已阻止这次统计。请把课程或班级写得更明确后重试。",
    "unknown_tool": "AI 选择了系统不允许的统计工具，本次已阻止，没有返回分析结果。",
    "invalid_tool_arguments": "AI 提供的统计参数不符合要求，本次已阻止，没有返回分析结果。",
    "tool_execution_error": "统计工具暂时无法完成，请稍后重试；这不是你的问题。",
    "provider_timeout": "AI 服务响应超时，请稍后重试；已确认的数据和统计结果仍然保留。",
    "agent_timeout": "本次分析超过了时间限制，请把问题缩小到一个课程或一个班后再试。",
    "invalid_model_response": "AI 返回的结果未通过校验，系统没有展示未经验证的回答。",
    "llm_not_configured": "后端 AI 服务尚未配置完成。请联系负责启动系统的同事检查服务窗口，或改用本地规则分析。",
    "provider_authentication": "后端无法通过 AI 服务的身份验证，请联系负责启动系统的同事检查配置。",
    "provider_rate_limited": "AI 服务当前繁忙，请稍后再试；本地统计不受影响。",
    "llm_configuration_error": "后端 AI 服务配置不完整。请联系负责启动系统的同事检查服务窗口，或改用本地规则分析。",
    "llm_authentication_error": "后端无法通过 DeepSeek 的身份验证，请联系负责启动系统的同事检查服务配置。",
    "llm_insufficient_balance": "AI 服务账户余额不足，请联系负责启动系统的同事；本地统计不受影响。",
    "llm_rate_limited": "AI 服务当前繁忙，请稍后再试；本地统计不受影响。",
    "llm_timeout": "AI 服务响应超时，请稍后重试；已确认的数据和统计结果仍然保留。",
    "llm_connection_error": "暂时无法连接 AI 服务，请稍后重试；本地统计不受影响。",
    "model_refused": "AI 没有处理这个问题。请换用课程成绩、班级比较、数据概览或目录查询类问题。",
    "invalid_final_selection": "AI 引用了未被验证的证据，系统没有展示这次回答。",
    "agent_budget_exceeded": "本次分析达到了工具或调用次数上限，请把问题缩小到一个课程或一个班后再试。",
    "agent_internal_error": "分析服务暂时无法完成，请稍后重试；如持续出现，请联系负责系统的同事。",
    "internal_error": "分析服务暂时无法完成，请稍后重试；如持续出现，请联系负责系统的同事。",
    "api_error": "暂时无法完成这项操作，请稍后重试；仍然失败时，请联系负责系统的同事。",
    "http_error": "暂时无法完成这项操作，请稍后重试；仍然失败时，请联系负责系统的同事。",
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

    @staticmethod
    def _query_payload(question, *, execution_mode=None, scope=None, expected_version=None):
        """Keep the legacy one-field request intact unless a mode is explicit."""
        payload = {"question": question}
        if execution_mode is not None:
            payload["execution_mode"] = execution_mode
        if scope is not None:
            payload["scope"] = scope.model_dump(exclude_none=True) if hasattr(scope, "model_dump") else dict(scope)
        if expected_version is not None:
            payload["expected_version"] = expected_version
        return payload

    def resolve(self, question, *, execution_mode="grounded", scope=None, expected_version=None) -> ResolvedIntent:
        """Catalog-bound intent preview. The server resolves locally: no model call."""
        return self._request("POST", "/api/agent/resolve", ResolvedIntent,
                             payload=self._query_payload(question, execution_mode=execution_mode,
                                                         scope=scope, expected_version=expected_version))

    def query(self, question: str, *, execution_mode=None, scope=None, expected_version=None) -> AgentResponse:
        return self._request("POST", "/api/agent/query", AgentResponse,
                             payload=self._query_payload(question, execution_mode=execution_mode,
                                                         scope=scope, expected_version=expected_version))

    def synthetic_preview(self, options: SyntheticOptions) -> CollegePreview:
        """Only options are sent: the server generates and parses the workbook."""
        payload = options.model_dump(mode="json") if hasattr(options, "model_dump") else dict(options)
        return self._request("POST", "/api/datasets/synthetic/preview", CollegePreview, payload=payload)

    def synthetic_commit(self, options: SyntheticOptions, preview_token: str, *, confirmed=True) -> CollegeCommitResult:
        payload = {"options": options.model_dump(mode="json") if hasattr(options, "model_dump") else dict(options),
                   "preview_token": preview_token, "confirmed": confirmed}
        return self._request("POST", "/api/datasets/synthetic/commit", CollegeCommitResult, payload=payload)

    def college_preview(self, filename: str, content: bytes, *, duplicate_policy="block") -> CollegePreview:
        return self._request("POST", "/api/datasets/college/preview", CollegePreview,
                             files={"workbook": (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                             data={"duplicate_policy": duplicate_policy})

    def college_commit(self, filename: str, content: bytes, *, preview_token: str) -> CollegeCommitResult:
        return self._request("POST", "/api/datasets/college/commit", CollegeCommitResult,
                             files={"workbook": (filename, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                             data={"preview_token": preview_token, "confirmed": "true"})
