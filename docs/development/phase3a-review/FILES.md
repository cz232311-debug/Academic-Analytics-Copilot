# Phase 3A — file inventory and verification record

Date: 2026-11-11 · Branch: `feature/chat-first-agent` · Baseline: `63315a0` (398 tests passing)

Scope: wire the pre-existing, previously uncalled Academic Semantic Layer into the
configured DeepSeek Function Calling loop. No new Agent framework, no second LLM
validator, no Streamlit changes, no changes to the four tool signatures, the
analytics engine or the storage layer.

## Files changed in this round

| File | Kind | Change |
|---|---|---|
| `src/copilot/agent/models.py` | modified | `QueryRequest.execution_mode` accepts `grounded` in addition to `configured` / `local`. |
| `src/copilot/api.py` | modified | `agent_query` forwards the resolved `intent` to `AgentService`; non-ASCII questions are compiled through the semantic layer; clarification/unsupported notices never construct a provider; `/api/agent/resolve` uses `generated_only = execution_mode != "local"`. |
| `src/copilot/agent/service.py` | modified | `query()` accepts `intent`; new `_run_notice()`; `_run()` gains a grounded branch (`ACADEMIC_INTENT` + `GROUNDED_SYSTEM`), an intent/catalog version guard and `validate_intent_call()` before every tool dispatch; `resolved_scope` returned from the resolved intent. |
| `tests/test_agent_semantic_integration.py` | new | 29 Mock regression tests (see below). |
| `docs/development/phase3a-review/LEARNING.zh-CN.md` | new | Chinese learning packet: call chain, three core functions, privacy matrix, demo questions, limitations. |
| `docs/development/phase3a-review/FILES.md` | new | This file. |
| `docs/development/02b-workbench.md` | modified | Progress index entry. |

## Deliberately unchanged

- `src/copilot/agent/semantics.py` — reused as-is: `resolve_question()`, `ResolvedIntent`,
  `validate_intent_call()`, `MAJOR_ALIASES`, catalog-bound course/class/term resolution.
- `src/copilot/agent/tools.py`, `analytics.py`, `storage.py`, `schemas.py`, `models.py` (core),
  `narrative.py`, `privacy.py`, `provider.py` — all reused without modification.
- `src/copilot/dashboard/*`, `streamlit_app.py` — no UI work this round.
- All pre-existing tests: none deleted, skipped or weakened.

A field briefly added to `/api/agent/status` was removed after it broke
`DashboardClient.agent_status()` (`AgentStatus` forbids extra fields). The status
response contract is byte-for-byte unchanged.

## Call chain

```
POST /api/agent/query
  -> api.agent_query
     -> resolve_payload(generated_only=True)          # 403 on uploaded provenance
        -> store.overview() / store.reporting_catalog() / resolve_question()
        -> ResolvedIntent(status, tool, arguments, canonical_question, dataset_version)
     -> grounded = execution_mode=="grounded" or not question.isascii()
     -> agent_runtime() only when a model call is actually needed
     -> AgentService.query(question, scope, expected_version, execution_mode, intent)
        -> intent.status != "ready"  -> _run_notice()   # no provider, no model, no evidence
        -> execution_mode == "local" -> _run_local()    # deterministic, no external call
        -> intent is None            -> _run()          # legacy English gate, unchanged
        -> intent is not None        -> _run()          # same loop, grounded branch
             -> messages = [GROUNDED_SYSTEM, "ACADEMIC_INTENT {tool, arguments}"]
             -> provider.complete()
             -> registry.validate() -> validate_intent_call() -> registry.execute()
             -> Evidence -> available_tokens() -> render_narrative()
        -> AgentResponse
```

`intent=None` preserves the original English loop because `tests/test_agent_service.py`
asserts the exact normalized user content and `test_agent_api.py` asserts legacy
error codes for short English questions. `grounded` is not a second pipeline: it is
a branch inside the same `_run()`.

## Privacy boundaries

| Case | External model | Outcome |
|---|---|---|
| Generated data, Chinese aggregate query | ASCII `ACADEMIC_INTENT` only (catalog IDs + term + metric) | 200 completed with Evidence |
| Generated data, ambiguous / unknown entity / personal-data request | none (provider not even constructed) | 200 `notice` in Chinese |
| Uploaded data, configured or grounded | none | 403 `agent_privacy_block` before provider settings are read |
| Uploaded data, local | none | 200 `verified_localized`, `provider="local"`, `model_turns=0` |
| Dataset/version changed during resolution | none | 409 `dataset_changed` |
| DeepSeek/API failure | no fabricated fallback statistics | 5xx with an explicit error code |

The user's prose is never copied into `canonical_question`; it is built from
`tool` + validated `arguments` only. No personal record, name, student ID, API key
or uploaded row leaves the process.

## Regression coverage (new file)

`tests/test_agent_semantic_integration.py` — 29 tests:

- Chinese course ID, explicit `grounded`, legacy English path unchanged.
- Chinese entity resolution against a real server-generated college workbook:
  course names, `2024 级` / `软工` aliases, `1 班/2 班`, `2025-T1`, catalog IDs,
  `compare_classes` with correct `class_ids`, Evidence/arguments consistency.
- Model drift: `G1-C01 -> G1-C02`, changed class, changed term, wrong tool all
  blocked with 422 `intent_mismatch`, an error `ToolTrace` and no Evidence.
- Ambiguity, unknown course, missing term, unknown major, personal-data requests
  return Chinese notices with zero model calls.
- Stale `expected_version` (409) and stale `intent.dataset_version` (409).
- Uploaded data blocked (403) with `provider.requests == []`; local answers are
  labelled `local` / `verified_localized`, never DeepSeek.
- Provider error (502, no invented numbers) and fabricated narrative fallback.

## Commands and actual results

```
.venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=.test-tmp-p3a1 \
    tests/test_agent_service.py tests/test_agent_api.py tests/test_agent_local_mode.py \
    tests/test_deepseek_narrative.py tests/test_synthetic_activation.py tests/test_dashboard_http.py
    -> 1 failed, 83 passed (status-contract regression found and reverted)

.venv/Scripts/python.exe -m pytest -q tests/test_dashboard_http.py::test_http_dashboard_client_gets_actual_seeded_dataset
    -> 1 passed

.venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=.test-tmp-p3a4 tests/test_agent_semantic_integration.py
    -> 29 passed, 6.27s

.venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=.test-tmp-p3a-final
    -> 427 passed, 1 warning in 143.84s (0:02:23), 0 failures
```

The single warning is the pre-existing Starlette TestClient deprecation notice.
`tests/conftest.py` forces `COPILOT_LLM_PROVIDER=mock` and clears the API key for
every test. `OpenAICompatibleProvider` is exercised only with
`httpx.MockTransport`. **No paid DeepSeek request was made.** No live Uvicorn or
Streamlit process was started and no browser acceptance was performed.

## Owner-owned files left untouched

`docs/development/results/14-deepseek-offline-demo.json` remains modified but
uncommitted (a regenerated Mock receipt from the previous round).
`.workbuddy/memory/2026-11-11.md` remains untracked local workspace memory.
`.test-tmp-*/` directories hold ignored probe scripts and pytest logs. None were
deleted, staged or committed.

## Known limitations / next increment

1. No authorized live DeepSeek Chinese end-to-end verification (paid call forbidden).
2. Legacy `configured` English path still uses the old English vocabulary gate and
   is not semantically constrained; the Chat-first UI should send `grounded`.
3. Answers are still English evidence-token templates; Chinese narrative localisation pending.
4. `AgentResponse.stages` is still empty; no execution-stage instrumentation and no SSE streaming.
5. `validate_intent_call` still permits a parameter-matching `get_catalog` side query;
   `GROUNDED_SYSTEM` instructs the model not to do so, but the hard check is unchanged.
6. The semantic layer is a bounded academic grammar, not a general PII detector;
   the privacy guarantee comes from forwarding only the structured intent.
7. Chat-first frontend not implemented this round.

Next: `dashboard/client.py` `resolve()` + `grounded`, conversational view,
example-data activation through `/api/datasets/synthetic/*`, then real acceptance,
debugging, Agent Evaluation and README/CV preparation.
