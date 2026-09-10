"""Actual-hour research rounds with immutable in-round personnel membership.

These pure helpers record session facts.  They do not keep a process alive,
schedule work, approve investments or authorize personnel decisions.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from .investment_operations import utc_time


def _stamp(value):
    return utc_time(value).isoformat().replace("+00:00", "Z")


def collect_strategy_roster(organization):
    roster = []
    for company_id, company in organization["companies"].items():
        for team_id, team in company["teams"].items():
            for employee in team["employees"]:
                if employee["status"] != "retired":
                    roster.append({key: employee.get(key) for key in
                                   ("id", "name", "display_name", "genome", "status", "born", "parent")}
                                  | {"company": company_id, "team": team_id})
    ids = [person["id"] for person in roster]
    if len(ids) != len(set(ids)):
        raise ValueError("Employee IDs must be unique")
    return sorted(roster, key=lambda person: person["id"])


def start_round(organization, round_id, actual_now, session_deadline, *, existing_rounds=()):
    now, deadline = utc_time(actual_now), utc_time(session_deadline)
    if any(record.get("status") == "active" for record in existing_rounds):
        raise ValueError("An active actual-hour round already exists")
    if any(record.get("id") == round_id for record in existing_rounds):
        raise ValueError("Round ID already recorded")
    end = now + timedelta(minutes=60)
    fits = end <= deadline
    roster = collect_strategy_roster(organization)
    return {
        "id": round_id, "status": "active" if fits else "deferred",
        "requested_at": _stamp(actual_now),
        "actual_started_at": _stamp(actual_now) if fits else None,
        "scheduled_end_at": end.isoformat().replace("+00:00", "Z") if fits else None,
        "actual_ended_at": None, "minimum_actual_minutes": 60,
        "session_deadline": _stamp(session_deadline), "clock": "actual_utc_wall_clock",
        "activity_mode": "active_session_rule_research_no_background_worker",
        "roster_locked": True, "strategy_employee_count": len(roster), "strategy_employees": roster,
        "in_round_personnel_changes_allowed": False, "risk_pause_or_capital_reduction_is_departure": False,
        "experiments": [], "results_review_evidence": None, "personnel_decisions": [],
        "deferral_reason": None if fits else "insufficient_actual_time_before_session_deadline",
    }


def validate_roster_lock(record, organization):
    """Detect hires, departures or identity/genome changes; allow risk pauses."""
    expected = {person["id"]: person for person in record["strategy_employees"]}
    current = {person["id"]: person for person in collect_strategy_roster(organization)}
    reasons = []
    if set(current) - set(expected):
        reasons.append("new_staff_inside_locked_round")
    if set(expected) - set(current):
        reasons.append("departure_inside_locked_round")
    for person_id in sorted(set(expected) & set(current)):
        if any(current[person_id].get(key) != expected[person_id].get(key)
               for key in ("name", "genome", "team", "company")):
            reasons.append(f"frozen_identity_or_strategy_changed:{person_id}")
    return {"valid": not reasons, "reasons": reasons, "risk_pause_is_departure": False}


def register_experiment(record, experiment, actual_now):
    """Record a new protocol before its computation, within the active hour."""
    now = utc_time(actual_now)
    if record["status"] != "active":
        raise ValueError("Experiments require an active round")
    if not utc_time(record["actual_started_at"]) <= now < utc_time(record["scheduled_end_at"]):
        raise ValueError("Registration is outside the actual round window")
    if not experiment.get("id") or not experiment.get("protocol_reference") or not experiment.get("protocol_sha256"):
        raise ValueError("Experiment needs an ID and a versioned protocol reference/hash")
    if experiment.get("status", "planned") != "planned":
        raise ValueError("Existing completed experiments cannot be registered as new round experiments")
    if any(item["id"] == experiment["id"] for item in record["experiments"]):
        raise ValueError("Experiment ID already registered")
    result = deepcopy(record)
    result["experiments"].append(deepcopy(experiment) | {"status": "planned", "registered_at": _stamp(actual_now)})
    return result


def close_round(record, actual_now):
    """Close only after the full actual hour; closure does not imply review."""
    now = utc_time(actual_now)
    if record["status"] != "active":
        raise ValueError("Only an active round can be closed")
    if now < utc_time(record["scheduled_end_at"]):
        raise ValueError("The full actual 60-minute round has not elapsed")
    if now - utc_time(record["actual_started_at"]) < timedelta(minutes=60):
        raise ValueError("Round duration is below the minimum")
    result = deepcopy(record)
    result.update(status="ended_awaiting_review", actual_ended_at=_stamp(actual_now))
    return result


def complete_results_review(record, evidence, actual_now):
    now = utc_time(actual_now)
    if record["status"] != "ended_awaiting_review":
        raise ValueError("A full ended round is required before results review")
    if (not evidence.get("reference") or not evidence.get("observed_at")
            or not utc_time(record["actual_ended_at"]) <= utc_time(evidence["observed_at"]) <= now):
        raise ValueError("Review evidence must be observed after actual closure and by the review time")
    result = deepcopy(record)
    result.update(status="completed", results_review_evidence=deepcopy(evidence))
    return result


def personnel_timing_gate(record, actual_now):
    """Eligibility for later authorized review, never a personnel approval."""
    now = utc_time(actual_now)
    evidence = record.get("results_review_evidence") or {}
    ended = record.get("actual_ended_at")
    started = record.get("actual_started_at")
    eligible = bool(
        record.get("status") == "completed" and ended and started
        and utc_time(ended) - utc_time(started) >= timedelta(minutes=60)
        and evidence.get("reference") and evidence.get("observed_at")
        and utc_time(ended) <= utc_time(evidence["observed_at"]) <= now
    )
    return {
        "eligible_for_authorized_review": eligible,
        "approved": False,
        "reason": "Round reviewed; existing personnel authority must decide" if eligible
                  else "Roster remains locked until a full actual round ends and its results are reviewed",
    }
