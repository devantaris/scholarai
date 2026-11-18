"""Scholar Pulse: Follow-up Engine & Escalation Ladder.

Implements:
1. FollowupResult domain model extending Intervention.
2. 48-Hour Outcome Evaluation (recovered, partial, no_response, pending_followup).
3. Escalation Ladder progression (MICRO_LESSON -> REENGAGE -> ESCALATE_MENTOR).
4. Student-level batch pending follow-up processor.
5. Aggregate recovery metrics computation for /api/metrics.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional, Union
import uuid

from app.config import (
    FOLLOWUP_WINDOW_HOURS,
    REENGAGE_FIRST_TASK_MIN,
    REENGAGE_SECOND_TASK_MIN,
)
from app.generator import generate_template_nudge
from app.models import (
    ContentConcept,
    Event,
    Intervention,
    RoadmapItem,
    Student,
    get_concept,
)


class FollowupResult(Intervention):
    """Domain model representing the evaluated outcome of an intervention.
    
    Subclasses Intervention to provide seamless polymorphism with existing stores,
    APIs, and test suites while attaching explicit evaluation metadata.
    """
    next_kind: Optional[str] = None
    is_due: bool = False


def evaluate_followup(
    intervention: Intervention,
    events: Optional[list[Event]] = None,
    sim_now: Optional[datetime] = None,
    history: Optional[list[Intervention]] = None,
    prior_interventions: Optional[list[Intervention]] = None,
    **kwargs: Any,
) -> FollowupResult:
    """Evaluate 48-hour outcome or progress escalation for an intervention.
    
    Args:
        intervention: The candidate Intervention being evaluated.
        events: Student event stream.
        sim_now: Current simulated timestamp.
        history / prior_interventions: Prior interventions for streak analysis.
        
    Returns:
        FollowupResult with updated status, outcome_ts, outcome_details, and next_kind.
    """
    now = sim_now or datetime.now(timezone.utc)
    ev_list = events if events is not None else []
    priors = history if history is not None else (prior_interventions or [])

    # 1. Handle case where intervention is already in 'partial' status (Escalation to MICRO_LESSON)
    if intervention.status == "partial":
        concept_obj = get_concept(intervention.concept_id) if intervention.concept_id else None
        msg, cta = generate_template_nudge("MICRO_LESSON", concept=concept_obj)
        res_dict = intervention.model_dump()
        res_dict.update({
            "id": f"int-{uuid.uuid4().hex[:8]}",
            "kind": "MICRO_LESSON",
            "message": msg,
            "cta": cta,
            "status": "pending_followup",
            "ts": now,
            "followup_due": now + timedelta(hours=FOLLOWUP_WINDOW_HOURS),
            "rationale": f"Escalated from partial recovery on {intervention.target_problem_id or 'problem'}; assigning focused micro-lesson.",
        })
        return FollowupResult(**res_dict, next_kind="MICRO_LESSON", is_due=True)

    # 2. Handle case where intervention is already in 'no_response' status (Escalation Ladder)
    if intervention.status == "no_response":
        other_priors = [
            p for p in priors
            if p.id != intervention.id and (not p.student_id or p.student_id == intervention.student_id)
        ]
        other_priors.sort(key=lambda x: x.ts, reverse=True)
        is_consecutive_no_resp = (
            len(other_priors) > 0 and other_priors[0].status in ["no_response", "escalated"]
        )
        if is_consecutive_no_resp:
            res_dict = intervention.model_dump()
            res_dict.update({
                "id": f"int-{uuid.uuid4().hex[:8]}",
                "kind": "ESCALATE_MENTOR",
                "status": "escalated",
                "message": None,
                "cta": None,
                "ts": now,
                "outcome_ts": now,
                "outcome_details": "Second consecutive no_response; escalated to human mentor.",
                "rationale": "Student unresponsive across multiple 48h windows; alerted mentor portal.",
            })
            return FollowupResult(**res_dict, next_kind="ESCALATE_MENTOR", is_due=True)
        else:
            student = kwargs.get("student")
            if not student and intervention.student_id:
                from app.store import store
                student = store.get_student(intervention.student_id)
            first_name = student.name.split()[0] if student and student.name else "there"
            msg = (
                f"Hi {first_name}, great to see you! "
                f"Let's get your study momentum rolling today with a quick {REENGAGE_SECOND_TASK_MIN}-minute warm-up exercise."
            )
            cta = f"Start {REENGAGE_SECOND_TASK_MIN}-Min Task"
            res_dict = intervention.model_dump()
            res_dict.update({
                "id": f"int-{uuid.uuid4().hex[:8]}",
                "kind": "REENGAGE",
                "status": "no_response",
                "message": msg,
                "cta": cta,
                "ts": now,
                "followup_due": now + timedelta(hours=FOLLOWUP_WINDOW_HOURS),
                "rationale": "First no-response; sending low-friction 5-minute re-engagement task.",
            })
            return FollowupResult(**res_dict, next_kind="REENGAGE", is_due=True)

    # 3. Handle pending_followup timing check (< 48h)
    due = intervention.followup_due or (intervention.ts + timedelta(hours=FOLLOWUP_WINDOW_HOURS))
    if now < due:
        return FollowupResult(**intervention.model_dump(), is_due=False, next_kind=None)

    # 4. Perform 48-Hour Outcome Evaluation (>= 48h)
    relevant_events = [
        e for e in ev_list
        if (not e.student_id or e.student_id == intervention.student_id)
        and e.ts >= intervention.ts
        and e.ts <= now
    ]

    target_pid = intervention.target_problem_id
    has_target_pass = any(
        e.type == "attempt_pass" and (target_pid is None or e.problem_id == target_pid)
        for e in relevant_events
    )
    session_count = sum(1 for e in relevant_events if e.type == "session")

    # A. Recovered
    if has_target_pass or session_count >= 2:
        reason = "Target problem passed" if has_target_pass else f"Completed {session_count} study sessions"
        details = f"Recovered: {reason} post-nudge."
        res_dict = intervention.model_dump()
        res_dict.update({
            "status": "recovered",
            "outcome_ts": now,
            "outcome_details": details,
        })
        return FollowupResult(**res_dict, next_kind="REPLAN_UP", is_due=True)

    # B. Partial
    elif len(relevant_events) > 0:
        details = f"Partial recovery: {session_count} session(s) completed without passing target problem."
        res_dict = intervention.model_dump()
        res_dict.update({
            "status": "partial",
            "outcome_ts": now,
            "outcome_details": details,
        })
        return FollowupResult(**res_dict, next_kind="MICRO_LESSON", is_due=True)

    # C. No Response
    else:
        other_priors = [
            p for p in priors
            if p.id != intervention.id and (not p.student_id or p.student_id == intervention.student_id)
        ]
        other_priors.sort(key=lambda x: x.ts, reverse=True)
        is_consecutive_no_resp = (
            len(other_priors) > 0 and other_priors[0].status in ["no_response", "escalated"]
        )
        if is_consecutive_no_resp:
            res_dict = intervention.model_dump()
            res_dict.update({
                "status": "escalated",
                "kind": "ESCALATE_MENTOR",
                "message": None,
                "cta": None,
                "outcome_ts": now,
                "outcome_details": "Second consecutive no_response; escalated to mentor.",
            })
            return FollowupResult(**res_dict, next_kind="ESCALATE_MENTOR", is_due=True)
        else:
            details = "No student activity recorded in 48-hour follow-up window."
            res_dict = intervention.model_dump()
            res_dict.update({
                "status": "no_response",
                "outcome_ts": now,
                "outcome_details": details,
            })
            return FollowupResult(**res_dict, next_kind="REENGAGE", is_due=True)


def determine_escalation(
    intervention: Intervention,
    prior_interventions: Optional[list[Intervention]] = None,
) -> str:
    """Determine the next intervention kind based on outcome history.
    
    Returns:
        One of 'MICRO_LESSON', 'REENGAGE', 'ESCALATE_MENTOR', 'REPLAN_UP', 'NONE'.
    """
    if intervention.status == "partial":
        return "MICRO_LESSON"
    elif intervention.status in ["no_response", "escalated"]:
        priors = sorted(
            [
                p for p in (prior_interventions or [])
                if p.id != intervention.id
                and (not p.student_id or p.student_id == intervention.student_id)
            ],
            key=lambda x: x.ts,
            reverse=True,
        )
        if priors and priors[0].status in ["no_response", "escalated"]:
            return "ESCALATE_MENTOR"
        return "REENGAGE"
    elif intervention.status == "recovered":
        return "REPLAN_UP"
    return "NONE"


def evaluate_student_pending_followups(
    student_id: str,
    interventions: list[Intervention],
    events: list[Event],
    sim_now: datetime,
) -> tuple[list[Intervention], list[dict[str, Any]]]:
    """Scan and evaluate all pending interventions for a student.
    
    Returns:
        tuple of (updated_interventions_list, updates_log)
    """
    updated: list[Intervention] = []
    updates_log: list[dict[str, Any]] = []

    for item in interventions:
        if item.student_id == student_id and item.status == "pending_followup":
            res = evaluate_followup(item, events=events, sim_now=sim_now, history=interventions)
            if res.status != "pending_followup":
                updated.append(res)
                updates_log.append({
                    "intervention_id": res.id,
                    "previous_status": "pending_followup",
                    "new_status": res.status,
                    "outcome_details": res.outcome_details,
                    "outcome_ts": res.outcome_ts.isoformat() if res.outcome_ts else None,
                })
            else:
                updated.append(item)
        else:
            updated.append(item)

    return updated, updates_log


def compute_recovery_metrics(
    interventions: list[Intervention],
    students: Optional[list[Student]] = None,
) -> dict[str, Any]:
    """Calculate aggregate proactive metrics for GET /api/metrics."""
    nudges_sent = sum(
        1 for i in interventions
        if i.kind != "NONE" and i.message is not None and i.status not in ["blocked", "deferred"]
    )
    nudges_blocked = sum(
        1 for i in interventions
        if i.status in ["blocked", "deferred"]
        or (
            i.message is None and (
                any("blocked" in log.lower() or "consent" in log.lower() or "cooldown" in log.lower() or "deferred" in log.lower() for log in i.guardrail_log)
                or (i.kind not in ["NONE", "ESCALATE_MENTOR"] and i.message is None)
            )
        )
    )
    escalations = sum(
        1 for i in interventions
        if i.kind == "ESCALATE_MENTOR" or i.status == "escalated"
    )
    
    evaluated = [
        i for i in interventions
        if i.status in ["recovered", "partial", "no_response", "escalated"]
        and i.status not in ["blocked", "deferred"]
        and i.kind != "NONE"
    ]
    recovered = [i for i in evaluated if i.status == "recovered"]
    recovery_rate = (len(recovered) / len(evaluated)) if evaluated else 0.0
    recovery_rate = min(1.0, max(0.0, recovery_rate))

    durations: list[float] = []
    for i in recovered:
        if i.outcome_ts and i.ts:
            hours = (i.outcome_ts - i.ts).total_seconds() / 3600.0
            if hours >= 0:
                durations.append(hours)
    avg_hours = (sum(durations) / len(durations)) if durations else 0.0

    return {
        "recovery_rate": round(recovery_rate, 4),
        "avg_hours_to_recover": round(avg_hours, 2),
        "nudges_sent": nudges_sent,
        "nudges_blocked": nudges_blocked,
        "escalations": escalations,
    }


# Specification and Polymorphic Aliases
evaluate_outcome = evaluate_followup
check_followup = evaluate_followup
