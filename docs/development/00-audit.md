# Module 00 — upstream audit and decision

Status: audit completed with explicitly limited dynamic coverage. Product implementation is pending acceptance of the independent-build proposal.

## Files added

- `scripts/audit/prepare_snapshot.py`: pinned Git inspection, text-only runtime copy, upstream data/header metadata and opaque artifact hashes.
- `scripts/audit/dependency_inventory.py`: installed package metadata and safely preserved license/notice artifacts.
- `scripts/audit/data_probes.py`: six narrow behavior assertions over exact reviewed upstream statements and real Pandas.
- `scripts/audit/runtime_probe.py`: synthetic Streamlit AppTest scenarios; final run blocked at dependency initialization.
- `scripts/audit/server_smoke.py`: loopback startup/health/root check with cleanup.
- `scripts/audit/check_evidence.py`: hash integrity, package coverage, syntax and local-document consistency checks.
- `docs/audit/`: review, verification and raw evidence; `docs/IMPLEMENTATION_PLAN.md` and `docs/EVALUATION_PLAN.md`: proposed next stages.
- `README.md`, `NOTICE.md`, original upstream MIT notice, `.gitignore` and `.gitattributes`: clear scope, attribution and byte-preserved evidence.

## Core knowledge

An MIT project license does not replace its dependencies' licenses or prove sample-data provenance. A Python virtual environment provides package separation, not hostile-code isolation. Correctness depends on the data model and denominator, not on whether a chart renders. A structured model response needs deterministic evidence checks in addition to schema validation. A service's healthy HTTP endpoint does not prove its interactive analysis works.

## Design tradeoffs

Choose an independent implementation because no backend module exists to extract and the CSV assumptions conflict with normalized enrollments. Preserve analytical ideas and provenance. Do not inherit the model file, sample data, ranking semantics or unvalidated regression. Use selected-statement probes to verify specific data defects when full UI testing cannot complete; label their narrower scope visibly.

## Verification, problems and fixes

The exact environment, commands, results and failed attempts are in `docs/audit/VERIFICATION.md`. Installation, consistency, advisory query and HTTP startup succeeded. Six data probes passed their audit assertions. Bandit found one low-severity issue. Full AppTest and browser behavior remain unverified because native dependency initialization timed out. No Docker run occurred.

Resolved setup issues: failed local Git proxy, sandbox package access, an overbroad loopback guard, flattened license basenames and legitimate wheel parent paths. Unresolved issue: native numerical/plotting dependency initialization in this host. Future stages must write their own files/knowledge/decisions/tests/failures/fixes record and must not inherit an audit result as product verification.
