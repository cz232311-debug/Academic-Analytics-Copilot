# Phase 3C — implementation and verification record

Date: 2026-11-12. Branch: `feature/chat-first-agent`.

Baseline: `f8526e5fd16e65b62ba22dfb5d404a114cf7b60a`.

1. `6b6866024231727e34e1bb305ba5fd6d09f151c8` — semantic guards, readable IDs, active generated snapshot export.
2. `e610a0ec6a622a7b3b1b928ab267abad9e9e7098` — verified Chinese evidence analysis, explicit fallback, further natural-expression guards and offline planning prototype.
3. A subsequent documentation-only commit records this inventory, operator instructions, screenshots and test receipt. Its exact hash and all cumulative/per-commit patches are in the exported packet manifest. Generated packet files are outside their own Git history.

Pre-existing owner changes were preserved and excluded from these commits: `docs/development/results/14-deepseek-offline-demo.json`, `.workbuddy/`, `docs/development/CURRENT_SYSTEM_HANDOFF.md`, and `docs/development/phase3b-review.zip`. The tracked owner delta is exported separately; owner untracked files are listed, not copied into the review package.

## Changed files

| File | Change and responsibility |
|---|---|
| `src/copilot/agent/semantics.py` | Capability inquiries, unresolved course subjects, catalog candidates, polite wording, explicit whole-scope conflicts, Chinese-adjacent ID boundaries. Does not loosen `validate_intent_call()`. |
| `src/copilot/analytics.py` | Pass the existing `generated_only` snapshot guard through `course_report()` used by major-filtered tools; no formula change. |
| `src/copilot/api.py` | Add generated-only XLSX export GET route requiring the expected version. |
| `src/copilot/data/synthetic_export.py` (new) | One-transaction metadata/five-table snapshot, source/version check, five Chinese XLSX sheets, exact IDs/cents, formula protection. |
| `src/copilot/dashboard/client.py` | Typed binary HTTP boundary with content-type/version verification and safe export error. |
| `src/copilot/dashboard/chat.py` | Readable course/class names, short/full-copyable version, deferred download, provenance invalidation, verified analysis display and explicit fallback. Missing class means are omitted from charts rather than drawn as zero. |
| `src/copilot/agent/models.py` | Additive analysis selection/result contracts and explicit verification/fallback fields; legacy narrative remains accepted on its old path. |
| `src/copilot/agent/analysis.py` (new) | Safe goal/required metrics, evidence claim catalog, derived ratios/exact comparison, strict selection validation and Chinese rendering. No model-written factual prose is accepted. |
| `src/copilot/agent/service.py` | Grounded protocol integration, claim context, candidate labels in notices and outcome logging; keep tool/intent/privacy/version guards and deterministic fallback. |
| `src/copilot/agent/planning.py` (new) | Offline-only validation of up to three preapproved nodes. No production caller or executor. |
| `tests/test_agent_semantic_integration.py` | Correct one fixture-inconsistent question: ask for an existing Web course instead of a nonexistent data-structures course. Unknown-course rejection is separately required by new regressions. |
| `tests/test_phase3c_semantics.py` (new) | Consultation, fuzzy/unknown courses, explicit course preservation, session/global conflicts and natural wording regressions. |
| `tests/test_synthetic_snapshot_export.py` (new) | Nine tests: all table counts/IDs/zero/nulls, direct DB changes, old versions, same-hash uploaded provenance, no dataset, injection, unenrolled students, concurrent WAL switching. |
| `tests/test_phase3c_chat.py` (new) | Seven UI/client cases: lazy current download, forbidden uploaded source, stale/same-hash switching, readable IDs, major guard and verified/fallback frontend branches. |
| `tests/test_phase3c_analysis.py` (new) | 43 cases covering ratios, Chinese analysis, fabricated claims, invalid free text, fallback, legacy compatibility, metric coverage, version and directory content. |
| `tests/test_phase3c_planning.py` (new) | 16 scripted plan-validation cases; no LLM inference or execution. |
| `README.md` | Current architecture, synthesis boundary, generated snapshot download and links. |
| `START_HERE.zh-CN.md` | Analysis outcome labels, clarification examples, full-version copying and current synthetic export instructions. |
| `docs/development/02b-workbench.md` | Per-round progress index. |
| `docs/development/phase3c-review/LEARNING.zh-CN.md` (new) | Chinese diagnosis, call chains, three code excerpts, responsibilities and limits. |
| `docs/development/phase3c-review/PLANNING.zh-CN.md` (new) | Design, actual Mock evaluation and prerequisites for future controlled planning. |
| `docs/development/phase3c-review/FILES.md` (new) | This inventory and verification record. |
| `docs/development/results/phase3c-verification.json` (new) | Sanitized completed test, browser and export results; no credentials or student data. |
| `docs/development/phase3c-review/phase3c-home.png`, `phase3c-review.png`, `phase3c-clarification.png`, `phase3c-download.png` (new) | Phase 1 real Edge/Mock UI and actual XLSX download checks, before the final analysis display increment. |
| `docs/development/phase3c-review/phase3c-analysis-verified.png`, `phase3c-analysis-fallback.png` (new) | Final real Edge checks of scripted validated analysis and original Mock fallback; both explicitly offline. |
| `docs/development/phase3c-review/phase3c-clarification-final.png` (new) | Final clarification answer visibly lists the readable sports candidate without executing it. |

No source files were deleted. Database schema, import adapter, provider, tool whitelist, `validate_intent_call()`, old English `narrative.py`, and the legacy report workspace remain in place. No Word/RAG/multi-model/runtime multi-agent framework was added.

## Actual verification

All Python commands used the repository `.venv/Scripts/python.exe`. The completed full commands were:

```powershell
python -m pytest -q --disable-warnings --basetemp=<workspace>/work/phase3c-phase1-all-tmp --junitxml=<workspace>/work/phase3c-phase1-all.xml
# 504 passed, 2 warnings in 55.04s

python -m pytest -q --disable-warnings -p no:cacheprovider --basetemp=<workspace>/work/phase3c-final-all-tmp --junitxml=<workspace>/work/phase3c-final-all.xml
# 584 passed, 1 warning in 63.48s
```

The final suite includes original Excel/analytics/Agent/report and three-view tests. The remaining warning is the existing Starlette/httpx TestClient deprecation. No tests were skipped to obtain these results. Intermediate full regression: 581 passed in 61.80s, followed by three additional directory/combined-metric regressions and the final run above.

Targeted evidence includes nine export tests (1.77s), 80 semantic/local tests (9.12s), 21 old/new chat UI tests (26.38s), and 59 analysis/planning tests (2.87s). Independent analysis review ran 40 tests successfully before the final three additions. JUnit files are provided in the review ZIP under `evidence/`.

Real browser: headless Microsoft Edge through CDP, 1440×1100 CSS pixels, light theme, isolated temporary databases/ports, no inherited API key. Phase 1 checked first open, preview without activation, explicit confirmation, fuzzy-course clarification and an actual deferred XLSX download. Phase 2 checked original Mock fallback and Scripted Mock valid analysis through the real service validation and UI. Screenshots above show the tested states. This is not mobile/dark-theme/cross-browser acceptance and not live DeepSeek validation.

Actual export verification: **12 students, 2 classes, 6 courses, 72 enrollments, 72 grade records**; all worksheet counts and full version matched the active API snapshot. Recomputed from exported sports rows independently: mean **85.05**, distribution **0/0/5/3/4**, no formula cells. Tests also modify DB scores without changing the seed, proving that export reads the database rather than regenerating random data.

## Failures found and corrected

- The old semantic integration test asked for a course absent from its four-course fixture. Its former success depended on silently querying all courses. The test now uses an existing course for its original scope purpose, with explicit rejection of the nonexistent course added.
- New UI tests initially asserted a full major name instead of the actual catalog class label and passed `evidence_id` positionally to a keyword-only API. The tests were corrected against the real contracts; they did not justify weakening product guards.
- Default Windows pytest temporary-directory ACL and Edge GPU subprocess sandbox issues required writable test directories and a headless browser helper with GPU disabled. Only completed runs above are counted as passes. No security policy was disabled for the application.
- Independent review caught valid-but-incomplete analysis: missing requested metrics and catalog names. Required metric tags and mandatory readable catalog entries now prevent those cases from being marked verified.

## What is implemented versus unverified

Implemented and offline-verified: the two diagnosed intents, clarification candidates, exact tools, same-version evidence, bounded Chinese composition, explicit invalid/missing-analysis fallback, source/version-safe current snapshot export, privacy regression, readable main UI and the retained statistical views.

Designed/tested only: controlled planning. `planning.py` is imported by tests, never production; no free-question decomposition or plan execution is claimed.

Not verified in this increment: real DeepSeek adherence/quality/latency/cost with the new protocol, dark/mobile/cross-browser usability, arbitrary Chinese understanding, complex multi-turn or unrestricted analytical prose. The user's earlier live tests are separate evidence, not this increment's live validation. **Live model calls made here: zero.**

The original Mock deliberately does not generate the new protocol. Its `missing_analysis` fallback is expected. Dedicated scripted replies prove the trusted path and UI behavior, without proving that a real model will choose useful claims reliably.

The delayed-download failure prompt is supplied by Streamlit and may be generic; refresh and reconfirm after a dataset change. Previously downloaded files cannot be revoked. This is still a local demonstration without multiuser authorization.

## Review package boundary

The exported `phase3c-review.zip` contains the committed source checkpoint, this documentation, sanitized aggregate-only diagnostics, completed JUnit receipts, cumulative diff and per-commit patches. `manifest.json` records baseline/final hashes, source paths/hashes and a separate inventory of untracked files. The owner-owned tracked working-tree patch is separate from the implementation diff.

Excluded: `.env`, keys, `.git`, `.venv`, databases, raw workbooks, private import archives, runtime logs, `.workbuddy/` and owner-untracked handoff/ZIP. The standalone downloadable XLSX is explicitly synthetic and supplied separately. The review packet does not recursively include itself.
