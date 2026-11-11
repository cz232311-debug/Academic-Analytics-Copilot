# Phase 3B — Chat-first MVP: file inventory and verification record

Baseline snapshot: `ad52867` (`feat: bind resolved academic intents to the configured model loop`) on branch `feature/chat-first-agent`. `main` remains at `d7c010a`. Two pre-existing uncommitted files were preserved and are still uncommitted: the owner-owned offline demo receipt `docs/development/results/14-deepseek-offline-demo.json` and the local workspace memory file.

## 1. Scope and decisions

Goal: turn the trusted Phase 3A backend into a usable Chat-first system without a new Agent framework, a second LLM validator, Word export or multi-model support.

Three decisions shaped the increment:

1. **Entry point.** `streamlit_app.py` is started directly by nine `AppTest.from_file()` cases that assert the legacy five-step workspace (`session_state.workflow`, exact button labels). It is therefore left **unchanged** as the legacy entry, and a new `chat_app.py` renders the Chat-first first screen. `scripts/start_dashboard.ps1` now launches `chat_app.py`, so the first screen a user actually opens is the new one. No existing test was deleted, skipped, weakened or repointed.
2. **`get_catalog` side query.** `semantics.validate_intent_call()` previously allowed a parameter-matching `get_catalog` call even when the resolved intent named another tool. The catalog is already resolved on the server and `GROUNDED_SYSTEM` explicitly forbids the model from looking up alternatives, so this exception only let the model execute a tool the user never requested and attach extra Evidence. It was removed; three targeted tests cover rejection, end-to-end blocking and no false rejection when `get_catalog` really is the intent.
3. **Chinese answers.** The backend's English evidence-token narrative and numeric validation are untouched. The UI renders a second, deterministic Chinese conclusion from `AgentResponse.evidence[i].data` only. The model cannot write a number into that text and no causal claim is generated. The original server answer is kept inside the collapsed technical panel for cross-checking.

## 2. Files changed

| File | Status | Responsibility |
|---|---|---|
| `src/copilot/dashboard/chat.py` | new | Chat-first view: intro, sidebar data source, conversation, example questions, deterministic Chinese conclusions, charts, evidence/technical expanders, Chinese error guidance. No statistics, no DB, no model import. |
| `chat_app.py` | new | Recommended Streamlit entry point (`streamlit run chat_app.py`). |
| `src/copilot/dashboard/client.py` | modified | Typed HTTP boundary: added `resolve()`, `synthetic_preview()`, `synthetic_commit()`, optional `execution_mode`/`scope`/`expected_version` on `query()`, and Chinese actionable messages for 14 more error codes. The legacy `query(question)` one-field request body is byte-for-byte unchanged. |
| `src/copilot/agent/semantics.py` | modified | Removed the `get_catalog` side-query exception from `validate_intent_call()`; tightened the docstring. `resolve_question()` is unchanged. |
| `scripts/start_dashboard.ps1` | modified | Launches `chat_app.py`; prints a note about the legacy workspace. |
| `README.md` | modified | Current interface, two provenance modes, startup commands (`-Provider mock` for offline), legacy entry note. |
| `docs/development/02b-workbench.md` | modified | Phase 3B progress index entry. |
| `tests/test_phase3b_backend.py` | new | 17 Mock tests: server-side synthetic activation, client payload contracts, legacy query contract, uploaded-data privacy boundary, Chinese error messages, extra-tool trust boundary (unit + end-to-end + no false negative). |
| `tests/test_chat_app.py` | new | 12 Streamlit AppTest cases over a real FastAPI app via MockTransport: first open, preview/confirm, grounded mode, Chinese course/class questions, upload → local only, clarification/unsupported, LLM configuration error, connection error, version switch, existing Dashboard API contract. |
| `docs/development/phase3b-review/LEARNING.zh-CN.md` | new | Chinese learning packet (call chain, three core functions, demo questions, limits). |
| `docs/development/phase3b-review/FILES.md` | new | This inventory. |
| `docs/development/phase3b-review/*.png` | new (8) | Browser acceptance screenshots described in section 5; the last three record the follow-up revision in section 8. |

**Deliberately unchanged:** `streamlit_app.py`, `src/copilot/dashboard/{app,workbench,import_panel,synthetic_panel,presentation,workflow}.py`, `src/copilot/api.py`, `src/copilot/agent/{service,models,tools,narrative,privacy,provider}.py`, `analytics.py`, `storage.py`, `schemas.py`, `college*.py`, `synthetic_activation.py`, all pre-existing tests, and `conftest.py` (which still forces `COPILOT_LLM_PROVIDER=mock`).

## 3. Actual call chain

```
Browser → chat_app.py → chat.main(client_factory=None)
  ├─ client.health() / client.dataset()            GET /health, /api/dataset   (version guard)
  ├─ sidebar: server synthetic
  │    client.synthetic_preview(SyntheticOptions)  POST /api/datasets/synthetic/preview
  │      → SyntheticActivationService.preview()    (server generates + parses workbook)
  │    client.synthetic_commit(options, token)     POST /api/datasets/synthetic/commit
  │      → database.replace(source="generated")     external_llm_allowed = True
  │  sidebar: uploaded XLSX
  │    client.college_preview(name, bytes)         POST /api/datasets/college/preview
  │    client.college_commit(name, bytes, token)   POST /api/datasets/college/commit
  │      → source="uploaded_local_only"            external_llm_allowed = False
  ├─ client.catalog() / client.resolve(q, mode)   GET /api/reports/catalog, POST /api/agent/resolve
  │      → example questions are filtered to status="ready" only
  └─ client.query(q, execution_mode, scope, expected_version)  POST /api/agent/query
       ├─ generated → "grounded": resolve_payload(generated_only=True)
       │      → ACADEMIC_INTENT → DeepSeek Function Calling
       │      → registry.validate() → validate_intent_call() → registry.execute()
       │      → AnalyticsEngine → Evidence → verified answer
       └─ uploaded → "local": AgentService._run_local(), provider=None, model_turns=0
   UI ← AgentResponse → _evidence_cn() (numbers from Evidence only) + charts + expanders
```

## 4. Test commands and actual results

All commands ran from the repository root with `.venv/Scripts/python.exe`. `tests/conftest.py` forces Mock; **no paid DeepSeek request was made** in this increment.

```
pytest -q --tb=short --basetemp=.test-tmp-p3b-base
    → 427 passed, 1 warning in 141.20s                     (Phase 3B starting baseline)
pytest -q ... tests/test_phase3b_backend.py test_agent_semantic_integration.py \
              test_dashboard.py test_agent_api.py test_agent_service.py
    → 111 passed, 1 warning in 21.13s                     (increment 1 + regressions)
pytest -q ... tests/test_chat_app.py
    → 3 failed / 9 passed → fixed test helpers            (round 1)
    → 2 failed / 10 passed → fixed sample wording        (round 2)
    → 12 passed, 1 warning in 17.16s                     (round 3)
pytest -q --tb=short --basetemp=.test-tmp-p3b-full
    → 456 passed, 1 warning in 163.06s, 0 failures        (complete suite)
pytest -q ... tests/test_chat_app.py tests/test_phase3b_backend.py
    → 29 passed, 1 warning in 7.69s                       (after final UI polish)
pytest -q --tb=short --basetemp=.test-tmp-review-full
    → 461 passed, 1 warning in 88.00s                     (follow-up revision, section 8)
pytest -q ... tests/test_college_adapter.py tests/test_chat_app.py
    → 42 passed, 1 warning in 9.73s                       (follow-up, after preview caption polish)
pytest -q --tb=short --basetemp=.test-tmp-final-handoff
    → 461 passed, 1 warning in 77.77s                     (final full re-run before commit)
pytest -q ... 4 college/chat/import modules
    → 63 passed, 1 warning in 13.41s                      (after removing dead preview constants)
```

The only warning is the pre-existing Starlette `TestClient` deprecation. No test was deleted, skipped or weakened. UI test failures were all in the test harness (multipart body parsed as JSON; AppTest `clear()` clears attachments, not text; a compact Chinese question that the semantic layer's `\b` class-ID regex cannot match) — each was fixed without changing product assertions.

## 5. Actual runtime and browser acceptance

Two independent services were started for acceptance (the user's existing 8000/8501 services were left untouched):

```
ACADEMIC_DB_PATH=data/phase3b-uat.sqlite3 COPILOT_LLM_PROVIDER=mock \
  .venv/Scripts/python.exe -m uvicorn copilot.api:app --host 127.0.0.1 --port 8010
COPILOT_API_BASE_URL=http://127.0.0.1:8010 \
  .venv/Scripts/python.exe -m streamlit run chat_app.py --server.port=8511 --server.headless=true
```

Real HTTP checks: backend `/health` → `{"status":"ok","dataset_active":false}`, `/api/agent/status` → `provider=mock, simulated=true`, `/api/dataset` → 503 `no_dataset` (no dataset is auto-activated), Streamlit `/_stcore/health` → `ok`.

Browser: headless Chrome 157 driven over CDP (`websockets`), waiting for actual DOM text rather than a fixed delay. Screenshots:

| File | Check |
|---|---|
| `first-open.png` | Title fully visible, four supported areas readable, sidebar data source present, chat input visibly disabled, no truncation/overlap/low contrast. |
| `synthetic-activated.png` | Server preview → checkbox → confirm produced `source=generated`; mode label and six catalog-verified examples visible. |
| `answer-with-evidence.png` | Chinese conclusion, honest "离线 Mock 演示（合成数据，未调用 DeepSeek）" label, collapsible calculation evidence. |
| `answer-with-chart.png` | Course analysis with human-readable course name, exact mean/pass rate, distribution bar chart, evidence expander. |
| `preview-details.png` | Follow-up fix: the preview was moved into the wide main area (the sidebar was too narrow for tables), showing the detected course catalog and eight de-identified grade rows. **Historical screenshot** — that eight-row `学生0001` sample was replaced in the later revision (section 8). |
| `review-issues-with-coordinates.png` | Section 8: full issue list plus coordinate-located review cells. Problem cells such as row 6 / column E and row 7 / column F are visible with their original Excel coordinates; no re-numbered student alias. |
| `review-auto-restored.png` | Section 8: after confirming an upload and reloading the page, the sidebar still shows `上传的 Excel 成绩单 / 本地规则分析` and the main area shows the blue "已自动沿用" notice; the chat input is enabled. |
| `review-user-bubble-right.png` | Section 8: a real submitted question. The user message (red avatar) is flush right, the assistant answer (orange avatar) is left; no overlap. Measured `flex-direction: row-reverse`, `margin-left: 162.8px` (auto), 0 px gap to the parent's right edge and 163 px on the left. |

Observed conclusion (synthetic, reproducible): `课程 工程实践2`: 12 grade records, 12 graded, mean 74.00, pass rate 83.33% (10/12), distribution 2/3/3/3/1. These are outputs of `AnalyticsEngine`, not model prose.

Browser interaction was done with real CDP mouse events; a synthetic `input.click()` does not move Streamlit's React checkbox state, which was diagnosed and corrected. **Not verified in a browser:** real DeepSeek interaction, mobile/accessibility, cross-browser rendering, multi-turn model reasoning. Error guidance and version switching are covered by AppTest, not by browser clicks.

## 6. Implemented / partial / pending

**Implemented:** Chat-first first screen; two explicit provenance modes; server-side synthetic activation (no client-faked source); uploaded XLSX preview/review/confirm; `grounded` for synthetic and `local` for uploads, always explicit; honest per-answer mode labels; deterministic Chinese conclusions from Evidence; charts and expandable evidence; catalog-verified example questions; dataset-version invalidation; Chinese actionable errors; `get_catalog` side-query removal; 29 new Mock tests; real service + browser acceptance.

**Partial / boundaries:** Chat history is display-only; the model never receives previous turns. At this checkpoint scope reuse was opt-in (`后续问题沿用上一题的范围`) and explicit conditions always won — that control was **removed in the follow-up revision (section 8)** because it was redundant. `AgentResponse.stages` and SSE streaming are unimplemented, so the page uses a plain spinner and does not fabricate stage logs. Chinese answers are a display projection, not a backend narrative feature.

**Pending / next-round bugfix and evaluation suggestions:**
1. The legacy `configured` English path still uses the old vocabulary gate and bypasses semantic constraints. The new UI always sends `grounded`; that legacy path exists only to keep historical `test_agent_service.py` / `test_agent_api.py` assertions intact. Retire it after those contracts are deliberately migrated.
2. `semantics.resolve_question()` uses `\bC(\d{1,3})\b` for class IDs. In compact Chinese such as `比较C001和C002的成绩` (no spaces) the word boundary fails because Chinese characters are `\w`. Use an ASCII-aware lookaround instead, with regression tests. Example questions currently use spaced IDs.
3. No live DeepSeek Chinese end-to-end run (paid calls forbidden). Next: an explicitly authorized single live request plus an Agent Evaluation matrix (tool choice, parameter drift, ambiguity, version changes, privacy).
4. Mobile/accessibility/cross-browser checks; real uploaded workbook with an authorized sample; `stages` post-hoc event records if progress detail is wanted.

## 7. Cumulative diff and working tree

Cumulative diff from `d7c010a` through this checkpoint covers the WIP checkpoint `31834ad`, baseline repair `63315a0`, Phase 3A `ad52867` and this Phase 3B commit. Per-commit patches are available with `git show <hash>`. This packet is authored before its own commit and therefore cannot recursively include that commit's diff.

Working-tree changes after the commit (intentionally left uncommitted): the owner-owned offline demo receipt and the local `.workbuddy/memory/*.md`. Ignored local artifacts: `.test-tmp-*`, `.test-tmp-probe/` (throwaway CDP/probe scripts), `data/phase3b-uat.sqlite3`, `data/review-uat*.sqlite3`. Screenshots contain only an empty, synthetic or deliberately fabricated dataset; no student record, key, `.env`, database or private log is included in the review packet.

## 8. Follow-up revision: review usability and cross-session continuity

Triggered by four operator observations after the MVP was validated in a real browser: (1) the "会话范围" scope control felt redundant — once a file is uploaded it should stay in use until the operator actively changes it; (2) `学生0001` did not match the original workbook, the de-identification was useless, and eight sample rows hid most of the cells needing correction; (3) user chat bubbles should be on the right; (4) progress made in one AI conversation should be reproducible in a fresh one.

| Concern | Change | Files |
|---|---|---|
| Redundant scope | Removed `_scope_panel()` and the `chat_reuse_scope` / `chat_clear_scope` keys. `_ask()` no longer sends a hidden `scope`; every question is resolved on its own. | `src/copilot/dashboard/chat.py`, `tests/test_chat_app.py` |
| Dataset persistence | `_observe_dataset()` now restores an already-confirmed `uploaded_local_only` dataset once per page session and shows a blue "已自动沿用" notice. Synthetic data is deliberately **not** restored: it is the only path that can reach a paid model and therefore still needs an explicit confirmation each session. Version changes still reset the chat. | `src/copilot/dashboard/chat.py`, `tests/test_chat_app.py` |
| Real coordinates | The old `sample` was built from `students` sorted by student ID and re-numbered as `学生0001`, so it could not be matched to the source sheet. `college.py` now builds `review_cells` keyed by the **original Excel row number and column letter** (`get_column_letter`), puts problem cells first, and exposes `sample_total` / `issue_cell_total`. Bounded by `MAX_REVIEW_CELLS = 200` with an explicit omitted-count warning. Names, student IDs and unrecognized cell text are never echoed (`shown = "—"`). | `src/copilot/data/college.py`, `tests/test_college_adapter.py` |
| Full issue list | The main area now renders every `preview.issues` entry with severity, original row/column, a Chinese label and message, alongside the course catalog (with column letters) and the coordinate-located cell table. No `学生` column remains. | `src/copilot/dashboard/chat.py`, `tests/test_chat_app.py` |
| Right-aligned bubbles | CSS targets `[data-testid=stChatMessage]:has([data-testid=stChatMessageAvatarUser])` with `flex-direction:row-reverse` and `margin-left:auto`. | `src/copilot/dashboard/chat.py`, `tests/test_chat_app.py` |
| Continuity | New `HANDOFF.zh-CN.md` carries a paste-ready prompt for a fresh conversation, the source-of-truth hierarchy, current state, hard constraints and open items. `README.md` and `START_HERE.zh-CN.md` were synced. | `HANDOFF.zh-CN.md`, `README.md`, `START_HERE.zh-CN.md`, this file |

**Verification.** Targeted suites: 49 passed, then 42 passed after the preview caption polish, then 63 passed across four modules after dead-code cleanup. Exclusive full suite: **461 passed, 1 warning in 88.00s**, re-confirmed at **461 passed in 77.77s** immediately before the commit; zero failures/skips. Browser acceptance used an isolated backend on 8010 with `COPILOT_LLM_PROVIDER=mock`, an isolated Streamlit on 8511, an isolated SQLite database, and headless Chrome on 9222; the user's 8000/8501 services were untouched and no paid model call was made. Real-browser checks: no scope control on first open; right-alignment CSS present; uploading a workbook with deliberately bad cells shows the complete issue table and coordinate-located cells; `学生0001` absent; the literal strings `NOT_A_GRADE` / `BAD_STATUS` absent from the DOM; after confirm + reload the dataset is auto-restored and the chat input is enabled; a submitted question produces a user bubble with `flex-direction: row-reverse`, `margin-left: 162.8px` (auto), 0 px to the parent's right edge and 163 px of left inset.

Two CDP harness issues were diagnosed and fixed rather than papered over: a synthetic `Input.dispatchKeyEvent` Enter does not submit Streamlit's `chat_input`, so the check clicks a catalog-verified example button with real CDP mouse events (which also produces a genuine user message); and a coarse `body.innerText` substring check reported `has 第6行/E: False` even though the screenshot clearly showed those coordinates, because the dataframe renders them in separate elements.

**Unchanged hard boundaries:** `import_archive.py` still persists only `original.xlsx`, `cleaning.json` and `confirmation.json` — never `sample` or raw cell text; uploaded data stays `external_llm_allowed=false`; Chinese conclusions still come only from backend Evidence numbers. Open items in section 6 remain open (ASCII-aware class-ID boundary, authorized live DeepSeek evaluation, `stages`/SSE, multi-turn context, mobile/accessibility/cross-browser).
