# Phase 2A — controlled LLM Function Calling

Date: **2026-11-04, Asia/Shanghai**. This increment reuses the existing architecture, schema, metric definitions and evaluation specification. It implements one bounded model/tool loop; no Agent framework, Dashboard, RAG or vector store was added.

## Review and reuse

The existing core already supplies exact integer/Decimal statistics, missing/exempt semantics, SQL/Pandas checks, typed IDs, SQLite transactions, provenance and dataset hashes. `AnalyticsEngine.summary` and `compare_classes` remain the sole statistical implementations. Overview/catalog reuse the repository. The existing 74 tests passed before changes. Only optional read guards were added to the existing engine/repository; ordinary deterministic API behavior remains available for local uploads.

## Files added/changed

| File | Actual change |
|---|---|
| `src/copilot/agent/models.py` | Strict argument, model-message, trace, evidence and query/response contracts; duplicate/nonfinite JSON rejection; safe Agent errors |
| `src/copilot/agent/tools.py` | Four explicit dispatch targets; schemas; existing-core delegation; output limits and result digests |
| `src/copilot/agent/provider.py` | Environment configuration, bounded Chat Completions HTTP adapter, response validation and an explicitly simulated offline demo |
| `src/copilot/agent/privacy.py` | Local restricted-English domain gate; student identifiers and unsupported vocabulary blocked before model calls |
| `src/copilot/agent/service.py` | One bounded loop, JSON events, safe traces, result-reference validation and deterministic English rendering |
| `src/copilot/agent/__init__.py` | Package marker |
| `src/copilot/storage.py`, `analytics.py` | Optional provenance/version guards inside each data-read transaction; no duplicate statistics |
| `src/copilot/api.py` | `/api/agent/status`, `/api/agent/query`, 8 KiB query-body cap, trace-bearing errors and version 0.2.0 |
| `tests/test_agent_tools.py`, `test_llm_provider.py`, `test_agent_service.py`, `test_agent_api.py` | 79 additional tests, including parametrized cases; no paid API dependency |
| `scripts/smoke_agent.py` | Actual TCP/HTTP app-to-model-protocol check using a local scripted HTTP server and temporary generated database |
| `.env.example`, `pyproject.toml`, README, NOTICE, existing plan status | Config reference, HTTPX promoted to a runtime dependency, current instructions and attribution |
| `docs/development/results/07-*` through `12-*`, `phase2a-http-smoke.json` | Actual test and integration evidence, including initial failures |

The existing version lock already contains the tested HTTPX 0.28.1, so no new external dependency version was required. The editable package was refreshed to 0.2.0 and `pip check` reported no broken requirements. The upstream license remains unchanged; no upstream app logic was copied.

## Four controlled tools

| Tool | Strict arguments | Existing implementation |
|---|---|---|
| `get_dataset_overview` | Empty object only | `Database.overview` |
| `get_score_summary` | Required term plus nullable course/class/grade filters | `AnalyticsEngine.summary(Scope(...))`; includes means, rates, distribution and failure counts |
| `compare_classes` | Course, term, 2–10 distinct class IDs | `AnalyticsEngine.compare_classes`; exact comparison and existing sample sizes |
| `get_catalog` | Required nullable grade level | `Database.catalog`, filtered metadata only |

There is no SQL/Python/shell tool, dynamic function lookup, path argument or arbitrary query expression. Arguments are checked locally with strict Pydantic models even when remote schema strictness is disabled. Numbers-as-strings, bool-as-grade, unknown fields, duplicate JSON keys, nonfinite constants, duplicate class IDs, invalid identifiers and unknown tool names are rejected. Tool outputs contain aggregates/catalog entries, never student rows or names.

## Actual execution flow

1. `POST /api/agent/query` validates a bounded JSON body containing only `question`.
2. The service issues a request ID and reads generated-data provenance/version. It canonicalizes an allowed English aggregate question locally; unrestricted text is not forwarded.
3. The provider receives the canonical question and four function schemas. It returns an assistant `tool_calls` message. A real HTTP provider chooses the functions; the default mock only simulates that decision.
4. The service validates the name/JSON arguments and dispatches the fixed wrapper. The underlying data read checks provenance/version in its transaction. Unknown terms/entities and empty scopes retain the existing deterministic semantics.
5. The server creates request-scoped evidence (`r1`, etc.), computes a result digest and records a sanitized trace. It sends the aggregate JSON back in a `role: tool` message with the original `tool_call_id`.
6. The model can request another tool round, such as catalog → statistics. Limits are four model turns, six tool calls, bounded provider output and an overall deadline. Calls are sequential; there are no silent retries.
7. The model returns strict final JSON such as `{"status":"completed","evidence_ids":["r1"]}`. References must point to successful results from this request. The server fills the factual English answer from selected evidence. Clarification/unsupported states have fixed safe text.

This is intentionally result selection plus rendering, rather than unrestricted model-written numerical prose. It prevents unsupported numbers from entering the displayed factual answer, but does not establish that a model selected the user's intended course/metric. Its filters and evidence remain visible. A general claim-path/derived-arithmetic/report system remains outside this increment.

The HTTP protocol was checked against the official [Function Calling guide](https://developers.openai.com/api/docs/guides/function-calling) and [Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). The adapter is a tested protocol implementation, not a claim of live compatibility with every “OpenAI-compatible” service. Provider/model support must be verified after configuration.

## Executed verification

| Gate | Actual result | Evidence |
|---|---|---|
| Existing baseline | 74 passed, 1 existing warning | [07-phase2a-baseline.xml](results/07-phase2a-baseline.xml) |
| Registry + existing analytics | 31 passed | [08-phase2a-tools.xml](results/08-phase2a-tools.xml) |
| First provider/registry run | **4 failed, 33 passed** | [09-phase2a-provider.xml](results/09-phase2a-provider.xml) |
| Provider fix and rerun | 37 passed | [09-phase2a-provider-fixed.xml](results/09-phase2a-provider-fixed.xml) |
| Initial full loop | 62 passed | [10-phase2a-loop.xml](results/10-phase2a-loop.xml) |
| API integration + full regression | 152 passed, 1 warning | [11-phase2a-api-full.xml](results/11-phase2a-api-full.xml) |
| Final full suite, including three-turn catalog → statistics → answer | **153 passed, 0 failed/errors/skipped; 1 warning** | [12-phase2a-final.xml](results/12-phase2a-final.xml) |
| Local app ↔ HTTP adapter ↔ scripted HTTP model | **6/6 passed; two upstream HTTP requests; both servers stopped** | [phase2a-http-smoke.json](results/phase2a-http-smoke.json) |

Final suite breakdown: 74 existing tests; 15 registry tests, 22 provider tests, 27 loop/privacy/rendering tests and 15 new API tests. These are **not** the 36 English model evaluation questions and do not measure real LLM accuracy.

The HTTP smoke used a real Uvicorn process, real HTTPX requests, a local scripted Chat Completions server, a fake key and a temporary generated dataset. It observed G1-C01 average **69.73**, pass rate **67.80%**, and **469** graded records, then verified tool output reached the model endpoint. It also blocked a student-ID question and a source changed to uploaded data before any additional model request. The user's persistent database was untouched.

## Observed problems and fixes

- Four provider tests exposed incorrect error classification: `AgentError` inherits `ValueError` through the existing domain error. A broad response-parse `ValueError` catch swallowed HTTP/429 errors. Catching/rethrowing `AgentError` first fixed all four; the initial failed report is retained.
- Chat Completions may return `tool_calls: null` and extra usage-detail fields. The parser normalizes null calls to an empty list and validates the known usage counters while ignoring vendor metadata. Dedicated fixtures verify both cases.
- Privacy checks only before a model request would leave an upload race. Optional guards now check source/version in the same repository transaction as every tool read. Tests replace data between model selection and execution; the read is blocked.
- Rounded class means can tie although raw means differ; the existing exact-rational engine behavior is reused, not reimplemented by the Agent.
- Sandbox temporary-file permissions blocked the editable metadata refresh. The approved scoped local install succeeded; no packages or models were downloaded for that refresh.
- One pre-existing Starlette warning remains: its TestClient deprecates the HTTPX adapter. It is preserved, not suppressed; all tests and real local HTTP checks passed.

## Run and debug

From the repository root after the existing environment is installed:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q --basetemp=.test-tmp-agent-manual
& '.\.venv\Scripts\python.exe' scripts/smoke_agent.py
```

The [README](../../README.md) gives full PowerShell startup/query examples and interactive secret entry. `.env.example` is a reference only, not an auto-loaded configuration file. Use `COPILOT_LLM_PROVIDER=mock` for a free local demonstration; live HTTP mode requires model ID/key/base URL in process environment variables. Incorrect LLM configuration returns a sanitized 503 without breaking the deterministic APIs.

Debug with `/api/agent/status`, then inspect the response's request ID, tool arguments, status, evidence and dataset hash. Errors preserve safe completed/failed tool traces. Expected error classes include 422 query policy/schema/refusal, 403 uploaded-source block, 404 unknown entity, 409 dataset change, 502 protocol/model/tool errors, 503 configuration/limit/service errors and 504 timeout. JSON events omit raw questions, invalid raw arguments, provider bodies and secrets. No incoming LLM response is executed as code.

## Not completed or not verified

No real model/provider call, credentials, external connectivity, paid usage or model answer-quality score was verified. Mock mode has limited English routing; the local HTTP stub is also scripted. Non-ASCII/general unrestricted questions are deliberately unsupported in this privacy-constrained increment. No streaming, RAG, vector database, multi-Agent framework, Streamlit Dashboard, full 36-question evaluation or Docker was implemented. Next is Phase 2B's Streamlit client against the existing structured API/trace contract.
