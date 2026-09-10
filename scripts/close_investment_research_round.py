#!/usr/bin/env python3
"""Record a completed real hour and the authorized no-turnover review.

Uses the real UTC clock, verifies registered result digests, and never places
orders, hires staff, writes external apps, or starts another round.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ai_stock_assistant.investment_rounds import (  # noqa: E402
    close_round, complete_results_review, personnel_timing_gate,
    validate_roster_lock,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path):
    return json.loads((ROOT / path).read_text())


def write(path, value):
    target = ROOT / path
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-id", default="r03")
    args = parser.parse_args()
    ledger = read("data/reference/investment_rounds.json")
    index = next(i for i, record in enumerate(ledger["rounds"])
                 if record["id"] == args.round_id)
    record = ledger["rounds"][index]
    if record["status"] == "completed":
        print(json.dumps({"round": args.round_id, "status": "already_completed",
                          "actual_ended_at": record["actual_ended_at"]}))
        return
    # Reject early invocation before reading or writing any closure evidence.
    closed = close_round(record, utc_now())
    organization = read("data/reference/investment_organization.json")
    lock = validate_roster_lock(record, organization)
    if not lock["valid"]:
        raise ValueError(f"Locked roster changed: {lock['reasons']}")
    experiments = record["experiments"]
    if not experiments or any(item["status"] != "completed" for item in experiments):
        raise ValueError("All registered experiments must be completed before this review")
    digests = {}
    for item in experiments:
        reference = item["result_reference"]
        digest = hashlib.sha256((ROOT / reference).read_bytes()).hexdigest()
        if digest != item["result_sha256"]:
            raise ValueError(f"Registered result changed: {item['id']}")
        digests[reference] = digest
    validation_reference = "docs/validation/investment_r03_validation.json"
    validation = read(validation_reference)
    manifest_reference = "docs/replays/2026-09-10-r03-fixed-roster/manifest.json"
    if (validation["result"] != "passed"
            or validation["source_manifest_sha256"] != hashlib.sha256(
                (ROOT / manifest_reference).read_bytes()).hexdigest()):
        raise ValueError("Result audit or audited manifest changed")
    reviewed_at = utc_now()
    closure_reference = f"docs/replays/2026-09-10-r03-analysis/{args.round_id}-closure.json"
    evidence = {
        "reference": closure_reference, "observed_at": reviewed_at,
        "description": "실제 60분 종료 후 등록된 결과 36개 해시·감사 근거·명단을 재대조하고 결과와 인사 유지안을 검토",
    }
    completed = complete_results_review(closed, evidence, reviewed_at)
    timing = personnel_timing_gate(completed, reviewed_at)
    if not timing["eligible_for_authorized_review"]:
        raise ValueError("Personnel review timing gate is not satisfied")
    decision = {
        "id": f"{args.round_id}-retain-roster", "decided_at": reviewed_at,
        "decision": "retain_roster", "retained_strategy_employees": len(record["strategy_employees"]),
        "hires": [], "departures": [], "promotions": [],
        "reviewer": "ROOT-INTEGRATION",
        "authority_context": "사용자가 위임한 시뮬레이션 고객·감사 역할의 현원 유지 판단; 운영조정팀의 자동 승인 아님",
        "reason": "한 개의 재사용 과거 표본과 상관된 민감도 결과만으로 추가 해고·채용 또는 승진을 정당화하지 않음",
        "next_research_reference": "data/reference/investment_next_round_proposal.json",
        "capital_changes_executed": False, "timing_gate": timing,
    }
    completed["personnel_decisions"].append(decision)
    completed["next_round_started"] = False
    completed["next_round_deferral_reason"] = "오늘 19시 마감까지 새 60분 회차를 완료할 시간이 없음"
    closure = {
        "round_id": args.round_id, "actual_started_at": record["actual_started_at"],
        "actual_ended_at": closed["actual_ended_at"], "reviewed_at": reviewed_at,
        "status": "completed", "registered_experiments_completed": len(experiments),
        "roster_lock": lock, "result_sha256": digests,
        "validation_reference": validation_reference,
        "validation_sha256": hashlib.sha256((ROOT / validation_reference).read_bytes()).hexdigest(),
        "personnel_decision": decision,
        "research_findings": "컴파운드 회사 70/30 조합의 같은조건 개선은 후속 연구 후보. 시장 초과 성과 또는 기업 논리 수익성 입증은 아님.",
    }
    queue = read("data/reference/investment_operations_queue.json")
    queue["observed_at"] = reviewed_at
    queue["round_context"] = {key: completed.get(key) for key in
        ("id", "status", "actual_started_at", "scheduled_end_at", "actual_ended_at", "results_review_evidence")}
    queue["round_context"]["source"] = f"data/reference/investment_rounds.json:{args.round_id}"
    for task in queue["tasks"]:
        if task["id"] == "ROUND-R03-REVIEW":
            task.update(status="completed", observed_at=reviewed_at,
                        next_action="35명 유지·추가 해고 0·채용 0. 다음 후보는 별도 회차에서 검증",
                        completion_evidence=evidence)
    organization["latest_actual_round_review"] = {
        "round_id": args.round_id, "reviewed_at": reviewed_at,
        "decision": "retain_roster", "evidence": closure_reference,
        "historical_employee_statuses_rewritten": False,
    }
    ledger["rounds"][index] = completed
    ledger["actual_updated_at"] = reviewed_at
    write(closure_reference, closure)
    write("data/reference/investment_organization.json", organization)
    write("data/reference/investment_operations_queue.json", queue)
    write("data/reference/investment_rounds.json", ledger)
    print(json.dumps({"round": args.round_id, "status": "completed",
                      "actual_ended_at": closed["actual_ended_at"], "reviewed_at": reviewed_at,
                      "experiments": len(experiments), "retained": len(record["strategy_employees"]),
                      "hires": 0, "departures": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
