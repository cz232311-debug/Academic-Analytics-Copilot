# College import — exact changed-file inventory

Baseline: `fb590744f70529ec2844de061b5525400a5563c2`. Business implementation checkpoint: `ef651676cacc81224a6528422ccbe2709bb23d6f`.

The final export manifest identifies the later documentation checkpoint. A=added, M=modified; no deletions. Source was committed after completed checks; these are actual increment commits.

| Status | Repository path |
|---|---|
| M | `.gitignore` |
| M | `README.md` |
| M | `START_HERE.zh-CN.md` |
| A | `docs/development/02b-college-import.md` |
| M | `docs/development/02b-workbench.md` |
| A | `docs/development/college-import-review/FILES.md` |
| A | `docs/development/college-import-review/UNTRACKED-SOURCES.txt` |
| A | `docs/development/results/college-import-pytest.xml` |
| A | `docs/development/results/college-import-reference-summary.json` |
| M | `src/copilot/analytics.py` |
| M | `src/copilot/api.py` |
| M | `src/copilot/dashboard/app.py` |
| M | `src/copilot/dashboard/client.py` |
| A | `src/copilot/dashboard/import_panel.py` |
| M | `src/copilot/dashboard/presentation.py` |
| A | `src/copilot/data/college.py` |
| A | `src/copilot/data/college_service.py` |
| M | `src/copilot/data/importing.py` |
| M | `src/copilot/models.py` |
| M | `src/copilot/schema.sql` |
| M | `src/copilot/schemas.py` |
| M | `src/copilot/storage.py` |
| A | `tests/college_fixture.py` |
| A | `tests/test_college_adapter.py` |
| A | `tests/test_college_api.py` |
| A | `tests/test_college_compatibility.py` |
| A | `tests/test_college_dashboard.py` |

Responsibilities, tests, limitations and three call-chain explanations are in [the learning/delivery record](../02b-college-import.md).

Pre-existing owner change (excluded from every increment commit): `docs/development/results/14-deepseek-offline-demo.json`.
Exported cumulative diff and per-commit patches cover the final checkpoint. A separate working-tree diff is labeled owner-existing. Generated ZIP files are outside their own commit history.
