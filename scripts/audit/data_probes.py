"""Execute only reviewed upstream ingestion/statistics AST nodes with real Pandas.

This deliberately avoids plotting/ML imports and is not an end-to-end UI test.
No model-provided code or SQL is accepted by this utility.
"""
import ast
import faulthandler
import hashlib
import io
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"
faulthandler.dump_traceback_later(90, exit=True)
import pandas as pd


def main() -> None:
    source = (ROOT / ".audit/runtime/app.py").read_bytes()
    assert hashlib.sha256(source).hexdigest() == "3690ea28eb3b3ed38938e19446968f98883f9e1c11fd50987eda8fd8336c371e"
    tree = ast.parse(source.decode())
    lines = {41, 42, 45, 46, 49, 50, 60, 113, 114}
    # Select statement nodes only, preserving upstream order and complete loop body.
    nodes = sorted((n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.For)) and n.lineno in lines), key=lambda n: n.lineno)
    assert [n.lineno for n in nodes] == sorted(lines)
    code = compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "upstream-reviewed-statements", "exec")
    fixtures = {
        "valid_totals_and_destroyed_gender": "Name,Math,Science,Gender\nSynthetic-A,50,60,F\nSynthetic-B,80,90,M\n",
        "numeric_class_is_score": "Name,Class,Math,Science\nSynthetic-A,1,70,80\nSynthetic-B,2,80,90\n",
        "out_of_range_scores_accepted": "Name,Math,Science\nSynthetic-A,-10,150\nSynthetic-B,120,80\n",
        "missing_scores_change_denominator": "Name,Math,Science\nSynthetic-A,60,80\nSynthetic-B,,95\nSynthetic-C,,\n",
        "keyword_excludes_valid_subject": "Name,Midterm,Math,Science\nSynthetic-A,90,60,80\nSynthetic-B,95,70,90\n",
        "history_negative_control": "Name,History,Math,Science\nSynthetic-A,90,60,80\nSynthetic-B,95,70,90\n",
    }
    results = []
    for case, csv in fixtures.items():
        scope = {"pd": pd, "uploaded_file": io.StringIO(csv)}
        exec(code, scope)
        df = scope["df"]
        item = {"case": case, "subject_cols": scope["subject_cols"], "categorical_cols": scope["categorical_cols"],
                "total_scores": df["total score"].tolist(),
                "average_scores": [None if pd.isna(v) else float(v) for v in df["average score"]]}
        if case == "valid_totals_and_destroyed_gender":
            assert item["total_scores"] == [110, 170]
            assert df["gender"].isna().all()
            item["gender_all_null"] = True
        elif case == "numeric_class_is_score":
            assert "class" in scope["subject_cols"]
            assert item["total_scores"] == [151, 172]
        elif case == "out_of_range_scores_accepted":
            assert df["math"].min() == -10 and df["science"].max() == 150
        elif case == "missing_scores_change_denominator":
            assert item["total_scores"] == [140, 95, 0]
            assert item["average_scores"] == [70, 95, None]
        elif case == "keyword_excludes_valid_subject":
            assert "midterm" in scope["excluded_cols"]
            assert scope["subject_cols"] == ["math", "science"]
        elif case == "history_negative_control":
            assert scope["subject_cols"] == ["history", "math", "science"]
            item["note"] = "History is retained: negative control for substring assumptions."
        item["passed"] = True
        results.append(item)
    output = {"method": "Exact upstream AST statements at lines 41-60 and 113-114; real pandas; no plotting, training or UI",
              "cases": results}
    (ROOT / "docs/audit/evidence/data-probes.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))
    faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
