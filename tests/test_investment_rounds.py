"""Actual-hour and roster tests, using deliberately synthetic timestamps."""
from copy import deepcopy
import unittest

from ai_stock_assistant.investment_rounds import (
    close_round, complete_results_review, personnel_timing_gate,
    register_experiment, start_round, validate_roster_lock,
)


START = "2026-09-10T08:58:20Z"
END = "2026-09-10T09:58:20Z"
DEADLINE = "2026-09-10T10:00:00Z"


def organization():
    return {"companies": {"c": {"teams": {"t": {"employees": [
        {"id": "e1", "name": "가상 직원 A", "genome": "efficient", "status": "funded"},
        {"id": "e2", "name": "가상 직원 B", "genome": "guarded", "status": "retired"},
    ]}}}}}


class RoundTests(unittest.TestCase):
    def test_actual_hour_fit_and_no_parallel_or_duplicate_round(self):
        org = organization()
        record = start_round(org, "r", START, DEADLINE)
        self.assertEqual(record["scheduled_end_at"], END)
        self.assertEqual(record["strategy_employee_count"], 1)
        self.assertEqual(record["experiments"], [])
        late = start_round(org, "late", "2026-09-10T09:00:01Z", DEADLINE)
        self.assertEqual(late["status"], "deferred")
        self.assertIsNone(late["actual_started_at"])
        with self.assertRaises(ValueError):
            start_round(org, "another", START, DEADLINE, existing_rounds=[record])

    def test_roster_lock_preserves_retirement_and_allows_risk_pause(self):
        org = organization()
        record = start_round(org, "r", START, DEADLINE)
        paused = deepcopy(org)
        paused["companies"]["c"]["teams"]["t"]["employees"][0]["status"] = "paused"
        self.assertTrue(validate_roster_lock(record, paused)["valid"])
        departed = deepcopy(org)
        departed["companies"]["c"]["teams"]["t"]["employees"][0]["status"] = "retired"
        self.assertFalse(validate_roster_lock(record, departed)["valid"])
        rehired = deepcopy(org)
        rehired["companies"]["c"]["teams"]["t"]["employees"][1]["status"] = "shadow"
        self.assertFalse(validate_roster_lock(record, rehired)["valid"])
        changed = deepcopy(org)
        changed["companies"]["c"]["teams"]["t"]["employees"][0]["genome"] = "original"
        self.assertFalse(validate_roster_lock(record, changed)["valid"])

    def test_predeclared_new_experiments_cannot_relabel_old_results(self):
        record = start_round(organization(), "r", START, DEADLINE)
        protocol = {"id": "new", "protocol_reference": "synthetic-protocol.json", "protocol_sha256": "a" * 64}
        registered = register_experiment(record, protocol, "2026-09-10T09:01:00Z")
        self.assertEqual(len(registered["experiments"]), 1)
        self.assertEqual(record["experiments"], [])
        with self.assertRaises(ValueError):
            register_experiment(record, protocol | {"status": "completed"}, "2026-09-10T09:01:00Z")
        with self.assertRaises(ValueError):
            register_experiment(record, protocol, END)

    def test_full_hour_and_after_end_review_only_enable_authorized_review(self):
        record = start_round(organization(), "r", START, DEADLINE)
        self.assertFalse(personnel_timing_gate(record, START)["eligible_for_authorized_review"])
        with self.assertRaises(ValueError):
            close_round(record, "2026-09-10T09:00:00Z")
        closed = close_round(record, END)
        self.assertFalse(personnel_timing_gate(closed, END)["eligible_for_authorized_review"])
        with self.assertRaises(ValueError):
            complete_results_review(closed, {"reference": "review", "observed_at": START}, END)
        reviewed = complete_results_review(closed, {"reference": "review", "observed_at": END}, END)
        gate = personnel_timing_gate(reviewed, END)
        self.assertTrue(gate["eligible_for_authorized_review"])
        self.assertFalse(gate["approved"])


if __name__ == "__main__":
    unittest.main()
