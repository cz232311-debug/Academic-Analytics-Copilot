# Upstream review: student-performance-analyzer

Review date: **2026-11-03 (Asia/Shanghai)**. This is an audit and implementation proposal, not a completed Copilot product.

**Decision: suitable as an attributed learning reference; unsuitable as the core of the proposed application. Recommend an independent implementation.** The small dashboard provides useful visualization ideas, but its data inference, metric semantics and tightly coupled execution would all need replacement. Keeping its script as the application foundation would add migration work without supplying reusable backend or agent infrastructure.

## Scope and immutable reference

- Repository: [CH-Anonymous/student-performance-analyzer](https://github.com/CH-Anonymous/student-performance-analyzer).
- Reviewed commit: [`c691eff6ecc0de351ffcdf26bdce436e5daa64e2`](https://github.com/CH-Anonymous/student-performance-analyzer/tree/c691eff6ecc0de351ffcdf26bdce436e5daa64e2), committed 2025-06-07.
- Complete tracked tree inspected: 11 files. The application is one 171-line Python file with zero function/class definitions. There are no tests, package configuration, CI, lockfile, Dockerfile or backend modules in that tree.
- Evidence: [file sizes, Git blob IDs and SHA-256 hashes](evidence/upstream_manifest.json). The upstream was cloned with `--no-checkout`; only five reviewed text/config files were materialized into `.audit/runtime/`.
- `model.pkl` is a 555-byte opaque object. Its bytes were hashed, never deserialized or imported. It is absent from the runtime directory. No attempt was made to establish that the model itself is safe.

All line references below refer to [the pinned upstream app.py](https://github.com/CH-Anonymous/student-performance-analyzer/blob/c691eff6ecc0de351ffcdf26bdce436e5daa64e2/app.py).

## License and authorship

The actual upstream LICENSE is MIT, copyright 2025 Chirag Khatri. An exact copy is retained at [third_party/student-performance-analyzer/LICENSE](../../third_party/student-performance-analyzer/LICENSE), with provenance and credit in [NOTICE.md](../../NOTICE.md). Copying upstream software requires retaining its copyright and permission notice in copies or substantial portions. The future README must explicitly separate the original author's dashboard from the new owner's implementation. Do not describe the upstream linear regression as the new project's LLM agent.

The repository's CSV has no independent provenance statement or data-generation description. The MIT software notice alone does not establish a factual claim that its records are synthetic or that all underlying data rights were investigated. Do not use it in the new application. Screenshots are reference assets; they have not been copied into the portfolio or treated as evidence of new work.

### Direct dependencies, as actually resolved

`requirements.txt` contains seven names with **no versions, hashes, Python version or platform constraints**. The review installed binary wheels into a dedicated Python 3.13.6 environment, without installing the repository as a package or running any source-build backend.

| Direct dependency | Resolved version | License evidence and implications |
|---|---|---|
| Streamlit | 1.66.1 | Metadata declares Apache-2.0; installed source headers corroborate it. No LICENSE/NOTICE-named artifact was present in its wheel RECORD. Distribution notice completeness remains unresolved. |
| pandas | 3.1.0 | BSD-3-Clause; wheel includes additional vendored notices and alternative licenses. |
| Plotly | 7.1.0 | MIT for Python project; wheel includes JS component notices separately. |
| seaborn | 0.13.2 | BSD-3-Clause, confirmed by installed LICENSE.md rather than an empty metadata license field. |
| Matplotlib | 3.11.2 | Matplotlib license agreement based on PSF terms; aggregate notice includes bundled components; fonts carry separate terms. |
| scikit-learn | 1.9.1 | BSD-3-Clause plus included external component notices. |
| statsmodels | 0.15.0 | BSD-3-Clause plus included component notices. |

The environment contains **52 runtime distributions plus pip 25.2**. [The complete inventory](evidence/dependency-inventory.json) records versions, declared expressions/classifiers, project URLs and extracted license-file hashes. **133 license/notice-named files** were retained under [dependency-licenses](evidence/dependency-licenses/), including bundled components. Package metadata can omit or abbreviate terms; those terms are not replaced by the upstream project's MIT license.

Notable transitive/bundled obligations: certifi declares MPL-2.0; retain its license and investigate source obligations if modifying/distributing covered files. NumPy/SciPy include native library notices. SciPy's aggregate license identifies OpenBLAS/LAPACK and `GPL-3.0-or-later WITH GCC-exception-3.1` runtime components; the exception matters, so calling the entire tree “MIT/BSD only” would be incorrect. Matplotlib includes font licenses such as OFL/Bitstream terms. Apache packages may require included NOTICE text when supplied. The collected files are an audit inventory, not a completed redistribution clearance or a full JavaScript/native SBOM. Verify bundled assets and the final container image when the actual application dependencies are chosen.

[The resolved version list](evidence/resolved-requirements.txt) and [pip install report](evidence/pip-install-report.json) preserve this installation's versions, download URLs and wheel hashes. The version list is **not** a cross-platform, hash-enforced lockfile. One transitive dependency resolved to `wrapt==2.6.0.dev1`; this is another reason to choose and test explicit release dependencies for the new application. Keep the heavy regression/visualization stack out of the new backend unless a feature needs it.

## Structure, data and execution

| Upstream file | Role | Review outcome |
|---|---|---|
| `app.py` | All UI, CSV parsing, statistics, plotting and regression | Fully read; no separation of concerns; replace core logic |
| `.streamlit/config.toml` | Theme | Static visual configuration; no secrets or external endpoint observed |
| `requirements.txt` | Seven unpinned dependencies | Installable on this review host; not reproducible without a resolved snapshot |
| `multi_subject_student_data.csv` | Example wide-format scores | 200 rows, six score columns; provenance unverified; not executed as test input |
| `model.pkl` | Serialized model artifact | Application never references it; hash-only quarantine; do not reuse |
| `readme.md` | Setup and feature description | Describes Streamlit and linear regression; no LLM/function-calling design |
| `screenshots/*.png` | Four illustration assets | File metadata/hashes recorded; not runtime dependencies or test evidence |
| `LICENSE` | MIT notice | Exact notice retained with author credit |

The CSV header is `math score, reading score, writing score, science score, social studies score, computer score`. It has no student, class, enrollment or course identifiers. It cannot represent the requested normalized schema. The README's example additionally mentions names, roll numbers and gender; the application attempts to guess which columns are scores instead of validating an explicit schema.

Documented upstream launch is `pip install -r requirements.txt` followed by `streamlit run app.py`. The app needs an uploaded CSV to enter the analysis branch, at least two usable numerical columns for its intended plots, at least one complete training row and nonconstant slider ranges. Those preconditions are not consistently enforced. The model is trained from uploaded data in memory; a pretrained model file is not needed. There is no documented held-out error measure or prediction-validity threshold.

## Security and correctness findings

Severity reflects this portfolio's proposed use; a static concern is distinguished from a demonstrated exploit. Exact runtime results and limitations are recorded in [VERIFICATION.md](VERIFICATION.md).

| ID / priority | Source | Finding | Required response |
|---|---|---|---|
| F01 / High | 45–57 | Score selection uses identifier substrings and accepts any partially numeric column. Numeric class/age values can become scores; legitimate headers containing keywords can be excluded. | Replace inference with declared typed schemas and explicit metrics. |
| F02 / High | 53, 60 | Numeric coercion is assigned to each non-ID column before deciding its role. Text category values become NaN; the “categorical” list retains the column names after their data was destroyed. | Validate without mutating raw input; preserve categorical types. |
| F03 / High | 113–123 | Totals use available values; missing values and unequal course loads make total-point ranking misleading. All-missing row sums default to zero. No explicit completeness or denominator appears. | Normalize enrollment data, define missingness, use comparable metrics and sample counts. |
| F04 / Medium | 63–65, 105–106, 128–151 | No-subject and one-subject paths continue into regression/prediction. The “not enough features” warning does not stop execution; imports and training are incorrectly guarded. | Validate preconditions and return typed empty/unsupported results before execution. |
| F05 / Medium | 41–42, 53, 151, 159 | No application-level file-size/row/schema/range/finite-value or duplicate-normalized-header validation. Malformed data, empty training data and constant sliders can fail. Streamlit's own upload defaults do not supply business validation. | Bounded upload pipeline, atomic validation and tests for each edge case. |
| F06 / Medium | 140–165 | `train_test_split` is imported but unused; all complete rows are fitted, with no holdout, quality metric, minimum-size rule or bounded-score guarantee. | Omit prediction from v1; if added later, evaluate it as a separate statistical feature. |
| F07 / Medium | 89 | A CSV-controlled subject header is interpolated into HTML with `unsafe_allow_html=True`. | Use escaped text. This is a display-injection surface; no browser-level executable XSS exploit was established. |
| F08 / Medium | Requirements/tree | Unpinned dependencies, no tests/CI, no structured error handling or audit logs. | New lock, reproducible builds, meaningful unit/API tests, redacted events and CI. |
| F09 / High if loaded | Serialized artifact | Unreviewed pickle has a code-execution hazard if deserialized. Current app has no loading path. | Never load or ship this model in the new demo. |
| F10 / Medium | Data/README | Example-data provenance and model quality are undocumented; visible personal fields can be accepted and displayed locally. | Generate clearly labeled synthetic data; introduce an explicit external-LLM privacy boundary. |

Manual source review found no model deserialization, `eval`, dynamic `exec`, shell command, outbound HTTP call, SQL construction or hardcoded API key in the application. This scoped observation is not a proof about every transitive package or serialized object. Bandit reports one low-severity/high-confidence `B112` at line 56 (bare exception followed by continue). Its narrow rule set does not detect most data correctness findings above.

Python's [pickle documentation](https://docs.python.org/3/library/pickle.html) describes the code-execution risk of untrusted deserialization. The [scikit-learn persistence guide](https://scikit-learn.org/stable/model_persistence.html) makes the same distinction for pickle-based model formats. Neither document establishes whether this particular opaque artifact is malicious; this review makes no such claim.

## Isolation and verification boundaries

No Docker executable was available in PATH, so no containerized audit or Docker build was performed. A virtual environment isolates dependencies; it is **not an operating-system security boundary**. Initial dynamic attempts used the execution sandbox. Localhost sockets were denied (`WinError 10013` / asyncio socketpair hang), and installation required an approved network-capable execution context. The reviewed runtime probe and HTTP startup check were retried in an approved process outside that sandbox. Do not describe those runs as container-isolated or as a hostile-code sandbox.

Before any upstream execution, the application source, imports, config and tree were reviewed. The execution directory contains only reviewed text/config files, with neither pickle nor upstream CSV. Test CSVs are small and entirely fabricated. Runtime probes clear credential environment variables, redirect profile/temp directories into `.audit/`, disable usage telemetry, limit numerical-library threads and block non-loopback ordinary Python socket connections. Those are defense-in-depth measures, not an OS egress/file-access guarantee. The audited code runs with host-user privileges in the approved context.

Installation succeeded using wheels only, and `pip check` found no broken requirements. `pip-audit` queried all 52 resolved runtime distributions and reported zero known vulnerabilities; pip itself was excluded as bootstrap tooling. This is a dated known-advisory check, not malware analysis or proof of safety. [Raw result](evidence/pip-audit.json). The temporary audit tooling lives in a different environment so it cannot alter the application dependency resolution.

HTTP health and root requests returned 200, health body `ok`; the process was then stopped. This confirms a server can start. It does not confirm plots, CSV upload workflows, prediction accuracy or browser security. See the verification record for the final functional-probe outcome.

## Reuse and replacement decision

| Category | Items | Proposed action |
|---|---|---|
| Reusable reference | Distribution, boxplot, correlation and summary-table ideas; simple English upload journey | Reimplement against verified aggregates; attribute conceptual inspiration |
| Legally retained provenance | Author, repository, reviewed commit, MIT copyright/permission text | Keep NOTICE and exact upstream LICENSE |
| Potential cosmetic reuse | Theme palette and layout ideas | Low value; optional; no source copy is currently planned |
| Requires replacement | CSV parsing, field inference, aggregation, ranking, plotting data preparation, exception handling, package configuration | New typed modules with contracts and tests |
| Exclude | `model.pkl`, provenance-unknown data, unsupported score-prediction claims | Never import into the new product |
| Entirely new capability | FastAPI, normalized SQL/Pandas analytics, hosted LLM adapter, controlled agent, evidence validation, prompt evaluation, logging, tests, Docker | Implement independently in bounded module gates |

There is no isolated existing backend/service module worth directly reusing. Reusing the script would preserve fragile assumptions while requiring the same new components anyway. An independent implementation is therefore a better fit for a project the owner must explain and maintain.

The proposed [implementation plan](../IMPLEMENTATION_PLAN.md) maps requirements A–J, defines all five tables, a seeded 1,382-student/40-class/3-grade/96-course design, 10–12 enrollments per student, metric contracts, safe agent boundaries, upload/privacy policy and per-module verification records. The [36-case English evaluation specification](../EVALUATION_PLAN.md) is prepared but unexecuted. Product implementation should begin only after acceptance of this independent-build decision.
