# Takeover and baseline repair — file inventory

Snapshot boundary: this packet describes the repository at starting commit `d7c010a` (branch `main`), the WIP checkpoint `31834ad` on branch `feature/chat-first-agent`, and the repair that follows it. The repair commit hash is deliberately not embedded here: generated review artifacts must not recursively include their own final commit. Cumulative diffs and per-commit patches are deferred to the next increment, when the snapshot boundary is fixed.

## Commits in this round

| Commit | Branch | Purpose |
|---|---|---|
| `d7c010a` | `main` | Starting commit at takeover. Unchanged by this round. |
| `31834ad` | `feature/chat-first-agent` | WIP checkpoint. Records 10 pre-existing uncommitted files as an explicitly unverified, recoverable checkpoint. Known broken at this commit: 8 pytest failures. |
| (repair) | `feature/chat-first-agent` | Independent fix commit: interface compatibility, provider-free local path, regression tests, this packet. |

No commit was made directly to `main`. No historical commit was invented or backdated: the WIP files were authored before this session and are recorded now.

## Files added, modified and preserved

### WIP checkpoint `31834ad` (pre-existing work, not authored in this round)

| File | Kind | Responsibility |
|---|---|---|
| `src/copilot/agent/semantics.py` | new source | Catalog-bound academic intent resolver: aliases, entry cohort vs academic year/term, Chinese course names, class aliases, clarification choices, `validate_intent_call`. Unwired and untested at this checkpoint. |
| `src/copilot/data/synthetic_activation.py` | new source | Server-generated demo activation with signed preview tokens; prevents a client-supplied file from obtaining `generated` provenance. |
| `tests/test_synthetic_activation.py` | new test | Ten checks covering provenance, confirmation, tokens, expiry, concurrent replacement and privacy. |
| `src/copilot/agent/models.py` | modified | `SessionScope`, `QueryRequest` extensions, `ExecutionStage`, `AgentResponse` fields. Fields are declared but not yet populated. |
| `src/copilot/agent/provider.py` | modified | DeepSeek as explicit default; Mock replay of `ACADEMIC_INTENT` plans. |
| `src/copilot/agent/tools.py` | modified | Strict required tool schemas, `major` filter reusing `course_report`, `generated_only` dispatch guard. |
| `src/copilot/api.py` | modified | `/api/agent/resolve`, synthetic preview/commit routes, extended query contract. Broken at this checkpoint. |
| `tests/conftest.py` | modified | Autouse fixture forcing Mock and clearing any inherited API key for every test. |
| `.env.example` | modified | Reference config: DeepSeek as production default; no key value. |
| `scripts/start_backend.ps1` | modified | Startup default provider changed to DeepSeek. |

### Repair commit (this round)

| File | Kind | Responsibility |
|---|---|---|
| `src/copilot/agent/service.py` | modified | `query()` accepts `scope`, `expected_version`, `execution_mode`; provider-free `_run_local()`; `resolved_scope` on the configured path. Default function-calling loop intentionally unchanged. |
| `src/copilot/api.py` | modified | `agent_query()` dispatches `local` to `provider=None`; configured mode keeps the semantic resolver as a catalog/version/privacy guard and always uses the configured provider. |
| `tests/test_agent_local_mode.py` | new test | Three checks: uploaded data answered locally with no provider call, ambiguous question clarifies without evidence, configured mode still blocks uploaded provenance. |
| `docs/development/takeover-baseline/LEARNING.zh-CN.md` | new doc | Chinese learning packet, root cause, tradeoffs, three code walkthroughs, verification and pending work. |
| `docs/development/takeover-baseline/FILES.md` | new doc | This inventory. |
| `docs/development/02b-workbench.md` | modified | Progress index entry for this round. |

### Deliberately preserved but NOT committed

| File | Reason |
|---|---|
| `docs/development/results/14-deepseek-offline-demo.json` | Regenerated offline Mock receipt; only `request_id` and `elapsed_ms` changed. Runtime output and owner-owned record, not source. Left untouched in the working tree. |
| `.test-tmp-takeover/baseline-pytest.log` | Temporary pytest output from the takeover baseline run. Moved into an ignored directory, never deleted, never staged. |

No file was deleted, overwritten, reset or discarded. No secret, `.env`, student record, database or private log was staged.

## Verification (all Mock, no paid LLM call)

```
pytest -q --tb=short --basetemp=.test-tmp-takeover
  -> 8 failed, 387 passed, 1 warning in 63.94s   (takeover baseline)

pytest -q --tb=short --basetemp=.test-tmp-fix tests/test_agent_api.py tests/test_agent_service.py tests/test_deepseek_narrative.py tests/test_synthetic_activation.py
  -> 70 passed, 1 warning in 8.81s

pytest -q --tb=short --basetemp=.test-tmp-local2 tests/test_agent_local_mode.py
  -> 3 passed, 1 warning in 1.95s                (after fixing a duplicate-keyword defect)

pytest -q --tb=short --basetemp=.test-tmp-final
  -> 398 passed, 1 warning in 64.18s             (stable baseline)
```

The single warning is the pre-existing Starlette TestClient deprecation notice. No test was deleted, skipped or weakened. Live DeepSeek behaviour remains unverified and requires separate authorization.

## Shareable for independent review

Source: `src/copilot/agent/service.py`, `src/copilot/api.py`, `src/copilot/agent/semantics.py`, `src/copilot/data/synthetic_activation.py`; tests: `tests/test_agent_local_mode.py`, `tests/test_synthetic_activation.py`; docs: everything under `docs/development/` except unvetted screenshots; config examples: `.env.example`.

Excluded: `data/*.sqlite3*`, `data/.import-history/`, uploaded workbooks, `.env`, private logs and any screenshot not manually checked for real student data.
