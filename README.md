# Academic Analytics Copilot

An independent engineering portfolio project for SAP STAR Application Engineer (2028 Graduates).

**Phase 2B adds a runnable Streamlit academic analysis workbench connected to FastAPI over HTTP.** It includes multidimensional course reports, charts, CSV downloads and the existing evidence-grounded Copilot. Phase 2A's user-run real DeepSeek check passed separately. [Workbench implementation and verification](docs/development/02b-workbench.md)

The owner's prior experience includes academic analysis and reporting covering 1,382 students and 40 classes. This demonstration uses entirely fabricated records; it contains no historical student data and claims no SAP integration or endorsement.

For the complete first-install and post-reboot PowerShell workflow, read [本机启动说明](START_HERE.zh-CN.md). After installing dependencies, keep two PowerShell windows open from the repository root:

```powershell
# Window A — FastAPI, offline Mock provider by default
.\scripts\start_backend.ps1
```

```powershell
# Window B — Streamlit, reads only the FastAPI service
.\scripts\start_dashboard.ps1
```

Open [the workbench](http://127.0.0.1:8501). The sidebar supports term, grade, class, major, course type and course. Click **Load analysis** to update the three report views. Excellent rate means graded scores ≥80. Tables, charts and CSV share one backend report and version. The AI area keeps its existing scope and does not inherit sidebar filters. External model calls require an explicit form submission and authorization.

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
| `classes` | `class_id`: string ID; `grade_level`: integer 1–3; `class_label`: text; optional `major`: enum |
| `students` | `student_id`: string ID; `class_id`: FK; `display_name`: exact `Synthetic Student NNNN` |
| `courses` | `course_id`: string ID; `course_name`: text; `grade_level`: integer; `credits`: integer 2–4; optional `course_type`: enum |
| `enrollments` | `enrollment_id`: string ID; `student_id` / `course_id`: FKs; `term`: typed term |
| `grade_records` | `grade_record_id`: string ID; `enrollment_id`: unique FK; `status`: graded/missing/exempt; `score_cents`: nullable integer 0–10,000 |

`score_cents=6000` means **60.00 points**. Graded records require a score; missing/exempt records require null. Every enrollment requires exactly one final record. Duplicate keys, duplicate student/course/term selections, orphan rows and cross-grade enrollments are rejected. Class IDs are never inferred to be scores.

Exports in `data/demo/` include five CSVs and a manifest with seed, counts and SHA-256 hashes. Generated data and local databases are ignored by Git; the code and [recorded manifest](docs/development/results/demo-manifest.json) reproduce them.

Major/course-type fields default to `unspecified` for legacy data and may be omitted from normalized CSV/XLSX uploads. Existing databases migrate without guessed classifications. New empty API databases receive explicit synthetic categories; the legacy generator default keeps its original payload and hash. Add `--reporting-metadata` to the generator command to export the enriched variant. Scores and enrollment counts are identical between variants; metadata is included in the enriched fingerprint. [Reporting policy and compatibility](docs/development/02b-workbench.md)

## Statistics and APIs

| Route | Behavior |
|---|---|
| `GET /health` | Service and dataset activation status |
| `GET /api/dataset` | Counts, populations, course loads, dataset hash and provenance |
| `GET /api/catalog` | Class/course IDs and labels for controlled filters |
| `GET /api/reports/catalog` | Versioned filter catalog, including major and course type |
| `GET /api/reports/courses` | Course-grouped report with six filters, exact mean bands, fail/excellent rates and version checking |
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

For DeepSeek Flash, set these variables in the service terminal. The masked prompt keeps the key out of PowerShell command history. The dedicated database path selects the generated demonstration data.

```powershell
Set-Location -LiteralPath 'D:\Document\ChatGPT\SAP-ApplicationEngineer'
$env:ACADEMIC_DB_PATH = 'data/agent-demo.sqlite3'
$env:COPILOT_LLM_PROVIDER = 'deepseek'
$env:COPILOT_LLM_BASE_URL = 'https://api.deepseek.com'
$env:COPILOT_LLM_MODEL = 'deepseek-flash'
$deepseekSecret = Read-Host 'DeepSeek API key' -AsSecureString
$env:COPILOT_LLM_API_KEY = [System.Net.NetworkCredential]::new('', $deepseekSecret).Password
Remove-Variable deepseekSecret
& '.\.venv\Scripts\python.exe' -m uvicorn copilot.api:app --host 127.0.0.1 --port 8000
```

The provider reads process environment variables. The DeepSeek profile appends `/chat/completions` to the official origin, disables thinking, requests JSON output and caps each completion at 2,048 output tokens. The stable endpoint uses local strict argument validation. Defaults are a 15-second provider timeout, a 45-second query deadline, four model turns and six tool calls. The adapter disables redirects and proxy inheritance. The `openai_compatible` profile still supports its own configurable base URL, model and remote schema strictness. [.env.example](.env.example) is a reference for the process variables. The app does not auto-load it. [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)

Four tools wrap the existing core: `get_dataset_overview`, `get_score_summary`, `compare_classes`, `get_catalog`. Statistics delegate to `AnalyticsEngine.summary`; comparison delegates to `AnalyticsEngine.compare_classes`. Overview/catalog reuse the repository. The model cannot supply executable Python, SQL, arbitrary filter fields, URLs or filenames.

The provider receives a canonicalized, restricted English academic question and aggregate/catalog results. Student IDs, unsupported vocabulary, non-ASCII questions, emails, phone numbers and code-like input are rejected before a provider request. This is a narrow domain gate, not a general PII detector. Only generated datasets are eligible; uploaded datasets remain blocked. Provenance/version are rechecked in the transaction reading each tool result. No student rows/display names are sent externally.

The model receives a `role: tool` message linked by `tool_call_id`. It writes English sentences by choosing and ordering scoped evidence tokens and grammatical connectors. The backend validates each sentence and fills the tokens from the tool output. `guidance` carries separately labeled suggestions from an allowlist. Unsupported prose with valid evidence triggers a deterministic fallback with `fallback_reason`. Invalid final JSON or evidence selection returns a traceable error. The tool filters and evidence expose the chosen scope for review.

Responses include request ID, provider mode, validated arguments, tool status, elapsed time, result digest and aggregate evidence. `model_trace` records each returned provider response ID and token usage when supplied. `usage` totals the input, output and combined tokens when every completed turn reports them. Mock turns report `null`. The `copilot.agent` JSON events record the same safe identifiers and outcome fields.

## Verification and debugging

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp=.test-tmp-manual
.venv/Scripts/python.exe scripts/smoke_http.py
.venv/Scripts/python.exe scripts/smoke_agent.py
.venv/Scripts/python.exe scripts/smoke_deepseek.py --offline
```

The Phase 2B regression contains **230 tests**, including the original 168 and actual TCP/Streamlit checks. Its [JUnit receipt](docs/development/results/phase2b-pytest.xml) records the completed result. The [workbench HTTP smoke receipt](docs/development/results/phase2b-http-smoke.json) records 15 passed checks. One existing TestClient deprecation warning remains. All Phase 2B automatic model calls use Mock/scripted transports. The user-run Phase 2A live check separately passed with `grounded_narrative`, one tool call and 3,482 reported tokens across two model turns. [Phase 2A completion record](docs/development/02a-deepseek.md)

Run the complete workbench HTTP acceptance with `.venv/Scripts/python.exe scripts/smoke_dashboard.py`. It starts isolated Uvicorn/Streamlit processes, tests the rendered page against real HTTP, and stops its own processes. No real API key is inherited. Streamlit AppTest verifies page behavior; browser pixel layout and a live DeepSeek submission through the new page remain unverified. [Official Streamlit app testing guide](https://docs.streamlit.io/develop/concepts/app-testing/get-started)

For debugging, start with `/health`, inspect `/api/dataset`, choose an ID from `/api/catalog`, then reproduce one query in Swagger. A 422 means a schema or dataset constraint failed; a 404 means a valid-format entity/term is absent. A 413 is a byte/row/expansion limit, and 415 is an unsupported file type. Match a response's dataset hash to the manifest before comparing numbers.

## Contribution boundary and next increment

Phase 2A adds the tool loop, the DeepSeek profile and grounded prose. Phase 2B's first increment adds Streamlit and standardized course reporting. Full Word/PDF export, the complete evaluation, Docker and delivery remain future work. [Architecture](docs/IMPLEMENTATION_PLAN.md) · [Evaluation design](docs/EVALUATION_PLAN.md) · [Upstream audit](docs/audit/UPSTREAM_REVIEW.md)

Reference inspiration: [Student Performance Analyzer](https://github.com/CH-Anonymous/student-performance-analyzer) by Chirag Khatri, commit `c691eff6ecc0de351ffcdf26bdce436e5daa64e2`. Its original [MIT notice](third_party/student-performance-analyzer/LICENSE) and [author credit](NOTICE.md) remain intact. The new business core is independently implemented; upstream data, model, inference logic and screenshots are not product inputs.
