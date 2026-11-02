# Academic Analytics Copilot

An English-language application-engineering portfolio project proposed for SAP STAR Application Engineer (2028 Graduates).

**Current status: upstream audit and design proposal. The Copilot application is not implemented.**

The project owner's background includes academic analysis and reporting covering 1,382 students and 40 classes. The planned demonstration uses entirely fabricated data and does not contain historical student records. No SAP product integration or affiliation is claimed.

## Review first

- [Upstream review and reuse decision](docs/audit/UPSTREAM_REVIEW.md)
- [Actual verification record and limitations](docs/audit/VERIFICATION.md)
- [Architecture and module-by-module implementation proposal](docs/IMPLEMENTATION_PLAN.md)
- [36 proposed English evaluation cases — not executed](docs/EVALUATION_PLAN.md)
- [Original author credit and contribution boundary](NOTICE.md)

The review recommends independent implementation, retaining the upstream project's attribution and analysis ideas. The original Streamlit script has no reusable backend/agent layer and relies on unsafe data-inference assumptions for the requested normalized data model.

## Planned product

FastAPI APIs will expose deterministic SQL/Pandas analytics over `students`, `classes`, `courses`, `enrollments` and `grade_records`. A bounded agent will use a hosted LLM through a configurable provider adapter to choose allowlisted tools. Typed evidence references and server-rendered reports will ensure formal numerical claims correspond to deterministic results. The model will have no arbitrary Python or SQL execution capability.

The generator will create 1,382 synthetic students, 40 classes, three grades and 96 catalog courses, with 10–12 courses per student using a fixed seed. These are planned requirements, not counts from an existing generated dataset. Tests, prompt evaluation, structured logging, Docker and an English walkthrough are specified in the plan.

## What is runnable today

Only the **upstream audit utilities**, under `scripts/audit/`, are available. Their prerequisites, commands and actual results are recorded in the verification document. Local source copies and virtual environments live under the ignored `.audit/` directory. They are not a standalone Copilot demo and are not committed to this repository. No product test pass rate, live LLM result or Docker verification is claimed.

## Upstream

Reference: [Student Performance Analyzer](https://github.com/CH-Anonymous/student-performance-analyzer) by Chirag Khatri, reviewed at `c691eff6ecc0de351ffcdf26bdce436e5daa64e2`. Its MIT notice is preserved in [third_party/student-performance-analyzer/LICENSE](third_party/student-performance-analyzer/LICENSE). The original project provides Streamlit visualizations and linear regression. All proposed backend, normalized dataset, agent, validation, evaluation and delivery work is a new contribution, to be implemented after the audit decision is accepted.
