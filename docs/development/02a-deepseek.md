# Phase 2A DeepSeek and grounded prose

November 6, 2026 (Asia/Shanghai). The implementation and offline verification are complete. The single paid acceptance query awaits explicit user authorization and a credential in the execution environment.

## Changes

| File | Responsibility |
|---|---|
| `src/copilot/agent/provider.py` | Add the official DeepSeek profile to the existing HTTP adapter. Use `deepseek-flash`, disable thinking, request JSON output, use `max_tokens`, and surface safe credential/balance errors. |
| `src/copilot/agent/models.py` | Add narrative and guidance fields, answer provenance, and per-turn provider IDs and usage. |
| `src/copilot/agent/narrative.py` | Validate the model's English sentence structure, scope and evidence tokens. Fill statistics and verified descriptive clauses from tool results. |
| `src/copilot/agent/service.py` | Return evidence and available slots to the model, record model turns, recheck the dataset before publication, and label fallback or validated narrative. |
| `src/copilot/api.py` | Include safe model traces in Agent errors. |
| `tests/test_deepseek_narrative.py` | Verify the DeepSeek wire format, second-turn evidence, token metadata, valid composition and rejection paths. |
| `scripts/smoke_deepseek.py` | Preview the paid test, run the offline full dataset example, or execute one authorized acceptance query and save its receipt. |
| `.env.example`, `README.md`, `results/13-deepseek-offline.xml`, `results/14-deepseek-offline-demo.json` | Configuration, commands and recorded offline evidence. |

DeepSeek's standard origin uses `thinking: {"type":"disabled"}` and `max_tokens`. Tool results carry the matching `tool_call_id`. The standard profile omits the remote `strict` flag because the documented strict-tool mode uses the beta endpoint. The application's Pydantic validation still applies to every tool call. [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/) · [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)

## Request and verification chain

1. `POST /api/agent/query` accepts the English question. The local gate canonicalizes allowed tokens and checks that the dataset was generated locally.
2. The model receives four schemas and proposes a tool. The existing registry validates the arguments and invokes the existing analytics or catalog method.
3. The adapter sends the aggregate evidence and available scoped tokens in a tool message. The model composes and orders English sentences such as `Looking at {{r1.scope}}, {{r1.mean}}, while {{r1.pass_rate}}.`
4. The backend rechecks provenance and dataset version, then validates each sentence against the permitted grammar and its scope. It fills the scoped facts from evidence and attaches the corresponding citation. Distribution ties and comparison leaders come from the aggregate data. Guidance codes are checked against the selected evidence and exposed separately.
5. A valid answer sets `answer_mode=grounded_narrative`. A missing or unsupported narrative with valid evidence sets `answer_mode=deterministic_fallback` and records `fallback_reason`. Malformed final JSON or invalid evidence IDs return a typed error with safe traces.

The controlled grammar is a design choice. The model selects the emphasis, sentence order, wording connectors and optional suggestions. The backend supplies factual clauses and numerical values. The validator can establish scope and value consistency. Whether a model selected the intended analysis still requires review or evaluation. The four-tool whitelist, time budgets and prohibition on executable model arguments apply throughout.

## Observed results

- **167 passed**, with one pre-existing TestClient deprecation warning. This includes 153 prior tests and 14 new tests. The DeepSeek round trip used MockTransport, including a scripted token total of 240 across two mocked completions. [JUnit report](results/13-deepseek-offline.xml)
- **Offline full dataset example passed.** The actual question was `What is the average score and pass rate for G1-C01?` The response was `Looking at course G1-C01 in term 2026-T1, the average score was 69.73 points, while 67.80% of graded enrollments passed (318 of 469). [r1]` It used two Mock turns and one successful `get_score_summary` call. [Offline receipt](results/14-deepseek-offline-demo.json)
- **Paid DeepSeek verification is pending.** The dry run showed the exact question, endpoint and budgets with zero outbound requests. No real response, latency, model accuracy or billable token count has been measured.

The response groups `tool_calls`, `evidence`, `dataset_version` and `model_trace` by `request_id`. Each tool trace records validated arguments, latency, evidence ID and result digest. Each model trace records its turn, tool count, provider response ID and prompt/completion/total tokens when reported. The aggregate `usage` is present only when every completed turn supplied usage. The offline receipt has `null` usage. Server JSON events record the validation outcome without persisting raw prompts, provider response bodies or keys.

## Review and authorize the live check

In the project directory, preview the exact query and run local checks.

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
& '.\.venv\Scripts\python.exe' scripts/smoke_deepseek.py
& '.\.venv\Scripts\python.exe' -m pytest -q --basetemp=.test-tmp-deepseek-manual
& '.\.venv\Scripts\python.exe' scripts/smoke_deepseek.py --offline
```

Set `COPILOT_LLM_PROVIDER=deepseek`, `COPILOT_LLM_BASE_URL=https://api.deepseek.com` and `COPILOT_LLM_MODEL=deepseek-flash`. Read the API key through the masked PowerShell prompt in the same terminal and set `COPILOT_LLM_API_KEY` for that process. See the startup snippet for the exact commands. [README](../../README.md)

After explicit authorization, execute the following command once in an environment holding that credential.

```powershell
& '.\.venv\Scripts\python.exe' scripts/smoke_deepseek.py --live
```

The command uses a temporary generated database and calls the FastAPI query path through TestClient. It submits one Agent question, with up to four billable Chat Completions requests and 2,048 output tokens per request. Input tokens also count toward usage. It verifies the narrative path, exact course metrics, citation, dataset version and provider usage. The receipt lands in `.audit/deepseek-live.json`. A failed call or fallback answer fails acceptance and produces a receipt for diagnosis.

## Privacy limits and Chinese queries

The current gate only accepts a restricted English vocabulary, approved academic identifiers and generated data. It rejects non-ASCII input before contacting the provider. Common words can still coincide with names, so the vocabulary check cannot establish that arbitrary text is free of personal information.

Chinese support would require local Unicode normalization, an explicit Chinese analytics vocabulary and local parsing into the same structured course, class, term and metric intents. Approved catalog aliases could map Chinese course names to known IDs. Unknown spans, student IDs and personal identifiers should be rejected locally. Tests should cover mixed scripts, full-width IDs, confusables and private input. The provider would receive the canonical aggregate intent. This increment leaves the English privacy gate in place.

Phase 2B covers the Dashboard and chat UI. Phase 3 covers the full Agent evaluation and deployment. Live DeepSeek behavior and answer quality remain to be measured.
