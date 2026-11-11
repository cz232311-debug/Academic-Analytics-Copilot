"""Small Chat Completions adapter and an explicitly simulated local demo provider."""
import json
import os
import re
from typing import Annotated, Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from copilot.agent.models import AgentError, ModelMessage, ModelTurn, Usage, strict_json
from copilot.models import Record


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"


class LLMSettings(Record):
    provider: Literal["mock", "openai_compatible", "deepseek"] = "mock"
    model: str = ""
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: Annotated[float, Field(gt=0, le=60, allow_inf_nan=False)] = 15.0
    request_timeout_seconds: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 45.0
    strict_tools: bool = True

    @model_validator(mode="after")
    def live_configuration(self):
        if self.provider == "deepseek" and self.base_url.rstrip("/") != DEEPSEEK_BASE_URL:
            raise ValueError("The DeepSeek profile requires the official HTTPS API origin")
        if self.provider != "mock":
            if not self.api_key.get_secret_value().strip() or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,100}", self.model):
                raise ValueError("Live provider requires an API key and model ID")
            parsed = urlsplit(self.base_url)
            local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or not (parsed.scheme == "https" or parsed.scheme == "http" and local)):
                raise ValueError("Use HTTPS or a loopback HTTP endpoint without URL credentials/query")
        return self

    @classmethod
    def from_env(cls):
        try:
            # Production selects DeepSeek explicitly; Mock requires an offline
            # setting and is never an automatic fallback for a missing key.
            provider = os.environ.get("COPILOT_LLM_PROVIDER", "deepseek")
            deepseek = provider == "deepseek"
            strict = os.environ.get("COPILOT_LLM_STRICT_TOOLS", "true").lower()
            if strict not in {"true", "false"}:
                raise ValueError
            return cls(provider=provider,
                       model=os.environ.get("COPILOT_LLM_MODEL") or (DEEPSEEK_MODEL if deepseek else ""),
                       api_key=SecretStr(os.environ.get("COPILOT_LLM_API_KEY", "").strip()),
                       base_url=(os.environ.get("COPILOT_LLM_BASE_URL") or (DEEPSEEK_BASE_URL if deepseek else "https://api.openai.com/v1")).rstrip("/"),
                       timeout_seconds=float(os.environ.get("COPILOT_LLM_TIMEOUT_SECONDS", "15")),
                       request_timeout_seconds=float(os.environ.get("COPILOT_AGENT_TIMEOUT_SECONDS", "45")),
                       strict_tools=strict == "true")
        except (ValueError, ValidationError):
            raise AgentError("llm_configuration_error", "Invalid LLM environment configuration; check the documented variables", 503) from None


class ChatProvider(Protocol):
    name: str
    model: str
    simulated: bool

    async def complete(self, messages: list[dict], tools: list[dict]) -> ModelTurn: ...


class Choice(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    message: ModelMessage
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter", "insufficient_system_resource", "aborted"]


class Completion(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    choices: Annotated[list[Choice], Field(min_length=1, max_length=1)]
    usage: Usage | None = None
    id: str | None = None


def _remove_deepseek_tool_indexes(payload):
    """Discard DeepSeek's positional metadata before strict tool-call validation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
        return
    for choice in payload["choices"]:
        message = choice.get("message") if isinstance(choice, dict) else None
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if isinstance(calls, list):
            for call in calls:
                if isinstance(call, dict) and type(call.get("index")) is int:
                    call.pop("index")


def _safe_error_path(error: ValidationError) -> str:
    fields = {"choices", "message", "finish_reason", "content", "role", "refusal", "tool_calls", "id", "type",
              "function", "name", "arguments", "usage", "prompt_tokens", "completion_tokens", "total_tokens"}
    location = error.errors(include_input=False, include_context=False, include_url=False)[0]["loc"]
    return ".".join(str(part) if isinstance(part, int) or part in fields else "<field>" for part in location) or "response"


class OpenAICompatibleProvider:
    name = "openai_compatible"
    simulated = False

    def __init__(self, settings: LLMSettings, *, transport=None):
        self.settings, self.model, self.transport = settings, settings.model, transport
        self.name = settings.provider

    async def complete(self, messages, tools):
        definitions = json.loads(json.dumps(tools))
        if self.name == "deepseek" or not self.settings.strict_tools:
            for item in definitions:
                item["function"].pop("strict", None)
        body = {"model": self.model, "messages": messages, "tools": definitions, "tool_choice": "auto", "stream": False}
        if self.name == "deepseek":
            body.update(thinking={"type": "disabled"}, max_tokens=2048, response_format={"type": "json_object"})
        else:
            body.update(parallel_tool_calls=False, store=False, max_completion_tokens=2048)
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, follow_redirects=False,
                                         trust_env=False, transport=self.transport) as client:
                async with client.stream("POST", self.settings.base_url.rstrip("/") + "/chat/completions",
                                         headers={"Authorization": "Bearer " + self.settings.api_key.get_secret_value()}, json=body) as response:
                    if response.status_code == 429:
                        raise AgentError("llm_rate_limited", "LLM provider rate limit reached", 503)
                    if self.name == "deepseek" and response.status_code == 401:
                        raise AgentError("llm_authentication_error", "DeepSeek rejected the configured API credential", 502)
                    if self.name == "deepseek" and response.status_code == 402:
                        raise AgentError("llm_insufficient_balance", "DeepSeek rejected the request because the account balance is insufficient", 503)
                    if not 200 <= response.status_code < 300:
                        raise AgentError("llm_http_error", "LLM provider rejected the request; check server configuration")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 131072:
                            raise AgentError("llm_response_too_large", "LLM response exceeds its byte limit")
                        content.extend(chunk)
        except AgentError:
            raise
        except httpx.TimeoutException:
            raise AgentError("llm_timeout", "LLM provider request timed out", 504) from None
        except httpx.RequestError:
            raise AgentError("llm_connection_error", "LLM provider could not be reached", 502) from None
        try:
            payload = strict_json(content.decode("utf-8"))
        except (ValueError, RecursionError):
            raise AgentError("invalid_model_response", "LLM response body was not valid JSON") from None
        if self.name == "deepseek":
            _remove_deepseek_tool_indexes(payload)
        try:
            completion = Completion.model_validate(payload)
        except ValidationError as error:
            raise AgentError("invalid_model_response", f"LLM response schema mismatch at {_safe_error_path(error)}") from None
        choice = completion.choices[0]
        if choice.finish_reason in {"insufficient_system_resource", "aborted"}:
            raise AgentError("llm_interrupted", "LLM provider interrupted generation", 503)
        if choice.finish_reason == "length":
            raise AgentError("model_output_truncated", "LLM output exceeded its token limit")
        if choice.finish_reason == "content_filter" or choice.message.refusal:
            raise AgentError("model_refused", "LLM declined this request", 422)
        if bool(choice.message.tool_calls) != (choice.finish_reason == "tool_calls"):
            raise AgentError("invalid_model_response", "LLM finish reason and tool calls disagree")
        response_id = completion.id if completion.id and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", completion.id) else None
        return ModelTurn(message=choice.message, usage=completion.usage, provider_response_id=response_id)


class MockDemoProvider:
    """Limited English routing for offline UI/debug demonstrations; NOT an LLM."""
    name, model, simulated = "mock", "scripted-demo-v1", True

    async def complete(self, messages, tools):
        results = [strict_json(m["content"]) for m in messages if m["role"] == "tool"]
        if results:
            reply = {"status": "completed", "evidence_ids": [r["evidence_id"] for r in results]}
            if len(results) == 1 and results[0]["tool"] == "get_score_summary":
                tokens = results[0].get("narrative_slots", [])
                scope = tokens[0] if tokens else None
                mean = next((token for token in tokens if token.endswith(".mean}}")), None)
                rate = next((token for token in tokens if token.endswith(".pass_rate}}")), None)
                if scope and mean and rate:
                    reply["narrative"] = [f"Looking at {scope}, {mean}, while {rate}."]
            content = json.dumps(reply)
            return ModelTurn(message=ModelMessage(role="assistant", content=content))
        question = next(m["content"] for m in messages if m["role"] == "user")
        if question.startswith("ACADEMIC_INTENT "):
            # Offline fixture: replay a locally validated plan. This is not an
            # LLM and does not infer entities or consume the original question.
            plan = strict_json(question.removeprefix("ACADEMIC_INTENT "))
            return ModelTurn(message=ModelMessage.model_validate({"role": "assistant", "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": plan["tool"],
                    "arguments": json.dumps(plan["arguments"])}}]}))
        courses = re.findall(r"\bg[1-3]-c[0-9]{2}\b", question)
        classes = re.findall(r"\bc[0-9]{3}\b", question)
        term = re.search(r"\b20[0-9]{2}-t[1-3]\b", question)
        grade = re.search(r"\bgrade ([1-3])\b", question)
        selected_term = term[0].upper() if term else "2026-T1"
        if "compare" in question and len(courses) == 1 and len(classes) >= 2:
            name, arguments = "compare_classes", {"course_id": courses[0].upper(), "class_ids": [c.upper() for c in classes], "term": selected_term}
        elif "compare" in question:
            return ModelTurn(message=ModelMessage(role="assistant", content='{"status":"needs_clarification","evidence_ids":[]}'))
        elif any(word in question.split() for word in ("catalog", "list", "available")):
            name, arguments = "get_catalog", {"grade_level": int(grade[1]) if grade else None}
        elif len(courses) == 1 or grade or len(classes) == 1:
            name, arguments = "get_score_summary", {"term": selected_term, "course_id": courses[0].upper() if len(courses) == 1 else None,
                "class_id": classes[0].upper() if len(classes) == 1 else None, "grade_level": int(grade[1]) if grade else None}
        elif "how many" in question or "overview" in question or "dataset" in question:
            name, arguments = "get_dataset_overview", {}
        elif any(word in question.split() for word in ("average", "mean", "pass", "distribution", "failures")):
            name, arguments = "get_score_summary", {"term": selected_term, "course_id": None, "class_id": None, "grade_level": None}
        else:
            return ModelTurn(message=ModelMessage(role="assistant", content='{"status":"needs_clarification","evidence_ids":[]}'))
        return ModelTurn(message=ModelMessage.model_validate({"role": "assistant", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}))


def build_provider(settings: LLMSettings) -> ChatProvider:
    return MockDemoProvider() if settings.provider == "mock" else OpenAICompatibleProvider(settings)
