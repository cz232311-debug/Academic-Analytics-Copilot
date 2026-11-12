"""Phase 3D — minimal reproducible DeepSeek evidence-analysis protocol check.

This script PREPARES a live acceptance run. It never performs a paid call unless
all of the following are true at the same time:

  * --mode live
  * --i-authorize-paid-calls
  * COPILOT_LLM_PROVIDER=deepseek and a non-empty COPILOT_LLM_API_KEY

`plan` (the default) is entirely local: it builds the synthetic dataset,
resolves the three questions and reports the predicted request shape. `fixture`
runs the full AgentService + OpenAICompatibleProvider code path against an
in-process httpx.MockTransport, so the recording logic is exercised with zero
network traffic and zero cost. Neither prints the key, the base URL, raw
workbook rows, student IDs or names.

Usage (all offline unless explicitly stated):
    python scripts/phase3d_live_protocol_check.py --mode plan
    python scripts/phase3d_live_protocol_check.py --mode fixture
    python scripts/phase3d_live_protocol_check.py --mode fixture --output <file>.json

Requires the project virtualenv (tests/conftest.py forces Mock only inside
pytest; this script runs outside pytest on purpose).
"""
import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path

import httpx

from copilot.agent.analysis import analysis_context, analysis_goal, analysis_requirements, claim_catalog
from copilot.agent.provider import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, LLMSettings,
                                    OpenAICompatibleProvider, build_provider)
from copilot.agent.semantics import resolve_question
from copilot.agent.service import AgentService, ANALYSIS_PROMPT, GROUNDED_SYSTEM
from copilot.data.college import parse_college_workbook
from copilot.data.importing import XLSX_TYPE
from copilot.data.synthetic_college import SyntheticOptions, generate_college_workbook
from copilot.storage import Database
from pydantic import SecretStr

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEED = 20261111
OPTIONS = SyntheticOptions(classes_per_major=2, students_per_class=6, course_count=6, seed=SEED)
QUESTIONS = [
    ("C1", "分析体育 III 的成绩分布。"),
    ("C2", "分析体育 III 的成绩表现，有哪些值得关注的地方？"),
    ("C3", "分析体育课的成绩。"),
]
# Hard budget for a live run. C3 resolves to a clarification and never builds
# a provider, so at most two requests can reach the model.
MAX_LIVE_REQUESTS = 2
MAX_MODEL_TURNS = 4
MAX_OUTPUT_TOKENS = 2048
REQUEST_TIMEOUT_SECONDS = 45

RECORDED_FIELDS = ("provider", "model", "simulated", "tools_and_arguments", "model_turns",
                   "answer_mode", "fallback_reason", "analysis_status",
                   "analysis_fallback_reason", "claim_ids", "chinese_analysis",
                   "evidence_consistency", "usage", "elapsed_ms", "cost")


def build_database(root: Path):
    workbook = generate_college_workbook(OPTIONS)
    parsed = parse_college_workbook(workbook.filename, XLSX_TYPE, workbook.content)
    assert parsed.dataset is not None
    db = Database(root / "phase3d-synthetic.sqlite3")
    db.initialize()
    db.replace(parsed.dataset, source="generated", seed=SEED)
    return db


def resolved_cases(db):
    catalog = db.reporting_catalog()
    rows = []
    for case_id, question in QUESTIONS:
        intent = resolve_question(question, catalog)
        goal = analysis_goal(question, intent.tool) if intent.status == "ready" else None
        required = (analysis_requirements(question) if intent.tool in {"get_score_summary", "compare_classes"}
                    and intent.status == "ready" else ())
        rows.append({
            "id": case_id, "question": question,
            "intent_status": intent.status,
            "tool": intent.tool,
            "arguments": intent.arguments,
            "analysis_goal": goal,
            "required_metrics": list(required),
            "would_call_model": intent.status == "ready",
            "clarification_candidates": [choice["label"] for choice in intent.choices[:6]],
        })
    return rows, catalog


def context_preview(db, catalog):
    """Measure the exact payload the model would see, without sending anything."""
    from copilot.agent.tools import ToolRegistry
    intent = resolve_question(QUESTIONS[0][1], catalog)
    registry = ToolRegistry(db)
    args = registry.validate(intent.tool, json.dumps(intent.arguments))
    result = registry.execute(intent.tool, args, version=catalog.dataset_version, evidence_id="r1")
    context = analysis_context([result], catalog, "distribution", ())
    tool_payload = json.dumps({**result.model_dump(mode="json"), "analysis_context": context}, ensure_ascii=False)
    return {
        "system_prompt_chars": len(GROUNDED_SYSTEM) + len("\nConfirmed analysis goal: distribution."),
        "analysis_protocol_chars": len(ANALYSIS_PROMPT),
        "user_message_chars": len(intent.canonical_question),
        "claim_count": len(context["claims"]),
        "mandatory_claim_count": len(context["mandatory_claim_ids"]),
        "tool_result_chars": len(tool_payload),
        "evidence_ids": [result.evidence_id],
        "aggregate_counts": {key: result.data[key] for key in
                             ("student_count", "enrollment_count", "graded_count", "missing_count", "exempt_count")},
    }


def selection_from_context(context):
    """Emulate a compliant model: choose only existing claim IDs, cover every rule."""
    claims = {item["claim_id"]: item for item in context["claims"]}
    mandatory = list(context["mandatory_claim_ids"])
    sections = {kind: [] for kind in ("findings", "observations", "limitations", "suggestions")}
    for identifier in mandatory:
        sections[claims[identifier]["kind"]].append(identifier)
    covered = {tag for identifier in mandatory for tag in claims[identifier]["tags"]}
    needed = set(context["required_goal_tags"])
    if context["goal"] == "distribution":
        needed |= {"distribution", "distribution_unavailable"}
    if context["goal"] == "comparison":
        needed.add("comparison")
    for item in context["claims"]:
        if not needed - covered:
            break
        if item["claim_id"] in mandatory or not set(item["tags"]) & needed:
            continue
        sections[item["kind"]].append(item["claim_id"])
        covered |= set(item["tags"])
    for kind, identifiers in sections.items():
        if not identifiers:
            fallback = next((item for item in context["claims"] if item["kind"] == kind), None)
            if fallback:
                identifiers.append(fallback["claim_id"])
    return {"version": "evidence_analysis_v1", "sections": [
        {"kind": kind, "claim_ids": identifiers,
         "style": "connected" if kind == "observations" else "plain"}
        for kind, identifiers in sections.items()]}


def fixture_provider():
    """A real OpenAICompatibleProvider wired to an in-process transport."""
    def handler(request):
        body = json.loads(request.content)
        messages = body["messages"]
        last = messages[-1]
        if last["role"] == "tool":
            payload = json.loads(last["content"])
            selection = selection_from_context(payload["analysis_context"])
            content = json.dumps({"status": "completed", "evidence_ids": [payload["evidence_id"]],
                                  "analysis": selection})
            message = {"role": "assistant", "content": content}
            finish = "stop"
            usage = {"prompt_tokens": 5200, "completion_tokens": 210, "total_tokens": 5410}
        else:
            plan = json.loads(last["content"].removeprefix("ACADEMIC_INTENT "))
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": plan["tool"], "arguments": json.dumps(plan["arguments"])}}]}
            finish = "tool_calls"
            usage = {"prompt_tokens": 1800, "completion_tokens": 70, "total_tokens": 1870}
        return httpx.Response(200, json={"id": "phase3d-fixture", "choices": [
            {"message": message, "finish_reason": finish}], "usage": usage})

    settings = LLMSettings(provider="deepseek", model=DEEPSEEK_MODEL,
                           api_key=SecretStr("offline-fixture-placeholder"),
                           base_url=DEEPSEEK_BASE_URL)
    return OpenAICompatibleProvider(settings, transport=httpx.MockTransport(handler))


def live_provider():
    settings = LLMSettings.from_env()
    if settings.provider != "deepseek":
        raise SystemExit("Refusing a live run: COPILOT_LLM_PROVIDER must be 'deepseek'.")
    if not settings.api_key.get_secret_value().strip():
        raise SystemExit("Refusing a live run: COPILOT_LLM_API_KEY is missing.")
    return build_provider(settings)


def evidence_consistency(result, catalog):
    """Structural checks: the answer may only contain server-rendered claims."""
    evidence_ids = {item.evidence_id for item in result.evidence}
    versions = {item.dataset_version for item in result.evidence} | {result.dataset_version}
    sections = result.analysis.sections if result.analysis else []
    by_id = {claim.identifier: claim for claim in claim_catalog(result.evidence, catalog)}
    chosen = [identifier for section in sections for identifier in section.claim_ids]
    texts = [by_id[identifier].text for identifier in chosen if identifier in by_id]
    claim_text = "".join(texts)
    numbers = set(re.findall(r"\d+\.\d+|\d+", result.answer))
    evidence_blob = json.dumps([item.data for item in result.evidence], ensure_ascii=False) + claim_text
    return {
        "version_aligned": len(versions) == 1,
        "evidence_refs_valid": all(set(section.evidence_ids) <= evidence_ids for section in sections),
        "selected_claims_exist": len(texts) == len(chosen),
        "answer_numbers_traceable_to_evidence": all(number in evidence_blob for number in numbers),
        "claim_count": len(chosen),
        "answer_chars": len(result.answer),
    }


def record_case(db, catalog, case_id, question, provider, *, live):
    intent = resolve_question(question, catalog)
    row = {"id": case_id, "question": question, "intent_status": intent.status,
           "tool": intent.tool, "arguments": intent.arguments,
           "would_call_model": intent.status == "ready"}
    if intent.status != "ready":
        row.update({"run_kind": "local_notice", "provider": "semantic",
                    "model": "catalog-bound-v1", "simulated": False,
                    "model_turns": 0, "tools_and_arguments": [], "answer_mode": "notice",
                    "fallback_reason": None, "analysis_status": "not_requested",
                    "analysis_fallback_reason": None, "claim_ids": [],
                    "chinese_analysis": intent.message,
                    "evidence_consistency": {"no_evidence_expected": True},
                    "usage": None, "elapsed_ms": 0, "cost": None,
                    "live_model_calls": 0})
        return row
    started = time.monotonic()
    result = asyncio.run(AgentService(db, provider, timeout_seconds=REQUEST_TIMEOUT_SECONDS).query(
        question, intent=intent, execution_mode="grounded"))
    elapsed = int((time.monotonic() - started) * 1000)
    sections = result.analysis.sections if result.analysis else []
    row.update({
        "run_kind": "live_call" if live else "fixture_replay",
        "provider": result.provider, "model": result.model, "simulated": result.simulated,
        "tools_and_arguments": [{"tool": trace.tool, "arguments": trace.arguments,
                                 "status": trace.status} for trace in result.tool_calls],
        "model_turns": result.model_turns,
        "answer_mode": result.answer_mode,
        "fallback_reason": result.fallback_reason,
        "analysis_status": result.analysis_status,
        "analysis_fallback_reason": result.analysis_fallback_reason,
        "claim_ids": [identifier for section in sections for identifier in section.claim_ids],
        "evidence_ids": sorted({identifier for section in sections for identifier in section.evidence_ids}),
        "chinese_analysis": result.answer,
        "evidence_consistency": evidence_consistency(result, catalog),
        "usage": result.usage.model_dump() if result.usage else None,
        "model_trace": [trace.model_dump(mode="json") for trace in result.model_trace],
        "elapsed_ms": elapsed,
        # Cost is deliberately never guessed from a third-party price list.
        "cost": None,
        "live_model_calls": 1 if live else 0,
    })
    return row


def run(mode, output):
    with tempfile.TemporaryDirectory(prefix="phase3d-protocol-") as folder:
        db = build_database(Path(folder))
        cases, catalog = resolved_cases(db)
        preview = context_preview(db, catalog)
        info = db.overview()
        payload = {
            "schema": "phase3d_live_protocol_v1",
            "mode": mode,
            "dataset": {"source": "generated", "seed": SEED,
                        "counts": {key: info.counts.get(key) for key in
                                   ("students", "classes", "courses", "enrollments", "grade_records")},
                        "dataset_version": info.dataset_version},
            "recorded_fields": list(RECORDED_FIELDS),
            "budget": {"max_live_requests": MAX_LIVE_REQUESTS,
                       "max_model_turns_per_request": MAX_MODEL_TURNS,
                       "max_output_tokens": MAX_OUTPUT_TOKENS,
                       "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
                       "automatic_retries": 0,
                       "note": "C3 resolves locally to a clarification and builds no provider."},
            "request_preview": preview,
            "cases": cases,
        }
        if mode != "plan":
            provider = fixture_provider() if mode == "fixture" else live_provider()
            payload["backend"] = {
                "provider": provider.name, "model": provider.model,
                "provider_simulated_attribute": provider.simulated,
                "transport": ("httpx.MockTransport (in-process, zero network)"
                              if mode == "fixture" else "httpx.AsyncClient (live HTTPS)"),
                "this_run_is_a_real_model_call": mode == "live",
            }
            payload["measurement_note"] = (
                "fixture: usage and elapsed_ms are INJECTED replay values, not DeepSeek telemetry. "
                "Only --mode live produces genuine token, latency and cost observations. "
                "A fixture success rate is not a real-model accuracy figure."
                if mode == "fixture" else
                "live: usage/latency come from the provider response and wall clock; "
                "cost is left null for the owner to reconcile against the official bill.")
            payload["cases"] = [record_case(db, catalog, case["id"], case["question"], provider,
                                            live=mode == "live") for case in cases]
            payload["live_model_calls"] = sum(case.get("live_model_calls", 0) for case in payload["cases"])
        else:
            payload["backend"] = {"provider": "not constructed", "model": "not constructed",
                                  "transport": "none", "this_run_is_a_real_model_call": False}
            payload["measurement_note"] = "plan: local intent resolution and payload sizing only; no provider built."
            payload["live_model_calls"] = 0
        payload["safety"] = {"api_key_printed": False, "base_url_printed": False,
                             "raw_student_rows_printed": False,
                             "data_source": "server-generated synthetic only"}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if output:
            Path(output).write_text(text, encoding="utf-8")
            print(f"Wrote {output}")
        else:
            print(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "fixture", "live"), default="plan")
    parser.add_argument("--output", help="Write the JSON record to this path instead of stdout")
    parser.add_argument("--i-authorize-paid-calls", action="store_true",
                        help="Explicit, separate authorization required for --mode live")
    args = parser.parse_args()
    if args.mode == "live" and not args.i_authorize_paid_calls:
        raise SystemExit("Refusing --mode live without --i-authorize-paid-calls. "
                         "Obtain separate explicit authorization before any paid call.")
    run(args.mode, args.output)


if __name__ == "__main__":
    main()
