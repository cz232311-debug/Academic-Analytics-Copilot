# Increment 01 — data core and FastAPI

Date: 2026-11-03, Asia/Shanghai. Scope: the accepted first development round. Existing architecture/evaluation/audit materials were reused; no Agent framework or new planning document was introduced.

## Actual implementation and files

| Area | Files | Delivered behavior |
|---|---|---|
| Packaging | `pyproject.toml`, `requirements.lock.txt` | Python 3.13 environment, installed editable package, tested versions |
| Domain | `src/copilot/models.py`, `schemas.py`, `schema.sql` | Five typed tables; score/status, FK, uniqueness and grade-eligibility constraints; typed results |
| Synthetic data | `src/copilot/data/generate.py` | Seed 20261103; deterministic exports and manifest; no upstream data |
| Persistence | `src/copilot/storage.py` | SQLite transactions, atomic activation/rollback, read snapshots, bound filter values, upload provenance |
| Analysis | `src/copilot/analytics.py` | Exact means, pass rates, weighted means, distributions, distinct failure counts, class comparison |
| Imports | `src/copilot/data/importing.py` | Five CSVs or five-sheet XLSX, explicit cleaning and bounded validation |
| HTTP | `src/copilot/api.py` | Health, overview, catalog, summary, comparison, atomic upload, typed errors, actual request-byte cap |
| Verification | `tests/`, `scripts/smoke_http.py`, `results/` | Hand-calculated fixtures, unit/API/security cases and real Uvicorn/HTTP checks |
| Documentation | README, existing plan status, NOTICE, ignore/attributes files | Current run instructions, contribution boundary, byte-preserved evidence and local-data exclusions |

The generated local `data/demo/` contains 1,382 students, 40 classes, 96 catalog courses and **15,194** enrollments/final records. There are 14,729 graded, 311 missing and 154 exempt records. Grade populations are 484/449/449 and every student takes 10–12 courses. Dataset SHA-256: `a52c8961a7c1da92ab4d7d8cc8af9e9a636fe3e788cbf5bcd24c8eb16607cabc`. The [manifest](results/demo-manifest.json) records all CSV hashes; the observed hash was then frozen as a regression check.

## Core knowledge and tradeoffs

- Integer hundredths distinguish a score value from a display string and avoid floating-point summation/threshold errors. Decimal half-up is applied only to display; ranking compares exact fractions before rounding.
- Enrollment is the unit of the default mean and pass denominator. A student taking more courses contributes more enrollment records; this is stated rather than silently calling it a per-student mean. Missing/exempt records are explicit and never turn into zeros.
- SQLite and Pandas keep the core small. A fixed SQL aggregate independently checks Pandas counts; tiny fixtures give hand-derived expected values so parity does not merely compare one implementation with itself. No ORM or Agent framework is necessary yet.
- Five-table imports are validated completely before replacement. The same transaction inserts all records and metadata; read snapshots keep results and dataset versions consistent. Invalid inserts roll back deleted rows too.
- XLSX was added per the accepted user requirement, superseding the earlier CSV-only proposal. It introduces a ZIP/XML parser surface: expansion limits, defused XML, formula/external-link rejection and literal-only sheets are deliberate constraints. `.xls` and macros are excluded.
- A synthetic-name constraint reduces accidental identity import; it cannot establish grade-data provenance. Uploads therefore remain local-only. This release has no external LLM calls, keys or arbitrary execution endpoints.

## Executed test gates

| Gate / exact test target | Observed result | Raw evidence |
|---|---|---|
| `pytest tests/test_data.py` | 16 passed | [01-data.xml](results/01-data.xml) |
| Add storage/analytics: `pytest tests/test_data.py tests/test_analytics.py` | 30 passed | [02-analytics.xml](results/02-analytics.xml) |
| Add CSV/XLSX: same plus `tests/test_imports.py` | 53 passed | [03-imports.xml](results/03-imports.xml) |
| First runnable `pytest tests/test_api.py` | **3 failed, 11 passed** | [04-api-initial.xml](results/04-api-initial.xml) |
| Fix upload fixture; rerun API tests | 14 passed | [04-api-fixed.xml](results/04-api-fixed.xml) |
| Expanded full regression | 73 passed | [05-full-suite.xml](results/05-full-suite.xml) |
| Final regression after golden hash and sanitized multipart errors | **74 passed, 1 warning; zero failures/skips** | [06-release.xml](results/06-release.xml) |
| `python scripts/smoke_http.py` | **7/7 actual TCP/HTTP checks passed**; temporary service stopped | [http-smoke.json](results/http-smoke.json) |
| `python -m pip check` | No broken requirements | Executed after installation/export |
| Generator CLI and editable installation | Both succeeded | Recorded manifest and actual HTTP bootstrap |

The final command was `.venv/Scripts/python.exe -m pytest -q --basetemp=.test-tmp-release --junitxml=docs/development/results/06-release.xml`. Tests ran on Windows x86-64 / Python 3.13.6; the version lock includes the installed build/test dependencies. The 36 **Agent** cases remain specifications, not these 74 unit/API tests and not an LLM benchmark.

The smoke script started a real Uvicorn process and checked health, manifest counts/hash, course statistics, comparison, full-volume CSV upload, unchanged statistics after upload, and invalid-upload rejection without data loss. G1-C01 returned average 69.73, pass rate 67.80%, and 469 graded records. It used a temporary database, did not replace the user's default database, and stopped the server. Excel was separately exercised through actual OOXML parsing and the FastAPI TestClient upload route; no Excel desktop or browser automation was performed.

## Actual problems and fixes

1. Sandbox `ensurepip`, package-index access and editable-install temporary files failed. Re-running the same scoped operations in an approved context succeeded. Product `.venv` is separate from the old `.audit/venv`; no upstream model or application was executed for product tests.
2. The first sandbox API-test attempt stalled before test execution in Windows asyncio's internal socketpair. Its trace identified the localhost permission boundary. That process was stopped; approved local TestClient execution completed. This was an environment block, not an application pass/fail result.
3. The first runnable API suite exposed **three real test failures**: a shared CSV helper used `(filename, MIME, bytes)`, while HTTPX requires `(filename, bytes, MIME)`. An explicit multipart adapter fixed the fixtures; all 14 API cases then passed before continuing. The initial failed XML report is retained.
4. Review identified a precision pitfall: two displayed means can both be 60.00 while their underlying means differ. Class ranking now uses exact fractions. A regression fixture verifies that 60.000 outranks 59.995 despite equal display values.
5. XLSX relationship entity declarations needed conversion into a sanitized validation error, not an internal error. The importer now catches defused-XML exceptions; an actual OOXML entity-declaration fixture verifies rejection. Multipart parse errors likewise return the sanitized HTTP error schema.
6. One warning remains: the installed Starlette TestClient deprecates its HTTPX adapter in favor of HTTPX2. HTTPX 0.28.1 still passes every API test. The warning is preserved in the reports, not suppressed. Migration is a later dependency-maintenance item; it did not affect actual Uvicorn/HTTP checks.

## Limits and next increment

Streamlit, LLM API integration, controlled tool calling, claim-reference validation, structured logging, the 36 Agent evaluations and Docker have not been implemented or verified. The existing architecture and evaluation set remain the starting points. Next, implement the Streamlit API client and test actual user selections/uploads against FastAPI before beginning the Agent adapter. No real student data, live-model correctness or production/SAP integration is claimed.
