# Academic Analytics Copilot

An independent engineering portfolio project for SAP STAR Application Engineer (2028 Graduates).

**Implemented and tested: synthetic data, CSV/XLSX import, deterministic analytics, FastAPI and a bounded Function Calling workflow.** LLM protocol tests use HTTP mocks and a local scripted model server; no paid/live model has been verified. Streamlit, the full 36-question Agent evaluation and Docker remain deferred.

The owner's prior experience includes academic analysis and reporting covering 1,382 students and 40 classes. This demonstration uses entirely fabricated records; it contains no historical student data and claims no SAP integration or endorsement.

## Run locally

Tested on Windows x86-64 with Python **3.13.6**. Run these PowerShell commands from the repository root; activation is unnecessary:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements.lock.txt
.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
.venv/Scripts/python.exe -m copilot.data.generate --output data/demo
.venv/Scripts/python.exe -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000
```

Open [Swagger UI](http://127.0.0.1:8000/docs), [health](http://127.0.0.1:8000/health), or [course statistics](http://127.0.0.1:8000/api/analytics/summary?course_id=G1-C01). For the default dataset, that course has an average of `69.73`, a pass rate of `67.80%` and 469 graded records. These were checked over real HTTP.

First startup creates `data/academic.sqlite3` and seeds the demo only if no dataset exists. Restarting preserves an uploaded dataset. To use a separate database, set `$env:ACADEMIC_DB_PATH = 'data/my-demo.sqlite3'` before starting the service. CSV export is a separate, reproducible command; it does not replace the database. This is a loopback demo without authentication. No API key or external model is needed.

`requirements.lock.txt` records the tested dependency versions, including the local build tools. It is a version lock, not a hash-enforced distribution lock. The first installation needs PyPI access. Linux, containers and other Python versions have not been verified in this increment.

## Implemented data contract

The default seed is `20261103`, term `2026-T1`. Actual generated counts are **1,382 students, 40 classes, three grades, 96 catalog courses and 15,194 enrollments/final records**. Each student takes eight core courses and two to four electives within the student's grade. Grade populations are 484 / 449 / 449. There are 14,729 graded, 311 missing and 154 exempt records. The full scores matrix is never fabricated for all 96 courses per student.

| Table | Typed columns |
|---|---|
| `classes` | `class_id`: string ID; `grade_level`: integer 1–3; `class_label`: text |
| `students` | `student_id`: string ID; `class_id`: FK; `display_name`: exact `Synthetic Student NNNN` |
| `courses` | `course_id`: string ID; `course_name`: text; `grade_level`: integer; `credits`: integer 2–4 |
| `enrollments` | `enrollment_id`: string ID; `student_id` / `course_id`: FKs; `term`: typed term |
| `grade_records` | `grade_record_id`: string ID; `enrollment_id`: unique FK; `status`: graded/missing/exempt; `score_cents`: nullable integer 0–10,000 |

`score_cents=6000` means **60.00 points**. Graded records require a score; missing/exempt records require null. Every enrollment requires exactly one final record. Duplicate keys, duplicate student/course/term selections, orphan rows and cross-grade enrollments are rejected. Class IDs are never inferred to be scores.

Exports in `data/demo/` include five CSVs and a manifest with seed, counts and SHA-256 hashes. Generated data and local databases are ignored by Git; the code and [recorded manifest](docs/development/results/demo-manifest.json) reproduce them.

## Statistics and APIs

| Route | Behavior |
|---|---|
| `GET /health` | Service and dataset activation status |
| `GET /api/dataset` | Counts, populations, course loads, dataset hash and provenance |
| `GET /api/catalog` | Class/course IDs and labels for controlled filters |
| `GET /api/analytics/summary` | Filter by `term`, `grade_level`, `class_id`, `course_id` |
| `GET /api/analytics/compare-classes` | Compare 2–40 distinct `class_ids` on one required `course_id` and term |
| `POST /api/datasets/import` | Activate five CSV files or one five-sheet XLSX workbook atomically |
| `GET /api/agent/status` | Provider mode, model ID, four tools and request budgets; no keys |
| `POST /api/agent/query` | Restricted English aggregate question → controlled tool calls → evidence-grounded English answer |

Try a comparison in PowerShell:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/analytics/compare-classes?course_id=G1-C01&class_ids=C001&class_ids=C002'
```

The mean is over **graded enrollment records**, not over per-student means. Pass rate = graded scores ≥60 / graded records. Missing/exempt records never become zero; empty/no-graded scopes return null means and rates. Responses expose the exact sum, numerator, denominator, weighted numerator, credits, scope and dataset version. Decimal outputs use half-up rounding and serialize as JSON strings such as `"69.73"`; counts remain integers. Distribution bins are `[0,60)`, `[60,70)`, `[70,80)`, `[80,90)`, `[90,100]`. Distinct failing students and failed-record counts are separate. Class ranking uses unrounded rational means and stable class IDs.

Pandas owns deterministic result calculation; SQLite stores the normalized data and supports independent aggregate parity tests. All user filters use bound parameters. There is no code-execution or arbitrary-SQL endpoint. A comparison uses a single read snapshot; replacement uses one transaction and preserves the previous dataset on validation/insert failure.

## Import CSV or Excel

Use only synthetic inputs. CSV import requires five multipart file fields named `classes`, `students`, `courses`, `enrollments`, `grade_records`. Headers must match the table above exactly after trimming/lowercasing. Supply blank `score_cents` for missing/exempt records. UTF-8 BOM, surrounding whitespace, blank rows and status capitalization are cleaned explicitly. Invalid numeric cells and duplicates are rejected instead of silently dropped or coerced into missing values.

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/datasets/import `
  -F "classes=@data/demo/classes.csv;type=text/csv" `
  -F "students=@data/demo/students.csv;type=text/csv" `
  -F "courses=@data/demo/courses.csv;type=text/csv" `
  -F "enrollments=@data/demo/enrollments.csv;type=text/csv" `
  -F "grade_records=@data/demo/grade_records.csv;type=text/csv"
```

For Excel, send a single file field `workbook`, with `.xlsx` media type `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. The workbook must have exactly five visible sheets with the same table names/headers. Only literal cell values are accepted. `.xls`, `.xlsm`, formulas, external relationships, embedded objects, encryption and oversized ZIP expansion are unsupported. The Excel importer was exercised through real OOXML fixtures and the FastAPI upload endpoint.

Limits: 5 MiB/file, 26 MiB/actual HTTP body, 32 MiB/expanded workbook and table-specific row caps. Filenames are never used as storage paths. Type, size, field, primary-key and relationship validation precede activation. Expected errors use `{ "error": { "code": "...", "message": "..." } }` with HTTP 400/413/415/422/404 as appropriate and do not echo cell contents. Imported data is marked `uploaded_local_only` and cannot claim external-LLM permission. Synthetic-looking names alone do not prove a file's provenance; do not upload historical records.

## Phase 2A: function calling

Run an **offline, explicitly simulated** demonstration in PowerShell. A separate database path avoids overwriting or relabeling a previously uploaded dataset:

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
$env:ACADEMIC_DB_PATH = 'data/agent-demo.sqlite3'
$env:COPILOT_LLM_PROVIDER = 'mock'
& '.\.venv\Scripts\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000
```

Keep that terminal open. In another PowerShell window:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/agent/status'
$payload = @{ question = 'What is the average score and pass rate for G1-C01?' } | ConvertTo-Json
Invoke-RestMethod 'http://127.0.0.1:8000/api/agent/query' -Method Post -ContentType 'application/json' -Body $payload | ConvertTo-Json -Depth 12
```

The default demo returns `provider: mock`, `simulated: true`, two model-interface turns, one tool call and the deterministic values **69.73**, **67.80%**, **469 graded records**. The mock uses limited English routing and canned evidence selection; it is not an LLM. Other demo questions include `How many students are in the dataset?`, `List courses in grade 1`, and `Compare C001 and C002 for G1-C01`.

For a real Chat Completions-compatible HTTP provider, stop the service with Ctrl+C, set these variables in the service terminal, then restart. Enter the key interactively:

```powershell
$env:COPILOT_LLM_PROVIDER = 'openai_compatible'
$env:COPILOT_LLM_BASE_URL = 'https://api.openai.com/v1'
$env:COPILOT_LLM_MODEL = Read-Host 'Enter a model ID supporting Chat Completions function tools'
$secret = Read-Host 'Enter LLM API key' -AsSecureString
$env:COPILOT_LLM_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
Remove-Variable secret
& '.\.venv\Scripts\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000
```

Only the administrator's environment controls provider, endpoint and model. [.env.example](.env.example) documents the variables; `.env` is **not auto-loaded**. The base URL is a trusted HTTPS API prefix; the adapter appends `/chat/completions`. Loopback HTTP is allowed for tests. It sends `store=false`, disables redirects/proxy inheritance, uses a 15-second provider timeout and 45-second whole-query deadline, and caps each query at four model turns and six tool calls. There are no hidden retries or automatic live-to-mock fallback. Set `COPILOT_LLM_STRICT_TOOLS=false` only for compatible endpoints without remote strict-schema support; local validation remains strict. Real model/endpoint support, credentials and external connectivity have not been verified here.

Four tools wrap the existing core: `get_dataset_overview`, `get_score_summary`, `compare_classes`, `get_catalog`. Statistics delegate to `AnalyticsEngine.summary`; comparison delegates to `AnalyticsEngine.compare_classes`. Overview/catalog reuse the repository. The model cannot supply executable Python, SQL, arbitrary filter fields, URLs or filenames.

The provider receives a canonicalized, restricted English academic question and aggregate/catalog results. Student IDs, unsupported vocabulary, non-ASCII questions, emails, phone numbers and code-like input are rejected before a provider request. This is a narrow domain gate, not a general PII detector. Only generated datasets are eligible; uploaded datasets remain blocked. Provenance/version are rechecked in the transaction reading each tool result. No student rows/display names are sent externally.

The model receives a `role: tool` message linked by `tool_call_id`, then selects server-issued evidence IDs in a strict final JSON object. The server renders the factual English answer from those results. Arbitrary model prose/numbers, fabricated IDs, duplicate references and answers without evidence are rejected. This basic reference mechanism is not a general free-form claim verifier or a guarantee that a model chose the intended filter; returned arguments and evidence make its choice reviewable.

Responses include request ID, provider mode, model turns, validated arguments, tool status, elapsed time, result digest and aggregate evidence. `copilot.agent` emits JSON events without raw questions, invalid raw arguments, provider bodies or keys. `simulated=false` identifies the HTTP adapter; it does not prove its upstream is a real model. The HTTP smoke test intentionally uses a scripted upstream.

## Verification and debugging

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp=.test-tmp-manual
.venv/Scripts/python.exe scripts/smoke_http.py
.venv/Scripts/python.exe scripts/smoke_agent.py
```

Latest recorded result: **153 passed, one dependency deprecation warning** (74 existing core/API tests plus 79 agent/provider tests). [Phase 2A results](docs/development/02-function-calling.md) retain the adapter failure and successful rerun. The Agent HTTP smoke passed **6/6** checks using real TCP, Uvicorn and a scripted model HTTP server; no real LLM was called. It creates temporary synthetic data and stops both services. The earlier [core record](docs/development/01-core-api.md) retains its seven-check HTTP result; `smoke_http.py` needs the export command first. Both scripts leave the default database unchanged.

For debugging, start with `/health`, inspect `/api/dataset`, choose an ID from `/api/catalog`, then reproduce one query in Swagger. A 422 means a schema or dataset constraint failed; a 404 means a valid-format entity/term is absent. A 413 is a byte/row/expansion limit, and 415 is an unsupported file type. Match a response's dataset hash to the manifest before comparing numbers.

## Contribution boundary and next increment

[The architecture](docs/IMPLEMENTATION_PLAN.md), [36-case English Agent evaluation design](docs/EVALUATION_PLAN.md) and [upstream audit](docs/audit/UPSTREAM_REVIEW.md) are retained. Phase 2A adds the tool loop, basic evidence selection and redacted agent events. Phase 2B will add Streamlit. Live-provider verification, the full Agent evaluation, richer report/claim workflows and Docker remain outstanding; no live-model accuracy or Docker verification is claimed.

Reference inspiration: [Student Performance Analyzer](https://github.com/CH-Anonymous/student-performance-analyzer) by Chirag Khatri, commit `c691eff6ecc0de351ffcdf26bdce436e5daa64e2`. Its original [MIT notice](third_party/student-performance-analyzer/LICENSE) and [author credit](NOTICE.md) remain intact. The new business core is independently implemented; upstream data, model, inference logic and screenshots are not product inputs.
