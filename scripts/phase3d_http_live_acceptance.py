"""Phase 3D live protocol acceptance against an already-running backend.

Drives the REAL DeepSeek provider through the HTTP API of a backend the user
started (key lives only in that backend process; this script never sees it).

Safety properties:
- Refuses to run unless the backend reports provider=deepseek, simulated=false.
- Refuses to run unless the active dataset is synthetic (source=generated),
  so no real student data is ever sent to the external model.
- Hard-bounded: at most 3 questions, one attempt each, no retries.
- Records provider/model/tools/arguments/model_turns/answer_mode/
  analysis_status/fallback_reason/claim IDs/answer/usage/latency and a
  local evidence-consistency check (every number in the answer must be
  traceable to the backend evidence).

Usage:
    python scripts/phase3d_http_live_acceptance.py \
        --base-url http://127.0.0.1:8000 \
        --output docs/development/phase3d-review/results/protocol-live.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

CASES = [
    {"id": "C1", "question": "分析体育 III 的成绩分布。",
     "expect_status": "completed", "expect_tool": "get_score_summary"},
    {"id": "C2", "question": "分析体育 III 的成绩表现，有哪些值得关注的地方？",
     "expect_status": "completed", "expect_tool": "get_score_summary"},
    {"id": "C3", "question": "分析体育课的成绩。",
     "expect_status": "needs_clarification", "expect_tool": None},
]

MAX_CASES = 3  # hard cap on paid calls, no retries


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def numbers_in_evidence(evidence: list[dict], analysis: dict | None = None) -> set[str]:
    # Traceable sources: raw evidence values AND the server-built claim texts
    # (analysis.sections[].text). A verified evidence_analysis answer is
    # assembled exclusively from those claim texts, so numbers appearing only
    # in claim wording (e.g. derived percentages) are still server-traceable.
    blob = json.dumps(evidence, ensure_ascii=False)
    if analysis:
        for section in analysis.get("sections") or []:
            blob += json.dumps(section.get("text") or "", ensure_ascii=False)
            blob += json.dumps(section.get("claim_ids") or [], ensure_ascii=False)
    return numbers_in(blob)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    out = {"mode": "live", "executed_at": datetime.now(timezone.utc).isoformat(),
           "base_url": args.base_url, "paid_call_cap": MAX_CASES, "cases": []}

    # trust_env=False: this shell exports HTTP_PROXY=127.0.0.1:<port> with an
    # empty NO_PROXY; routing localhost through that proxy makes keep-alive
    # connections return a bogus 404 on every request after the first.
    client = httpx.Client(base_url=args.base_url, timeout=120.0, trust_env=False)

    status = client.get("/api/agent/status").json()
    out["backend"] = {k: status.get(k) for k in
                      ("provider", "model", "simulated", "max_model_turns",
                       "max_tool_calls", "request_timeout_seconds")}
    if not (status.get("provider") == "deepseek" and status.get("simulated") is False):
        out["error"] = "backend is not a live DeepSeek provider; refusing to run"
        Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(out["backend"], ensure_ascii=False))
        print("REFUSED: backend is not live deepseek (simulated=%s)" % status.get("simulated"))
        return 1

    dataset_resp = client.get("/api/dataset")
    dataset = dataset_resp.json()
    out["dataset"] = {"source": dataset.get("source"), "seed": dataset.get("seed"),
                      "counts": dataset.get("counts"),
                      "dataset_version": dataset.get("dataset_version"),
                      "http_status": dataset_resp.status_code}
    if dataset.get("source") != "generated":
        out["error"] = "active dataset is not synthetic; refusing to send it to the model"
        out["dataset_raw"] = dataset_resp.text[:400]
        Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REFUSED: active dataset source is", dataset.get("source"))
        return 1
    version = dataset["dataset_version"]

    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for case in CASES[:MAX_CASES]:
        row = {"id": case["id"], "question": case["question"]}
        started = time.perf_counter()
        try:
            resp = client.post("/api/agent/query",
                               json={"question": case["question"],
                                     "expected_version": version})
            row["http_status"] = resp.status_code
            body = resp.json()
        except Exception as exc:  # noqa: BLE001 - record, never retry
            row["error"] = f"{type(exc).__name__}: {exc}"
            out["cases"].append(row)
            continue
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)

        analysis = body.get("analysis") or {}
        sections = analysis.get("sections") or []
        claim_ids = [cid for section in sections for cid in section.get("claim_ids", [])]
        usage = body.get("usage") or {}
        for key in total_usage:
            total_usage[key] += int(usage.get(key) or 0)

        row.update({
            "provider": body.get("provider"), "model": body.get("model"),
            "simulated": body.get("simulated"), "status": body.get("status"),
            "model_turns": body.get("model_turns"),
            "tools_and_arguments": [
                {"tool": ev.get("tool"), "arguments": ev.get("arguments")}
                for ev in body.get("evidence", [])],
            "answer_mode": body.get("answer_mode"),
            "analysis_status": body.get("analysis_status"),
            "analysis_fallback_reason": body.get("analysis_fallback_reason"),
            "fallback_reason": body.get("fallback_reason"),
            "claim_ids": claim_ids,
            "usage": usage,
            "chinese_analysis": body.get("answer"),
        })

        if body.get("status") == "completed":
            answer_numbers = numbers_in(body.get("answer") or "")
            ev_numbers = numbers_in_evidence(body.get("evidence") or [],
                                             body.get("analysis"))
            row["evidence_consistency"] = {
                "answer_numbers": sorted(answer_numbers),
                "untraceable_numbers": sorted(answer_numbers - ev_numbers),
                "all_numbers_traceable": answer_numbers <= ev_numbers,
            }
        row["expectation_met"] = (
            body.get("status") == case["expect_status"]
            and (case["expect_tool"] is None
                 or any(ev.get("tool") == case["expect_tool"]
                        for ev in body.get("evidence", []))))
        out["cases"].append(row)

    out["total_usage"] = total_usage
    out["paid_model_calls"] = sum(1 for c in out["cases"]
                                  if (c.get("model_turns") or 0) > 0)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(json.dumps({"backend": out["backend"],
                      "paid_model_calls": out["paid_model_calls"],
                      "total_usage": total_usage,
                      "cases": [{"id": c["id"], "status": c.get("status"),
                                 "answer_mode": c.get("answer_mode"),
                                 "analysis_status": c.get("analysis_status"),
                                 "fallback": c.get("analysis_fallback_reason"),
                                 "claim_ids": c.get("claim_ids"),
                                 "elapsed_ms": c.get("elapsed_ms"),
                                 "expectation_met": c.get("expectation_met"),
                                 "traceable": (c.get("evidence_consistency") or {}).get(
                                     "all_numbers_traceable")}
                                for c in out["cases"]]},
                     ensure_ascii=False, indent=2))
    print("written:", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
