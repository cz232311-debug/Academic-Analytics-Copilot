# Verification record

Date: **2026-11-03 (Asia/Shanghai)**. Host: Windows x86-64, Python 3.13.6, Git 2.53.0.windows.3. Docker was not found in PATH. Raw tool timestamps retain their original UTC/host formatting; dates in this narrative follow the client timezone.

This record distinguishes successful checks, reproduced defects, blocked checks and future work. There is no completed Copilot implementation, pytest suite, live LLM evaluation or Docker demonstration in this delivery.

## Actual results

| Check | Actual outcome | Evidence / scope |
|---|---|---|
| Pin source and inspect tracked tree | Completed | 11 tracked files, fixed commit and per-file hashes in `evidence/upstream_manifest.json` |
| Review source/config/manifest/license | Completed | Entire 171-line app, requirements, theme, README, actual MIT text; binary assets hashed only |
| Deserialize model | **Intentionally not performed** | No upstream pickle was placed in the runtime directory |
| Install requirements | Succeeded, exit 0 | Binary wheels only; 52 runtime distributions; `evidence/pip-install-report.json` |
| Dependency consistency | Passed, exit 0 | `python -m pip check`: `No broken requirements found.` |
| License inventory | Completed with stated completeness limits | 53 distributions including bootstrap pip; 133 extracted license/notice-named files, with hashes |
| Bandit 1.9.4 | Completed, exit 1 because a finding exists | One low-severity/high-confidence B112; no scanner errors; `evidence/bandit.json` |
| pip-audit 2.10.1 | Passed known-advisory check, exit 0 | 52 runtime distributions, zero reported vulnerabilities; bootstrap pip excluded; `evidence/pip-audit.json` |
| Streamlit server startup and HTTP | Passed, exit 0 in approved context | Health 200/`ok`, root 200/HTML, process stopped; `evidence/server-smoke.json` |
| Deterministic data probes | **6/6 audit assertions passed**, exit 0 | Actual selected upstream AST statements with real pandas; several assertions deliberately reproduce defects; `evidence/data-probes.json` |
| Full Streamlit AppTest scenarios | **Blocked; zero completed scenarios in final attempt** | Numerical dependency import timed out before scenarios began; `evidence/runtime-probe-timeout.txt` |
| Audit artifact integrity | Passed, exit 0; seven checks | Notice hashes, upstream license, 52-package coverage, recorded probe counts, 36 proposed case IDs, six script syntax checks and local document links; `evidence/integrity-check.json` |
| Browser upload/chart/prediction workflow | Not verified | HTTP startup and selected AST execution do not establish browser behavior or model validity |
| Product unit/API tests, 36 English evaluation cases, live LLM | Not run / not implemented | Specifications only |
| Container build and isolated container execution | Not run | Docker unavailable; venv is not an OS security boundary |

The 6/6 count measures audit assertions, **not six successful application features**. The actual upstream ingestion/statistics statements at lines 41–60 and 113–114 were selected by their original AST locations from a hash-checked source file. No plotting, training or UI code was substituted and then described as verified. That narrow check demonstrates:

| Synthetic fixture | Observed result |
|---|---|
| Valid Math/Science scores plus Gender | Known totals 110 and 170 matched; both Gender values were overwritten with nulls |
| Numeric Class alongside scores | Class incorrectly entered `subject_cols`; totals became 151 and 172 rather than 150 and 170 |
| Scores −10 / 120 / 150 | Invalid score ranges were accepted |
| Partial/all-missing row | Totals 140, 95 and 0; averages 70, 95 and null, showing changing denominators and all-missing zero sum |
| Legitimate `Midterm` field | Excluded because its name contains `id` |
| `History` negative control | Correctly retained; no unsupported claim that all humanities fields are excluded |

No real student records were used. The upstream CSV was read only with the standard-library CSV parser to inspect row count/header; its values were not included in reports or sent to a hosted LLM. There were no LLM calls.

## Problems, fixes and unresolved issues

1. **Repository download:** Git's saved proxy targeted unavailable `127.0.0.1:7897`. Initial sandbox and approved clone attempts failed. A command-scoped `-c http.proxy= -c https.proxy=` clone succeeded; no global Git configuration was modified.
2. **Environment creation/installation:** Sandbox `ensurepip` failed, and sandbox package-index access returned no matching distributions. Approved environment creation and PyPI wheel installation succeeded. Application and audit-tool dependencies were kept in separate venvs. No source distribution builds or repository install hooks were run.
3. **Local event-loop sockets:** The first Python-level socket guard blocked Windows asyncio's internal loopback connection, causing 12 harness errors. The guard was corrected to permit literal loopback addresses. Sandbox localhost restrictions still caused a socketpair hang / WinError 10013; the approved HTTP run succeeded. The first harness failure is retained at `evidence/runtime-probes-initial-harness-failure.json`. Its entries are harness failures, not upstream test failures.
4. **Native dependency initialization:** Later approved AppTest runs stalled during pandas/SciPy/seaborn native imports. Main-thread preloading and single-thread numerical-library settings moved progress but did not complete within the final 180-second initialization budget. The final attempt printed `Loaded pandas` and then timed out in the seaborn/SciPy import stack. Root cause is **unresolved**; this is not attributed conclusively to an upstream logic bug. The narrow pandas-only probes succeeded. No complete UI/prediction pass is claimed.
5. **License extraction:** A first approach flattened basenames, risking overwritten notices. It was replaced with a path-preserving extraction. Plotly also has valid wheel `.data` paths containing `../../share`; extraction now resolves each source inside the venv and maps parent components to safe output names. All final referenced file hashes are checked separately. Streamlit's installed RECORD lacks license/notice filenames, so final redistribution-notice completeness is not claimed.
6. **Advisory query:** Sandbox DNS failed. An approved query sent package names/versions only and succeeded. A cross-drive cache-write warning did not prevent the JSON result or exit 0. No automatic dependency fix was applied.

Approved runtime processes use the dedicated venv, credential-cleared environment and reviewed runtime directory, but they execute with host-user permissions outside the execution sandbox. The Python socket guard is not a firewall. No claim of container/VM isolation is made.

## Reproduction commands

PowerShell, from the repository root. These commands may need network permission or a functioning local proxy. Do not disable a proxy globally. Scripts expect the exact upstream commit; inspect them before running an audit of any other source. Python 3.13 on Windows was the tested host; other platforms remain unverified.

```powershell
git clone --no-checkout https://github.com/CH-Anonymous/student-performance-analyzer.git .audit/upstream
git -C .audit/upstream rev-parse HEAD
python scripts/audit/prepare_snapshot.py
python -m venv .audit/venv
python -m venv .audit/tools-venv
.audit/venv/Scripts/python.exe -m pip install --only-binary=:all: -r .audit/runtime/requirements.txt --report docs/audit/evidence/pip-install-report.json
.audit/tools-venv/Scripts/python.exe -m pip install --only-binary=:all: pip-audit bandit
.audit/venv/Scripts/python.exe -m pip check
.audit/venv/Scripts/python.exe -I -B scripts/audit/dependency_inventory.py
.audit/tools-venv/Scripts/python.exe -m bandit -r .audit/runtime/app.py -f json -o docs/audit/evidence/bandit.json
.audit/tools-venv/Scripts/python.exe -m pip_audit --no-deps --disable-pip --progress-spinner off -r docs/audit/evidence/resolved-requirements.txt -f json -o docs/audit/evidence/pip-audit.json
.audit/venv/Scripts/python.exe -I -B scripts/audit/data_probes.py
.audit/venv/Scripts/python.exe -I -B scripts/audit/server_smoke.py
.audit/venv/Scripts/python.exe -I -B scripts/audit/runtime_probe.py
python scripts/audit/check_evidence.py
```

`prepare_snapshot.py` rejects a different HEAD; if the remote branch advances, detach the no-checkout clone at the reviewed commit before preparing the snapshot. Installing the unpinned upstream requirements again can produce different versions. The version list and wheel hashes record the original run; use a hash-enforced lock/wheel archive for a future reproducible release. Runtime scripts use bounded waits and stop their own server process; they do not start a persistent demo service.

Successful reproduction of a blocked check would be new evidence, not a retroactive pass for this review. Preserve previous results under a separate run ID before rerunning tools that overwrite evidence files.
