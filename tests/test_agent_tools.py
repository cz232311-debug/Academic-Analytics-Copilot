from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from copilot.agent.models import AgentError
from copilot.agent.tools import TOOLS, ToolRegistry
from copilot.models import DataError
from copilot.storage import Database


@pytest.fixture
def registry(tmp_path, tiny_dataset):
    db = Database(tmp_path / "agent.sqlite3")
    db.initialize()
    db.replace(tiny_dataset, source="generated", seed=1)
    return ToolRegistry(db)


def test_four_strict_schemas_and_no_execution_tool():
    definitions = ToolRegistry.definitions()
    assert len(definitions) == 4
    assert set(TOOLS) == {"get_dataset_overview", "get_score_summary", "compare_classes", "get_catalog"}
    for definition in definitions:
        schema = definition["function"]["parameters"]
        assert definition["function"]["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema.get("required", [])) == set(schema["properties"])


def test_summary_delegates_and_exposes_only_aggregates(registry, tiny_dataset):
    args = registry.validate("get_score_summary", '{"term":"2026-T1","course_id":"G1-C01","class_id":null,"grade_level":null}')
    with patch.object(registry.engine, "summary", wraps=registry.engine.summary) as summary:
        result = registry.execute("get_score_summary", args, version=tiny_dataset.version, evidence_id="r1")
    assert summary.call_count == 1
    assert result.data["average_score"] == "73.33" and result.data["pass_rate_percent"] == "66.67"
    assert "student_id" not in result.model_dump_json() and "Synthetic Student" not in result.model_dump_json()


@pytest.mark.parametrize("name,args", [
    ("execute_sql", '{"sql":"SELECT * FROM students"}'),
    ("python", '{"code":"print(1)"}'),
    ("get_dataset_overview", '{"extra":"secret"}'),
    ("get_score_summary", '{"term":"2026-T1","course_id":null,"class_id":null,"grade_level":"1"}'),
    ("get_score_summary", '{"term":"2026-T1","course_id":null,"class_id":null,"grade_level":true}'),
    ("get_score_summary", '{"term":"2026-T1","course_id":null,"class_id":null,"grade_level":4}'),
    ("get_catalog", '{"grade_level":1,"grade_level":2}'),
    ("get_catalog", '{"grade_level":NaN}'),
    ("get_catalog", '[]'),
    ("get_catalog", 'not JSON'),
])
def test_bad_model_arguments_are_rejected_without_echo(registry, name, args):
    with pytest.raises(AgentError) as error:
        registry.validate(name, args)
    assert "secret" not in str(error.value) and "SELECT" not in str(error.value)


def test_other_tools_reuse_repository_and_comparison(registry, tiny_dataset):
    version = tiny_dataset.version
    for name, args in [("get_dataset_overview", {}), ("get_catalog", {"grade_level": 1}),
                       ("compare_classes", {"course_id": "G1-C01", "class_ids": ["C001", "C002"], "term": "2026-T1"})]:
        result = registry.execute(name, registry.validate(name, json.dumps(args)), version=version, evidence_id="r1")
        assert result.dataset_version == version
        if name == "compare_classes":
            assert result.data["highest_mean_class_ids"] == ["C002"]
        if name == "get_catalog":
            assert {c["grade_level"] for c in result.data["courses"]} == {1}


def test_upload_and_dataset_swap_are_blocked_inside_the_read(registry, tiny_dataset):
    args = registry.validate("get_dataset_overview", "{}")
    registry.database.replace(tiny_dataset)
    with pytest.raises(DataError) as error:
        registry.execute("get_dataset_overview", args, version=tiny_dataset.version, evidence_id="r1")
    assert error.value.status == 403
    changed = replace(tiny_dataset, grade_records=(tiny_dataset.grade_records[0].model_copy(update={"score_cents": 9000}), *tiny_dataset.grade_records[1:]))
    registry.database.replace(changed, source="generated", seed=2)
    with pytest.raises(DataError) as error:
        registry.execute("get_dataset_overview", args, version=tiny_dataset.version, evidence_id="r1")
    assert error.value.status == 409


def test_unknown_course_and_empty_catalog_scope_are_explicit(registry, tiny_dataset):
    args = registry.validate("get_score_summary", '{"term":"2026-T1","course_id":"G1-C99","class_id":null,"grade_level":null}')
    with pytest.raises(DataError) as error:
        registry.execute("get_score_summary", args, version=tiny_dataset.version, evidence_id="r1")
    assert error.value.status == 404
    empty = registry.execute("get_catalog", registry.validate("get_catalog", '{"grade_level":3}'),
                             version=tiny_dataset.version, evidence_id="r1")
    assert empty.data["courses"] == [] and empty.data["classes"] == []
