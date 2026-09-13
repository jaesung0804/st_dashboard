"""Behavior tests for routing authority, dependencies and the two clocks."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from ai_stock_assistant.investment_operations import INVESTMENT_GATES, review_queue
from ai_stock_assistant.investment_strategy_briefs import strategy_proposal


OBSERVED = "2026-09-10T08:00:00Z"
AS_OF = "2026-09-10T08:30:00Z"


def evidence(at=OBSERVED):
    return {"reference": "synthetic-test-observation", "observed_at": at}


def policy():
    return {
        "session_deadline": "2026-09-10T10:00:00Z", "default_wip_limit": 1,
        "approval_route_after_minutes": 15,
        "actors": {
            "worker": {"work_kinds": ["implementation", "investment"], "approval_scopes": []},
            "backup": {"work_kinds": ["implementation"], "approval_scopes": []},
            "lead": {"approval_scopes": ["investment", "personnel"]},
            "cio": {"approval_scopes": ["investment", "personnel"]},
            "customer": {"approval_scopes": ["publication"]},
            "OPS-LEAD": {"approval_scopes": ["investment", "publication", "personnel"]},
        },
        "standing_authorizations": {"publish": {"scope": "publication", "principal": "customer", "state": "granted", "evidence": evidence()}},
    }


def task(task_id="a", **extra):
    return {
        "id": task_id, "title": "Synthetic test task", "kind": "implementation",
        "status": "ready", "owner": "worker", "backup_owner": "backup",
        "created_at": OBSERVED, "observed_at": OBSERVED, "dependencies": [],
        **extra,
    }


def proposal(**extra):
    return task(
        kind="investment", status="awaiting_approval",
        strategy_brief={key: "Recorded strategy detail" for key in ("thesis", "entry", "exit", "horizon", "risk", "evidence_asof")},
        gates={name: {"state": "passed", "evidence": evidence()} for name in INVESTMENT_GATES},
        approval={"required": True, "scope": "investment", "reviewer": "lead", "alternate_reviewers": ["OPS-LEAD", "cio"], "requested_at": OBSERVED},
        **extra,
    )


def review(tasks, as_of=AS_OF, **extra):
    return review_queue({"tasks": tasks, "observed_at": OBSERVED, **extra}, policy(), as_of)


def by_id(result):
    return {row["id"]: row for row in result["tasks"]}


class OperationsTests(unittest.TestCase):
    def test_actual_strategy_proposal_routes_with_evidence_date_and_blocks_without_it(self):
        config_path = Path(__file__).resolve().parents[1] / "data/reference/employee_supervision_policy.json"
        generated = strategy_proposal("pulse_day", "efficient", json.loads(config_path.read_text(encoding="utf-8")), "2024-01-03")
        request = proposal()
        request["strategy_brief"] = generated["strategy_brief"]
        routed = review([request])
        self.assertEqual(routed["tasks"][0]["reviewed_status"], "awaiting_approval")
        self.assertEqual(routed["tasks"][0]["next_owner"], "cio")
        self.assertEqual(routed["tasks"][0]["blocked_reasons"], [])
        self.assertEqual(routed["approvals_created"], [])
        request["strategy_brief"].pop("evidence_asof")
        blocked = review([request])["tasks"][0]
        self.assertEqual(blocked["reviewed_status"], "blocked")
        self.assertIn("strategy_summary_missing:evidence_asof", blocked["blocked_reasons"])

    def test_delayed_approval_routes_only_to_existing_authority(self):
        request = proposal()
        original = deepcopy(request)
        result = review([request])
        row = result["tasks"][0]
        self.assertEqual(row["next_owner"], "cio")
        self.assertEqual(row["approval_wait_minutes"], 30)
        self.assertEqual(row["reviewed_status"], "awaiting_approval")
        self.assertEqual(result["approvals_created"], [])
        self.assertEqual(result["actions_executed"], [])
        self.assertEqual(request, original)
        request["approval"]["record"] = {"state": "approved", "reviewer": "OPS-LEAD", "evidence": evidence()}
        self.assertIn("invalid_approval_record", review([request])["tasks"][0]["blocked_reasons"])

    def test_brief_and_hard_gates_cannot_be_omitted_or_waived(self):
        request = proposal()
        request["approval"]["record"] = {"state": "approved", "reviewer": "cio", "evidence": evidence()}
        request["strategy_brief"].pop("exit")
        self.assertEqual(review([request])["tasks"][0]["reviewed_status"], "blocked")
        request = proposal()
        for gate in sorted(INVESTMENT_GATES):
            bad = deepcopy(request)
            bad["gates"][gate] = {"state": "waived", "evidence": evidence()}
            self.assertIn(f"gate_not_satisfied:{gate}", review([bad])["tasks"][0]["blocked_reasons"])
        request["approval"] = {"required": False}
        self.assertIn("approval_scope_missing", review([request])["tasks"][0]["blocked_reasons"])

    def test_existing_publication_permission_does_not_skip_verification(self):
        release = task(kind="release", approval={"required": True, "scope": "publication", "standing_authorization": "publish"}, required_gates=["verification"])
        row = review([release])["tasks"][0]
        self.assertEqual(row["approval_disposition"], "existing_authorization")
        self.assertEqual(row["reviewed_status"], "blocked")
        release["gates"] = {"verification": {"state": "passed", "evidence": evidence()}}
        row = review([release])["tasks"][0]
        self.assertEqual(row["reviewed_status"], "ready")
        self.assertEqual(row["next_owner"], "worker")

    def test_dependencies_cycles_and_unverified_completion(self):
        items = [task("a", dependencies=["b"]), task("b", dependencies=["a"]), task("c", dependencies=["a"]), task("d", dependencies=["missing"]), task("e", status="completed")]
        result = review(items)
        rows = by_id(result)
        self.assertEqual(result["dependency_cycles"], [["a", "b"]])
        self.assertEqual(rows["c"]["blocking_roots"], ["a", "b"])
        self.assertIn("unknown_dependency:missing", rows["d"]["blocked_reasons"])
        self.assertEqual(rows["e"]["reviewed_status"], "blocked")
        completed = task("done", status="completed", completion_evidence=evidence())
        self.assertEqual(by_id(review([completed, task("after", dependencies=["done"])]))["after"]["reviewed_status"], "ready")
        unexplained = task(status="blocked")
        self.assertEqual(review([unexplained])["tasks"][0]["reviewed_status"], "blocked")

    def test_actual_clock_never_uses_backtest_dates_or_invented_wait(self):
        request = proposal()
        request["simulation_clock"] = {"start": "2007-01-03", "end": "2037-01-03"}
        self.assertEqual(review([request])["tasks"][0]["approval_wait_minutes"], 30)
        request["approval"].pop("requested_at")
        self.assertIsNone(review([request])["tasks"][0]["approval_wait_minutes"])
        request["observed_at"] = "2026-09-10T08:31:00Z"
        self.assertIn("observation_after_review_time", review([request])["tasks"][0]["blocked_reasons"])
        with self.assertRaises(ValueError):
            review([task()], as_of="2026-09-10")
        future_completion = task(status="completed", completion_evidence=evidence("2026-09-10T09:00:00Z"))
        self.assertEqual(review([future_completion])["tasks"][0]["reviewed_status"], "blocked")

    def test_wip_duplicate_suggestions_are_non_mutating_and_idempotent(self):
        items = [task("a", status="in_progress", dedup_key="same"), task("b", status="in_progress", dedup_key="same")]
        original = deepcopy(items)
        first = review(items)
        second = review(items)
        self.assertEqual(first, second)
        self.assertEqual(items, original)
        self.assertTrue(first["wip"][0]["exceeded"])
        self.assertEqual(first["duplicate_merge_suggestions"], [["a", "b"]])
        self.assertEqual(sum(row["next_owner"] == "backup" for row in first["tasks"]), 1)

    def test_full_actual_hour_roster_lock_and_end_review(self):
        new_round = task(round_control={"request": "start_new_round"})
        self.assertEqual(review([new_round], as_of="2026-09-10T09:00:00Z")["tasks"][0]["reviewed_status"], "ready")
        result = review([new_round], as_of="2026-09-10T09:00:01Z")
        self.assertFalse(result["round_window"]["can_start_full_round"])
        self.assertIn("insufficient_time_for_full_60_minute_round", result["tasks"][0]["blocked_reasons"])
        person = task(kind="personnel", approval={"required": True, "scope": "personnel", "reviewer": "cio"})
        active = {"status": "in_progress", "actual_started_at": OBSERVED, "actual_ended_at": None}
        self.assertIn("active_round_already_running", review([new_round], round_context=active)["tasks"][0]["blocked_reasons"])
        self.assertIn("roster_locked_until_full_round_boundary", review([person], round_context=active)["tasks"][0]["blocked_reasons"])
        short = {"status": "completed", "actual_started_at": OBSERVED, "actual_ended_at": "2026-09-10T08:10:00Z", "results_review_evidence": evidence()}
        self.assertEqual(review([person], round_context=short)["tasks"][0]["reviewed_status"], "blocked")
        full = {"status": "completed", "actual_started_at": OBSERVED, "actual_ended_at": "2026-09-10T09:00:00Z", "results_review_evidence": evidence("2026-09-10T09:00:00Z")}
        row = review([person], as_of="2026-09-10T09:01:00Z", round_context=full)["tasks"][0]
        self.assertEqual(row["reviewed_status"], "awaiting_approval")
        self.assertEqual(row["next_owner"], "cio")


if __name__ == "__main__":
    unittest.main()
