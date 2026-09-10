"""Deterministic work routing for the simulated investment organization.

This module returns recommendations.  It does not approve an investment, edit an
input queue, send messages, schedule a worker, or execute a release.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from .investment_strategy_briefs import BRIEF_FIELDS


STATUSES = {"ready", "in_progress", "blocked", "awaiting_approval", "completed"}
INVESTMENT_GATES = {"risk", "information_time", "fees", "paid_usage", "verification"}
OPERATIONS_IDS = {"OPS-LEAD", "OPS-FLOW", "OPS-RELEASE"}


def utc_time(value: str) -> datetime:
    """Require an explicit UTC business timestamp, never a simulation date."""
    if not isinstance(value, str):
        raise ValueError("Business timestamps must be explicit UTC strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"Business timestamp must have UTC offset: {value}")
    return parsed.astimezone(timezone.utc)


def _dated_evidence(evidence, as_of):
    """A source claim must identify its basis and actual observation time."""
    return bool(
        isinstance(evidence, dict)
        and evidence.get("reference")
        and evidence.get("observed_at")
        and utc_time(evidence["observed_at"]) <= as_of
    )


def _cycles(tasks):
    """Return strongly connected dependency components, including self loops."""
    index = 0
    indices, low, stack, active, found = {}, {}, [], set(), []

    def visit(task_id):
        nonlocal index
        indices[task_id] = low[task_id] = index
        index += 1
        stack.append(task_id)
        active.add(task_id)
        for dependency in tasks[task_id].get("dependencies", []):
            if dependency not in tasks:
                continue
            if dependency not in indices:
                visit(dependency)
                low[task_id] = min(low[task_id], low[dependency])
            elif dependency in active:
                low[task_id] = min(low[task_id], indices[dependency])
        if low[task_id] == indices[task_id]:
            component = []
            while True:
                member = stack.pop()
                active.remove(member)
                component.append(member)
                if member == task_id:
                    break
            if len(component) > 1 or task_id in tasks[task_id].get("dependencies", []):
                found.append(sorted(component))

    for task_id in sorted(tasks):
        if task_id not in indices:
            visit(task_id)
    return sorted(found)


def review_queue(queue: dict, policy: dict, as_of: str) -> dict:
    """Review one observation snapshot without changing it.

    Identical inputs and ``as_of`` produce identical output.  Completion records
    are source attestations; this function does not independently hash artifacts.
    Historical simulation dates are metadata only and cannot age work items.
    """
    now = utc_time(as_of)
    deadline = utc_time(policy["session_deadline"]) if policy.get("session_deadline") else None
    round_minutes = max(60, policy.get("round_policy", {}).get("minimum_actual_minutes", 60))
    round_context = queue.get("round_context", {})
    raw = deepcopy(queue["tasks"])
    tasks = {task["id"]: task for task in raw}
    if len(tasks) != len(raw):
        raise ValueError("Task IDs must be unique; duplicate work uses dedup_key")
    actors = policy["actors"]
    for task in raw:
        if task.get("status") not in STATUSES:
            raise ValueError(f"Unknown status for {task['id']}")
        for field in ("created_at", "observed_at", "due_at"):
            if task.get(field):
                utc_time(task[field])
        if not task.get("created_at") or not task.get("observed_at"):
            raise ValueError(f"Missing actual business timestamps: {task['id']}")
        if utc_time(task["observed_at"]) < utc_time(task["created_at"]):
            raise ValueError(f"Observation predates queue record: {task['id']}")

    cycles = _cycles(tasks)
    cycle_of = {member: component for component in cycles for member in component}
    notes = {task_id: [] for task_id in tasks}
    routes = {}
    dispositions = {}
    approval_ages = {}

    def has_authority(actor, scope):
        # Operations staff cannot become investment/release approvers even if
        # their configurable actor record accidentally lists that permission.
        return actor not in OPERATIONS_IDS and scope in actors.get(actor, {}).get("approval_scopes", [])

    for task_id, task in tasks.items():
        reasons = notes[task_id]
        if utc_time(task["observed_at"]) > now or utc_time(task["created_at"]) > now:
            reasons.append("observation_after_review_time")
        if task.get("owner") not in actors:
            reasons.append("unknown_owner")
        if task.get("backup_owner") and task["backup_owner"] not in actors:
            reasons.append("unknown_backup_owner")
        if task_id in cycle_of:
            reasons.append("cyclic_dependency")
        for dependency in task.get("dependencies", []):
            if dependency not in tasks:
                reasons.append(f"unknown_dependency:{dependency}")
        for blocker in task.get("blockers", []):
            # A persisted unresolved issue remains open until new evidence is
            # recorded in the input queue; a deadline cannot remove it.
            if blocker.get("state", "open") != "resolved":
                reasons.append(f"open_blocker:{blocker.get('id', 'unspecified')}")
            elif not _dated_evidence(blocker.get("resolution_evidence"), now):
                reasons.append(f"unverified_blocker_resolution:{blocker.get('id', 'unspecified')}")

        approval = task.get("approval", {})
        scope = approval.get("scope")
        investment = task.get("kind") == "investment" or scope in {"investment", "risk_exception"}
        personnel = task.get("kind") == "personnel"
        control = task.get("round_control", {})
        if control.get("request") == "start_new_round":
            if round_context.get("status") in {"active", "in_progress"}:
                reasons.append("active_round_already_running")
            if deadline is None:
                reasons.append("session_deadline_unknown")
            elif now + timedelta(minutes=round_minutes) > deadline:
                reasons.append("insufficient_time_for_full_60_minute_round")
        if personnel:
            if scope != "personnel":
                reasons.append("personnel_approval_scope_missing")
            ended_at = round_context.get("actual_ended_at")
            started_at = round_context.get("actual_started_at")
            if (round_context.get("status") != "completed" or not ended_at or not started_at
                    or utc_time(ended_at) > now
                    or utc_time(ended_at) - utc_time(started_at) < timedelta(minutes=round_minutes)):
                reasons.append("roster_locked_until_full_round_boundary")
            round_review = round_context.get("results_review_evidence")
            if (not _dated_evidence(round_review, now) or not ended_at
                    or utc_time(round_review["observed_at"]) < utc_time(ended_at)):
                reasons.append("round_results_review_missing")
        required_gates = set(task.get("required_gates", []))
        if investment:
            required_gates |= INVESTMENT_GATES
        for gate_name in sorted(required_gates):
            gate = task.get("gates", {}).get(gate_name, {})
            if gate.get("state") != "passed" or not _dated_evidence(gate.get("evidence"), now):
                reasons.append(f"gate_not_satisfied:{gate_name}")
        if investment or approval.get("strategy_summary_required"):
            brief = task.get("strategy_brief", {})
            missing = [field for field in BRIEF_FIELDS if not isinstance(brief.get(field), str) or not brief[field].strip()]
            if missing:
                reasons.append("strategy_summary_missing:" + ",".join(missing))

        disposition = "not_required"
        if investment or personnel or approval.get("required"):
            disposition = "awaiting_authorized_review"
            if not scope:
                reasons.append("approval_scope_missing")
            record = approval.get("record")
            grant_id = approval.get("standing_authorization")
            grant = policy.get("standing_authorizations", {}).get(grant_id, {})
            # Reuse the user's existing, explicitly scoped permission.  The
            # output never generates a new approval or removes a required gate.
            if grant_id:
                if (grant.get("scope") == scope and grant.get("state") == "granted"
                        and has_authority(grant.get("principal"), scope)
                        and _dated_evidence(grant.get("evidence"), now)):
                    disposition = "existing_authorization"
                else:
                    reasons.append("invalid_standing_authorization")
            if record:
                if (record.get("state") == "approved" and has_authority(record.get("reviewer"), scope)
                        and _dated_evidence(record.get("evidence"), now)):
                    disposition = "recorded_authorized_approval"
                else:
                    reasons.append("invalid_approval_record")
            candidates = [approval.get("reviewer"), *approval.get("alternate_reviewers", [])]
            candidates = list(dict.fromkeys(actor for actor in candidates if has_authority(actor, scope)))
            requested = approval.get("requested_at")
            age = None
            if requested:
                request_time = utc_time(requested)
                if request_time > now:
                    reasons.append("approval_request_after_review_time")
                else:
                    age = round((now - request_time).total_seconds() / 60, 3)
            approval_ages[task_id] = age
            if disposition == "awaiting_authorized_review":
                if not candidates:
                    reasons.append("no_authorized_reviewer")
                else:
                    target = candidates[0]
                    if age is not None and age >= policy.get("approval_route_after_minutes", 15) and len(candidates) > 1:
                        target = candidates[1]
                    routes[task_id] = target
        dispositions[task_id] = disposition
        if task["status"] == "completed" and not _dated_evidence(task.get("completion_evidence"), now):
            reasons.append("completion_evidence_missing_or_future")

    effective = {}

    def status(task_id):
        if task_id in effective:
            return effective[task_id]
        task = tasks[task_id]
        # Cycle members already have an explicit blocker; avoid recursive loops.
        if task_id in cycle_of or notes[task_id]:
            effective[task_id] = "blocked"
        else:
            waiting = [dep for dep in task.get("dependencies", []) if status(dep) != "completed"]
            if waiting:
                notes[task_id].extend(f"dependency_not_completed:{dep}" for dep in waiting)
                effective[task_id] = "blocked"
            elif dispositions[task_id] == "awaiting_authorized_review":
                effective[task_id] = "awaiting_approval"
            elif task["status"] == "blocked" and not _dated_evidence(task.get("resume_evidence"), now):
                notes[task_id].append("blocked_state_requires_resolution_evidence")
                effective[task_id] = "blocked"
            elif task["status"] in {"blocked", "awaiting_approval"}:
                # A recommendation to resume is not a fabricated start or finish.
                effective[task_id] = "ready"
            else:
                effective[task_id] = task["status"]
        return effective[task_id]

    for task_id in sorted(tasks):
        status(task_id)

    def roots(task_id, visited=None):
        visited = set() if visited is None else set(visited)
        if task_id in visited:
            return set(cycle_of.get(task_id, [task_id]))
        visited.add(task_id)
        task = tasks.get(task_id)
        if not task:
            return {task_id}
        waiting = [dep for dep in task.get("dependencies", []) if effective.get(dep) != "completed"]
        found = set().union(*(roots(dep, visited) for dep in waiting)) if waiting else set()
        own_notes = [reason for reason in notes[task_id] if not reason.startswith("dependency_not_completed:")]
        if own_notes or effective[task_id] == "awaiting_approval" or not waiting:
            found.add(task_id)
        return found

    raw_wip = Counter(task.get("owner") for task in raw if task["status"] == "in_progress")
    limit = policy.get("default_wip_limit", 2)
    wip = [{"owner": owner, "observed_in_progress": count,
            "limit": policy.get("wip_limits", {}).get(owner, limit),
            "exceeded": count > policy.get("wip_limits", {}).get(owner, limit)}
           for owner, count in sorted(raw_wip.items())]
    overloaded = {row["owner"] for row in wip if row["exceeded"]}
    proposed_wip = Counter(raw_wip)
    rows = []
    for task_id, task in sorted(tasks.items(), key=lambda item: (item[1].get("priority", 3), item[0])):
        next_owner = task.get("owner")
        action = task.get("next_action", "담당자가 다음 작업과 완료 근거를 기록")
        if any(reason.startswith("strategy_summary_missing") for reason in notes[task_id]):
            action = "요청자가 투자 논리·진입·청산·주기·위험 한도·자료 기준시점을 보완한 뒤 검토 요청"
        elif effective[task_id] == "blocked":
            blockers = sorted(roots(task_id))
            known = [item for item in blockers if item in tasks]
            if known:
                selected = min(known, key=lambda item: (tasks[item].get("priority", 3), item))
                next_owner = tasks[selected].get("owner")
            action = "차단 근본 작업의 담당자에게 근거 또는 의존관계 수정 요청: " + ", ".join(blockers)
        elif effective[task_id] == "awaiting_approval":
            next_owner = routes.get(task_id)
            action = "기존 승인권한을 가진 검토자에게 전달 추천; 검토 결과와 근거를 받을 때까지 실행 대기"
        elif effective[task_id] == "completed":
            action = "기록된 완료 근거 보존; 후속 의존 작업 진행 가능 여부 검토"
        elif task.get("owner") in overloaded:
            backup = task.get("backup_owner")
            capabilities = actors.get(backup, {}).get("work_kinds", [])
            if (backup and task.get("kind") in capabilities
                    and proposed_wip[backup] < policy.get("wip_limits", {}).get(backup, limit)):
                next_owner = backup
                proposed_wip[backup] += 1
                action = "동일 작업 권한과 여유가 있는 대체 담당자에게 인계 추천; 승인권한은 이동하지 않음"
            else:
                action = "OPS-FLOW가 기존 진행 작업부터 정리하고 신규 착수 보류를 권고"
        due = utc_time(task["due_at"]) if task.get("due_at") else None
        rows.append({
            "id": task_id, "title": task["title"], "kind": task.get("kind"),
            "observed_status": task["status"], "reviewed_status": effective[task_id],
            "owner": task.get("owner"), "backup_owner": task.get("backup_owner"),
            "next_owner": next_owner, "priority": task.get("priority", 3),
            "dependencies": task.get("dependencies", []), "blocking_roots": sorted(roots(task_id)) if effective[task_id] == "blocked" else [],
            "due_at": task.get("due_at"), "overdue": bool(due and now > due and effective[task_id] != "completed"),
            "observed_at": task["observed_at"], "approval_disposition": dispositions[task_id],
            "approval_wait_minutes": approval_ages.get(task_id),
            "approval_scope": task.get("approval", {}).get("scope"),
            "blocked_reasons": notes[task_id], "next_action": action,
            "simulation_clock": task.get("simulation_clock"),
        })
    rows.sort(key=lambda row: (row["reviewed_status"] == "completed", row["priority"], row["due_at"] or "9999", row["id"]))
    groups = defaultdict(list)
    for task in raw:
        if task.get("dedup_key") and task["status"] != "completed":
            groups[(task["dedup_key"], task.get("kind"), task.get("approval", {}).get("scope"))].append(task["id"])
    duplicates = [sorted(ids) for ids in groups.values() if len(ids) > 1]
    return {
        "version": "investment-operations-review-v1", "reviewed_as_of": now.isoformat().replace("+00:00", "Z"),
        "source_observed_at": queue.get("observed_at"), "mode": "local_rule_recommendations_only",
        "actions_executed": [], "approvals_created": [],
        "status_counts": dict(sorted(Counter(row["reviewed_status"] for row in rows).items())),
        "tasks": rows, "dependency_cycles": cycles, "wip": wip,
        "round_window": {
            "minimum_actual_minutes": round_minutes,
            "session_deadline": policy.get("session_deadline"),
            "can_start_full_round": bool(deadline and now + timedelta(minutes=round_minutes) <= deadline
                                         and round_context.get("status") not in {"active", "in_progress"}),
            "remaining_actual_minutes": round(max(0, (deadline - now).total_seconds() / 60), 3) if deadline else None,
            "roster_rule": "No hiring or departures inside an actual 60-minute round; review personnel at its completed boundary.",
            "risk_pause_is_departure": False,
            "active_round_source": round_context.get("source"),
        },
        "duplicate_merge_suggestions": sorted(duplicates),
        "escalations": [{"task": row["id"], "to": "OPS-LEAD", "reason": "deadline_passed_without_completion"}
                        for row in rows if row["overdue"]],
        "limitations": [
            "가상 규칙 직원이 관찰된 큐를 검토한 결과이며 실제 메시지·인계·승인을 실행하지 않음",
            "완료 및 게이트 근거는 입력의 출처 있는 확인 기록이며 별도 파일 검증기를 대체하지 않음",
            "승인 대기 시작 시각이 기록되지 않으면 대기 시간을 추정하지 않음",
            "과거 simulation_clock은 업무 대기 시간과 기한 판정에 사용하지 않음",
        ],
    }
