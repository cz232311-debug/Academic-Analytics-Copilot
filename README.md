# Academic Analytics Copilot

An independent engineering portfolio project for SAP STAR Application Engineer (2028 Graduates).

**Implemented and tested: synthetic data, validated CSV/XLSX import, deterministic analytics and a FastAPI service.** Streamlit, the LLM agent, claim-reference verification, the 36 Agent evaluations and Docker are not implemented in this increment.

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

## Verification and debugging

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp=.test-tmp-manual
.venv/Scripts/python.exe scripts/smoke_http.py
```

Latest recorded result: **74 passed, one dependency deprecation warning**. The real TCP/HTTP smoke script passed seven checks, including complete demo CSV upload and preservation after an invalid upload; it starts a temporary server/database and stops the server afterward. Run the export command first. It does not alter the default database. See [the implementation/test record](docs/development/01-core-api.md) for the actual failures, fixes, raw results and environment limits.

For debugging, start with `/health`, inspect `/api/dataset`, choose an ID from `/api/catalog`, then reproduce one query in Swagger. A 422 means a schema or dataset constraint failed; a 404 means a valid-format entity/term is absent. A 413 is a byte/row/expansion limit, and 415 is an unsupported file type. Match a response's dataset hash to the manifest before comparing numbers.

## Contribution boundary and next increment

[The architecture](docs/IMPLEMENTATION_PLAN.md), [36-case English Agent evaluation design](docs/EVALUATION_PLAN.md) and [upstream audit](docs/audit/UPSTREAM_REVIEW.md) are retained. Next: build a Streamlit client against these APIs and test actual frontend/backend interaction; then add a small controlled LLM tool loop, numerical claim references, evaluation execution, structured logs and Docker. No live LLM pass rate or Docker validation is claimed today.

Reference inspiration: [Student Performance Analyzer](https://github.com/CH-Anonymous/student-performance-analyzer) by Chirag Khatri, commit `c691eff6ecc0de351ffcdf26bdce436e5daa64e2`. Its original [MIT notice](third_party/student-performance-analyzer/LICENSE) and [author credit](NOTICE.md) remain intact. The new business core is independently implemented; upstream data, model, inference logic and screenshots are not product inputs.
