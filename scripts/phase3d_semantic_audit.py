"""Phase 3D semantic-layer audit: full catalog x full question-pattern matrix.

Offline and free: builds the synthetic dataset in a temp directory and calls
resolve_question() directly. No model call, no network.

Purpose: "体育课" was one example. This audit enumerates EVERY catalog entity
(courses, classes, terms) against a matrix of natural question patterns so
that systemic intent-resolution defects surface all at once, instead of one
bug report at a time.

Output: JSON report + console table of SUSPECT rows.

Usage:
    python scripts/phase3d_semantic_audit.py \
        --output docs/development/phase3d-review/results/semantic-audit.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from copilot.agent.semantics import resolve_question  # noqa: E402
from copilot.data.college import parse_college_workbook  # noqa: E402
from copilot.data.importing import XLSX_TYPE  # noqa: E402
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook  # noqa: E402
from copilot.storage import Database  # noqa: E402

# Question patterns applied to every explicit course name. Each entry:
# (pattern_id, template, expectation). {c} = full course name, {a}/{b} = class
# labels, {t} = term, {c1}/{c2} = course ids.
COURSE_PATTERNS = [
    ("explicit_plain", "{c}的成绩", "ready_own_course"),
    ("explicit_analysis", "分析{c}的成绩分布", "ready_own_course"),
    ("explicit_mean_pass", "{c}的平均分和及格率", "ready_own_course"),
    ("explicit_passrate", "{c}的及格率是多少", "ready_own_course"),
    ("explicit_fail_count", "{c}有多少人不及格", "ready_own_course"),
    ("explicit_anyone_fail", "{c}有没有人不及格", "ready_own_course"),
    ("explicit_anyone_pass", "{c}有没有人及格", "ready_own_course"),
    ("explicit_who_fail", "{c}谁不及格", "ready_own_course_or_notice"),
    ("explicit_how", "{c}的成绩怎么样", "ready_own_course"),
    ("explicit_review", "{c}有哪些值得关注的地方", "ready_own_course"),
]

CAPABILITY_PATTERNS = [
    ("capability_generic", "你知道针对{c}的成绩分析嘛", "clarify_no_tool"),
    ("capability_can_you", "你能分析{c}的成绩吗", "clarify_no_tool"),
    ("capability_what", "你可以做什么分析", "clarify_no_tool"),
]


def build_catalog():
    options = SyntheticOptions(classes_per_major=2, students_per_class=6,
                               course_count=6, seed=20261111)
    workbook = generate_college_workbook(options)
    parsed = parse_college_workbook(workbook.filename, XLSX_TYPE, workbook.content)
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "audit.sqlite3")
        db.initialize()
        db.replace(parsed.dataset, source="generated", seed=options.seed)
        yield db.reporting_catalog()


def family_probe(course_name: str) -> str | None:
    """A plausible generic reference for a course, e.g. 体育 III -> 体育课."""
    match = re.match(r"^([一-鿿]+)", course_name)
    if not match:
        return None
    stem = match.group(1)
    if len(stem) < 2:
        return None
    return stem[:2] + "课"


def evaluate(case_id, question, intent, expect, course_id=None):
    row = {"id": case_id, "question": question, "expectation": expect,
           "status": intent.status, "tool": intent.tool,
           "arguments": {k: v for k, v in (intent.arguments or {}).items() if v is not None},
           "message": (intent.message or "")[:80]}
    ok = True
    reason = ""
    if expect == "ready_own_course":
        if intent.status != "ready":
            ok, reason = False, f"explicit question resolved as {intent.status}"
        elif intent.tool != "get_score_summary":
            ok, reason = False, f"unexpected tool {intent.tool}"
        elif (intent.arguments or {}).get("course_id") != course_id:
            ok, reason = False, ("course lost or replaced: "
                                 f"{(intent.arguments or {}).get('course_id')}")
    elif expect == "ready_own_course_or_notice":
        if intent.status == "ready" and (intent.arguments or {}).get("course_id") != course_id:
            ok, reason = False, "course lost or replaced"
    elif expect == "clarify_no_tool":
        if intent.status == "ready":
            ok, reason = False, "capability/ambiguous question silently executed"
        if intent.tool:
            ok, reason = False, f"tool {intent.tool} selected without confirmation"
    elif expect == "clarify_or_ready_same_scope":
        if intent.status == "ready" and not (intent.arguments or {}).get("course_id"):
            ok, reason = False, "silently widened to whole dataset"
    row["ok"] = ok
    row["reason"] = reason
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    rows = []
    for catalog in build_catalog():
        courses = list(catalog.courses)
        classes = list(catalog.classes)
        idx = 0

        def next_id(prefix):
            nonlocal idx
            idx += 1
            return f"{prefix}{idx:03d}"

        # 1. Explicit course x phrasing matrix
        for course in courses:
            for pattern_id, template, expect in COURSE_PATTERNS:
                question = template.format(c=course.course_name)
                intent = resolve_question(question, catalog)
                row = evaluate(next_id("EX"), question, intent, expect,
                               course_id=course.course_id)
                row.update({"category": "explicit_course", "pattern": pattern_id,
                            "entity": course.course_name})
                rows.append(row)

        # 2. Generic family references (the "体育课" class of problem, generalized)
        for course in courses:
            probe = family_probe(course.course_name)
            if not probe:
                continue
            question = f"分析{probe}的成绩。"
            intent = resolve_question(question, catalog)
            row = evaluate(next_id("AM"), question, intent,
                           "clarify_or_ready_same_scope")
            row.update({"category": "ambiguous_family", "pattern": "family_probe",
                        "entity": course.course_name})
            rows.append(row)

        # 3. Capability phrasings
        for course in courses[:2]:
            for pattern_id, template, expect in CAPABILITY_PATTERNS[:2]:
                question = template.format(c=course.course_name)
                intent = resolve_question(question, catalog)
                row = evaluate(next_id("CA"), question, intent, expect)
                row.update({"category": "capability", "pattern": pattern_id,
                            "entity": course.course_name})
                rows.append(row)
        question = CAPABILITY_PATTERNS[2][1]
        intent = resolve_question(question, catalog)
        row = evaluate(next_id("CA"), question, intent, "clarify_no_tool")
        row.update({"category": "capability", "pattern": "capability_what",
                    "entity": None})
        rows.append(row)

        # 4. Class x course combinations
        if len(classes) >= 2 and courses:
            a, b = classes[0].class_label, classes[1].class_label
            course = courses[0]
            combos = [
                ("class_course", f"{a}的{course.course_name}成绩", "ready_own_course",
                 course.course_id),
                ("class_fail", f"{a}的{course.course_name}有没有人不及格", "ready_own_course",
                 course.course_id),
            ]
            for pattern_id, question, expect, cid in combos:
                intent = resolve_question(question, catalog)
                row = evaluate(next_id("CL"), question, intent, expect, course_id=cid)
                row.update({"category": "class_course", "pattern": pattern_id,
                            "entity": f"{a}+{course.course_name}"})
                rows.append(row)

        # 5. Course-ID boundary cases (ASCII id adjacent to Chinese)
        for course in courses[:2]:
            cid = course.course_id
            for pattern_id, question in [
                    ("id_adjacent", f"比较{cid}和{cid}的成绩"),
                    ("id_spaced", f"比较 {cid} 和 {cid} 的成绩")]:
                intent = resolve_question(question, catalog)
                row = {"id": next_id("BD"), "question": question,
                       "expectation": "same_resolution_both_spellings",
                       "status": intent.status, "tool": intent.tool,
                       "arguments": {k: v for k, v in (intent.arguments or {}).items()
                                     if v is not None},
                       "message": (intent.message or "")[:80],
                       "category": "id_boundary", "pattern": pattern_id,
                       "entity": cid}
                rows.append(row)

    suspects = [r for r in rows if r.get("ok") is False]
    # Boundary pairs: compare adjacent vs spaced resolution.
    boundary = [r for r in rows if r["category"] == "id_boundary"]
    for i in range(0, len(boundary), 2):
        pair = boundary[i:i + 2]
        if len(pair) == 2 and pair[0]["status"] != pair[1]["status"]:
            for r in pair:
                r["ok"] = False
                r["reason"] = "adjacent/spaced spellings resolve differently"
            suspects.extend([r for r in pair if r not in suspects])

    report = {"total_cases": len(rows),
              "suspect_count": len({r['id'] for r in suspects}),
              "by_category": {},
              "suspects": suspects, "all_cases": rows}
    for r in rows:
        bucket = report["by_category"].setdefault(r["category"], {"total": 0, "suspect": 0})
        bucket["total"] += 1
        if r.get("ok") is False:
            bucket["suspect"] += 1

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(json.dumps({"total": report["total_cases"],
                      "suspects": report["suspect_count"],
                      "by_category": report["by_category"]}, ensure_ascii=False, indent=2))
    for r in suspects:
        print(f"SUSPECT [{r['category']}/{r['pattern']}] {r['question']}")
        print(f"   -> {r['status']}/{r['tool']} {r['arguments']} | {r['reason']}")
    print("written:", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
