import asyncio
import json

import httpx
from pydantic import SecretStr
import pytest

from copilot.agent.models import AgentError
from copilot.agent.provider import LLMSettings, OpenAICompatibleProvider
from copilot.agent.tools import ToolRegistry


def settings(**kwargs):
    return LLMSettings(provider="openai_compatible", model="test-model", api_key=SecretStr("fake-test-key"), **kwargs)


def completion(message=None, finish="stop", **kwargs):
    return {"choices": [{"message": message or {"role": "assistant", "content": '{"status":"completed","evidence_ids":["r1"]}', "tool_calls": None},
                         "finish_reason": finish}], **kwargs}


def test_http_protocol_authentication_and_usage_details():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=completion(usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
                                                          "prompt_tokens_details": {"cached_tokens": 0}}))

    result = asyncio.run(OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(handler)).complete(
        [{"role": "user", "content": "average score"}], ToolRegistry.definitions()))
    assert result.usage.total_tokens == 13
    request = seen[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer fake-test-key"
    payload = json.loads(request.content)
    assert payload["parallel_tool_calls"] is False and payload["store"] is False
    assert payload["model"] == "test-model" and len(payload["tools"]) == 4
    assert "fake-test-key" not in request.content.decode()


def test_from_env_trims_pasted_key_before_it_reaches_the_provider(monkeypatch):
    # A hidden prompt paste often carries leading/trailing spaces or a newline. The
    # emptiness check already stripped, but the Authorization header used the
    # raw value, so DeepSeek answered 401 for an otherwise valid credential.
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("COPILOT_LLM_API_KEY", "  sk-pasted-with-edges \n")
    for name in ("COPILOT_LLM_BASE_URL", "COPILOT_LLM_MODEL", "COPILOT_LLM_STRICT_TOOLS",
                 "COPILOT_LLM_TIMEOUT_SECONDS", "COPILOT_AGENT_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    configured = LLMSettings.from_env()
    assert configured.api_key.get_secret_value() == "sk-pasted-with-edges"
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=completion())

    asyncio.run(OpenAICompatibleProvider(configured, transport=httpx.MockTransport(handler)).complete([], []))
    assert seen[0].headers["Authorization"] == "Bearer sk-pasted-with-edges"


@pytest.mark.parametrize("status,code", [(401, "llm_http_error"), (429, "llm_rate_limited"), (500, "llm_http_error"), (302, "llm_http_error")])
def test_http_errors_are_sanitized_without_redirects(status, code):
    def handler(request):
        return httpx.Response(status, text="PRIVATE-ERROR fake-test-key", headers={"Location": "https://other.invalid"})
    with pytest.raises(AgentError) as error:
        asyncio.run(OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(handler)).complete([], []))
    assert error.value.code == code and "PRIVATE" not in str(error.value) and "fake-test-key" not in str(error.value)


@pytest.mark.parametrize("exception,code", [(httpx.ReadTimeout, "llm_timeout"), (httpx.ConnectError, "llm_connection_error")])
def test_network_errors(exception, code):
    def handler(request):
        raise exception("PRIVATE-CONNECTION", request=request)
    with pytest.raises(AgentError) as error:
        asyncio.run(OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(handler)).complete([], []))
    assert error.value.code == code and "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{"message": {"role": "user"}, "finish_reason": "stop"}]},
                                    completion({"role": "assistant", "tool_calls": [{"id": "x", "type": "function", "function": {"name": "x", "arguments": "{}"}}]})])
def test_bad_response_schema(payload):
    with pytest.raises(AgentError) as error:
        asyncio.run(OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))).complete([], []))
    assert error.value.code == "invalid_model_response"


@pytest.mark.parametrize("payload,code", [(completion(finish="length"), "model_output_truncated"),
    (completion(finish="content_filter"), "model_refused"),
    (completion({"role": "assistant", "refusal": "Private refusal"}), "model_refused")])
def test_truncation_and_refusal(payload, code):
    with pytest.raises(AgentError) as error:
        asyncio.run(OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))).complete([], []))
    assert error.value.code == code


@pytest.mark.parametrize("content", [b"not json", b"x" * 131073, b'{"choices":[],"choices":[]}'])
def test_response_size_json_and_duplicate_keys(content):
    with pytest.raises(AgentError):
        asyncio.run(OpenAICompatibleProvider(settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))).complete([], []))


def test_settings_env_and_secret_redaction(monkeypatch):
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("COPILOT_LLM_API_KEY", "fake-test-key")
    monkeypatch.setenv("COPILOT_LLM_MODEL", "test-model")
    config = LLMSettings.from_env()
    assert "fake-test-key" not in repr(config)
    monkeypatch.setenv("COPILOT_LLM_TIMEOUT_SECONDS", "NaN")
    with pytest.raises(AgentError) as error:
        LLMSettings.from_env()
    assert "fake-test-key" not in str(error.value)


@pytest.mark.parametrize("url", ["http://remote.invalid/v1", "https://user:password@remote.invalid", "https://remote.invalid/?key=secret"])
def test_untrusted_url_configuration_rejected(url, monkeypatch):
    monkeypatch.setenv("COPILOT_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("COPILOT_LLM_API_KEY", "fake")
    monkeypatch.setenv("COPILOT_LLM_MODEL", "test-model")
    monkeypatch.setenv("COPILOT_LLM_BASE_URL", url)
    with pytest.raises(AgentError):
        LLMSettings.from_env()


def test_compatible_endpoint_can_disable_remote_strict_but_not_local_validation():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=completion())
    asyncio.run(OpenAICompatibleProvider(settings(strict_tools=False), transport=httpx.MockTransport(handler)).complete([], ToolRegistry.definitions()))
    assert all("strict" not in t["function"] for t in seen[0]["tools"])
    assert ToolRegistry.definitions()[0]["function"]["strict"] is True
