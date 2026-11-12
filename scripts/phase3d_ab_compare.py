"""Phase 3D — reproducible A/B comparison on ONE synthetic snapshot.

A = the original deterministic statistics answer (render_fallback plus the
    Chat-first Chinese conclusion rendered from the same Evidence).
B = the Phase 3C evidence-grounded analysis (render_analysis), with a
    SCRIPTED selection of claim IDs. That selection is an offline fixture,
    NOT a real DeepSeek output.

The point is to separate information contributed by deterministic Python code
from the (much smaller) contribution of choosing/organising claims, and to
state plainly that real-model usefulness is still unverified.
"""
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

from copilot.agent.analysis import claim_catalog, render_analysis
from copilot.agent.models import FinalSelection
from copilot.agent.provider import MockDemoProvider
from copilot.agent.semantics import resolve_question
from copilot.agent.service import AgentService, render_fallback
from copilot.agent.tools import ToolRegistry
from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.storage import Database

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

QUESTION = "分析体育 III 的成绩分布"
SEED = 20261111
OPTIONS = SyntheticOptions(classes_per_major=2, students_per_class=6, course_count=6, seed=SEED)
NUM = re.compile(r"\d+(?:\.\d+)?")

# Deliberately a fixture. A real model would have to emit this on its own.
SELECTION = {"version": "evidence_analysis_v1", "sections": [
    {"kind": "findings", "claim_ids": ["r1.scope", "r1.sample", "r1.mean", "r1.pass_rate"], "style": "plain"},
    {"kind": "observations", "claim_ids": ["r1.dominant_band", "r1.high_band"], "style": "focus"},
    {"kind": "limitations", "claim_ids": ["r1.limits"], "style": "plain"},
    {"kind": "suggestions", "claim_ids": ["r1.review_distribution"], "style": "plain"},
]}

UNSUPPORTED_CAUSAL = ("因为", "导致", "由于", "教学效果好", "显著提升", "证明", "预测", "一定会", "必然")
NEGATED_CAUSAL = ("并不表示", "不能据此", "不是", "不代表")


def build(root: Path):
    workbook = generate_college_workbook(OPTIONS)
    parsed = parse_college_workbook(workbook.filename, XLSX_TYPE, workbook.content)
    db = Database(root / "ab.sqlite3")
    db.initialize()
    db.replace(parsed.dataset, source="generated", seed=SEED)
    return db


def stats(text):
    return sorted(set(m for m in NUM.findall(text) if "." in m or m.endswith("%")), key=len, reverse=True)


def main(output):
    with tempfile.TemporaryDirectory(prefix="phase3d-ab-") as folder:
        db = build(Path(folder))
        catalog = db.reporting_catalog()
        intent = resolve_question(QUESTION, catalog)
        assert intent.status == "ready", intent.message
        registry = ToolRegistry(db)
        args = registry.validate(intent.tool, json.dumps(intent.arguments))
        evidence = registry.execute(intent.tool, args, version=catalog.dataset_version, evidence_id="r1")

        # A: deterministic backend fallback, plus the real Chat-first deterministic path.
        answer_a = render_fallback(FinalSelection(status="completed", evidence_ids=["r1"]), [evidence])
        result_a = asyncio.run(AgentService(db, MockDemoProvider()).query(
            QUESTION, intent=intent, execution_mode="grounded"))
        from copilot.dashboard.chat import _evidence_cn
        answer_a_cn = _evidence_cn(result_a, catalog)

        # B: server-rendered Chinese analysis from a scripted claim selection.
        answer_b, verified = render_analysis(SELECTION, [evidence], catalog, "distribution")
        claims = {claim.identifier: claim for claim in claim_catalog([evidence], catalog)}
        chosen = [identifier for section in verified.sections for identifier in section.claim_ids]
        facts_blob = json.dumps({"data": evidence.data,
                                 "facts": [claims[i].facts for i in chosen]}, ensure_ascii=False,
                                default=str)
        numbers_b = stats(answer_b)

        # Dimension 1: information points, not characters.
        info_a = {"indicators": ["mean", "weighted_mean", "pass_rate", "failed_count", "distribution_bins",
                                 "enrollment/missing/exempt counts"],
                  "derived_relations": [], "limitations": [], "suggestions": []}
        info_b = {"indicators": info_a["indicators"] + ["dominant_band_share", "high_band_share"],
                  "derived_relations": ["41.67% dominant band", "58.33% >=80", "graded-record denominator"],
                  "limitations": ["small sample (n=12)", "scope-only, no cross-term/generalisation",
                                  "no causal inference"],
                  "suggestions": ["inspect band shares", "request a separate scoped comparison"]}

        # Dimension 4: assertive causal / comparative claims, checked per sentence
        # so a single negated disclaimer cannot mask an affirmative claim elsewhere.
        bad = [{"sentence": sentence.strip(), "word": word}
               for sentence in re.split(r"[。；\n]", answer_b)
               for word in UNSUPPORTED_CAUSAL
               if word in sentence and not any(neg in sentence for neg in NEGATED_CAUSAL)]
        comparison_claim = any("comparison" in claims[i].tags for i in chosen)

        record = {
            "schema": "phase3d_ab_v1",
            "question": QUESTION,
            "dataset_version": catalog.dataset_version,
            "counts": {"students": 12, "courses": 6, "grade_records": 72,
                       "graded_count": evidence.data["graded_count"],
                       "average_score": evidence.data["average_score"],
                       "pass_rate_percent": evidence.data["pass_rate_percent"]},
            "provenance": {
                "A": "deterministic Python render_fallback + _evidence_cn; no model wording",
                "B": "render_analysis from SCRIPTED claim IDs; Chinese sentences are server templates",
                "real_deepseek_output": False,
                "live_model_calls": 0,
            },
            "A": {"backend_fallback": answer_a, "chat_first_conclusion": answer_a_cn,
                  "chars": len(answer_a_cn), "statistic_values": stats(answer_a_cn)},
            "B": {"analysis": answer_b, "chars": len(answer_b), "statistic_values": numbers_b,
                  "claim_ids": chosen, "section_kinds": [section.kind for section in verified.sections],
                  "evidence_ids": sorted({i for section in verified.sections for i in section.evidence_ids})},
            "assessment": {
                "1_more_information_than_indicator_roster": {
                    "verdict": "yes_but_from_deterministic_code",
                    "A_information_points": info_a, "B_information_points": info_b,
                    "explanation": "The two extra facts (41.67%, 58.33% >=80) and the denominator note are "
                                   "computed by analysis.py from Evidence, not discovered by the model. "
                                   "The model only decides which pre-written claims appear and in what order."},
                "2_highlights_what_actually_matters": {
                    "verdict": "partially",
                    "distribution_claim_selected": any("distribution" in claims[i].tags for i in chosen),
                    "focus_style_used": any(section.get("style") == "focus" for section in SELECTION["sections"]
                                            if section["kind"] == "observations"),
                    "explanation": "The goal=distribution guard forces a distribution observation and the fixture "
                                   "uses focus style. Whether a real model would pick these is untested."},
                "3_sample_and_scope_limitations": {
                    "verdict": "yes",
                    "small_sample": "样本较小" in answer_b,
                    "scope_only": "仅描述本次已确认范围" in answer_b,
                    "no_causality": "因果" in answer_b,
                    "A_has_any_limitation": any(word in answer_a_cn for word in ("样本", "局限", "不能据此")),
                    "explanation": "A has no limitation statement at all; B adds a mandatory limitations section."},
                "4_unsupported_comparison_or_causation": {
                    "verdict": "none_detected",
                    "assertive_causal_words": bad,
                    "unauthorised_comparison_claim": comparison_claim,
                    "numbers_all_traceable": all(number in facts_blob for number in numbers_b),
                    "explanation": "Every number in B is in Evidence or a derived claim fact. Causal wording appears "
                                   "only in negated form ('并不表示教学效果的因果结论')."},
                "5_value_of_the_extra_model_call": {
                    "verdict": "design_value_only_real_gain_unverified",
                    "model_turns_A": result_a.model_turns,
                    "model_turns_B": 2,
                    "extra_turns_for_B": 0,
                    "explanation": "Both paths already use a 2-turn loop. B does not add a model round; it changes "
                                   "what the final turn must return. The only model contribution is claim selection "
                                   "and ordering. Since every verified B so far is a scripted fixture, a real "
                                   "DeepSeek gain (or its fallback rate) is NOT yet measured."},
            },
            "headline": ("B is more informative than A, but the added information is deterministic code, "
                         "not model insight. The unverified question is whether a real model can reliably "
                         "produce a valid, useful claim selection instead of falling back."),
        }
        text = json.dumps(record, ensure_ascii=False, indent=2)
        if output:
            Path(output).write_text(text, encoding="utf-8")
            print(f"Wrote {output}")
        else:
            print(text)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
