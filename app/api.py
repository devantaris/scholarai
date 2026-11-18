"""Scholar Pulse: REST API Endpoints.

Implements all 9 FastAPI endpoints connecting frontend controls, state store,
signal detectors, decision policy, guardrails, and simulation clock.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.config import (
    DEFAULT_ACTIVE_HOURS,
    DISENGAGED_THRESHOLD,
    FOLLOWUP_WINDOW_HOURS,
    PACE_DRIFT_THRESHOLD,
    REENGAGE_SECOND_TASK_MIN,
    REPEATED_FAILURE_THRESHOLD,
    STALL_TIME_THRESHOLD,
)
from app.followup import determine_escalation, evaluate_followup
from app.generator import generate_template_nudge, is_live_llm_mode
from app.guardrails import apply_guardrails
from app.models import (
    CohortResponse,
    CohortStudentItem,
    EvaluateResponse,
    Event,
    Intervention,
    MetricsResponse,
    ModeResponse,
    RoadmapItem,
    SignalResult,
    SimAdvanceRequest,
    SimAdvanceResponse,
    StudentSummary,
    get_concept,
)
from app.policy import decide_intervention
from app.replanner import apply_roadmap_diff, replan_roadmap
from app.signals import detect_all_signals
from app.sim import advance_clock, reset_simulation
from app.store import store

router = APIRouter(prefix="/api", tags=["Scholar Pulse API"])


def _determine_current_week(roadmap: list[RoadmapItem]) -> int:
    """Find current week from active roadmap items."""
    for item in roadmap:
        if item.status == "in_progress":
            return item.week
    for item in roadmap:
        if item.status == "todo":
            return item.week
    return 1


def _find_target_problem_id(roadmap: list[RoadmapItem], events: list[Event]) -> Optional[str]:
    """Identify the target problem requiring intervention."""
    for item in roadmap:
        if item.type == "problem" and item.status == "in_progress":
            return item.id
    for item in roadmap:
        if item.type == "problem" and item.status == "todo":
            if any(e.problem_id == item.id and e.type == "attempt_fail" for e in events):
                return item.id
    for e in reversed(events):
        if e.problem_id and e.type == "attempt_fail":
            return e.problem_id
    for item in roadmap:
        if item.type == "problem":
            return item.id
    return None


@router.get("/mode", response_model=ModeResponse)
def get_mode() -> ModeResponse:
    """Return LLM execution mode and current simulated ISO timestamp."""
    mode = "live" if is_live_llm_mode() else "template"
    return ModeResponse(
        llm=mode,
        sim_now=store.get_sim_now().isoformat(),
    )


@router.get("/students", response_model=list[StudentSummary])
def get_students() -> list[StudentSummary]:
    """Return all enrolled students with computed risk summaries."""
    students = store.get_students()
    sim_now = store.get_sim_now()
    summaries: list[StudentSummary] = []

    for s in students:
        roadmap = store.get_roadmap(s.id)
        events = store.get_events(s.id)
        current_week = _determine_current_week(roadmap)
        target_problem_id = _find_target_problem_id(roadmap, events)
        signals = detect_all_signals(
            student=s,
            roadmap=roadmap,
            events=events,
            current_week=current_week,
            target_problem_id=target_problem_id,
            now=sim_now,
        )
        sig_map = {sig.name: sig for sig in signals}
        stuck_score = sig_map["stuck"].score if "stuck" in sig_map else 0.0
        disengaged_score = sig_map["disengaged"].score if "disengaged" in sig_map else 0.0

        if stuck_score >= 0.6 or disengaged_score >= 0.6:
            risk_level = "high"
        elif stuck_score >= 0.4 or disengaged_score >= 0.4 or (sig_map.get("pace_drift") and sig_map["pace_drift"].score >= 0.3):
            risk_level = "medium"
        elif any(sig.triggered for sig in signals):
            risk_level = "low"
        else:
            risk_level = "healthy"

        interventions = store.get_interventions(s.id)
        active_count = sum(1 for i in interventions if i.status == "pending_followup")

        summaries.append(StudentSummary(
            id=s.id,
            name=s.name,
            goal_prompt=s.goal_prompt,
            roadmap_goal=s.roadmap_goal,
            consent_nudges=s.consent_nudges,
            stuck_score=round(stuck_score, 2),
            disengaged_score=round(disengaged_score, 2),
            risk_level=risk_level,
            active_interventions_count=active_count,
        ))

    return summaries


@router.get("/students/{id}")
def get_student_detail(id: str) -> dict[str, Any]:
    """Return student details, roadmap, past 30 days of events, and real-time signals."""
    s = store.get_student(id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Student '{id}' not found")

    sim_now = store.get_sim_now()
    roadmap = store.get_roadmap(id)
    events_30d = store.get_events(id, since=sim_now - timedelta(days=30))
    all_events = store.get_events(id)
    current_week = _determine_current_week(roadmap)
    target_problem_id = _find_target_problem_id(roadmap, all_events)
    signals = detect_all_signals(
        student=s,
        roadmap=roadmap,
        events=all_events,
        current_week=current_week,
        target_problem_id=target_problem_id,
        now=sim_now,
    )

    # Calculate weekly_nudge_count: delivered messages in last 7 days
    cap_start = sim_now - timedelta(days=7)
    past_interventions = store.get_interventions(id)
    weekly_nudge_count = sum(
        1 for i in past_interventions
        if i.message is not None
        and i.status not in ["blocked", "deferred"]
        and cap_start <= i.ts <= sim_now
    )
    s_copy = s.model_copy(deep=True)
    s_copy.weekly_nudge_count = weekly_nudge_count

    return {
        "student": s_copy,
        "roadmap": roadmap,
        "events": events_30d,
        "signals": signals,
    }


@router.post("/students/{id}/evaluate", response_model=EvaluateResponse)
def evaluate_student(id: str) -> EvaluateResponse:
    """Execute proactive detect -> decide -> guardrails -> generate loop."""
    s = store.get_student(id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Student '{id}' not found")

    sim_now = store.get_sim_now()
    roadmap = store.get_roadmap(id)
    events = store.get_events(id)
    past_interventions = store.get_interventions(id)

    # 1. Evaluate pending follow-ups due
    followup_updates: list[dict[str, Any]] = []
    for past_int in past_interventions:
        if past_int.status == "pending_followup" and past_int.followup_due and sim_now >= past_int.followup_due:
            ev_since = [e for e in events if e.ts >= past_int.ts and e.ts <= sim_now]
            updated_int = evaluate_followup(past_int, events=ev_since, sim_now=sim_now, history=past_interventions)
            store.update_intervention(updated_int)
            followup_updates.append({
                "intervention_id": updated_int.id,
                "status": updated_int.status,
                "outcome_ts": updated_int.outcome_ts.isoformat() if updated_int.outcome_ts else None,
            })

    # Refresh interventions after follow-up processing
    past_interventions = store.get_interventions(id)

    # 2. Detect real-time signals
    current_week = _determine_current_week(roadmap)
    target_problem_id = _find_target_problem_id(roadmap, events)
    signals = detect_all_signals(
        student=s,
        roadmap=roadmap,
        events=events,
        current_week=current_week,
        target_problem_id=target_problem_id,
        now=sim_now,
    )

    # 3. Check escalation ladder or apply decision policy
    intervention: Optional[Intervention] = None

    # Check if immediate previous evaluated intervention triggers escalation
    evaluated_priors = [i for i in past_interventions if i.status in ["recovered", "partial", "no_response", "escalated"]]
    if evaluated_priors:
        last_eval = evaluated_priors[0]  # sorted reverse by ts
        other_priors = evaluated_priors[1:]
        next_action_kind = determine_escalation(last_eval, prior_interventions=other_priors)

        if next_action_kind == "ESCALATE_MENTOR":
            intervention = Intervention(
                id=f"int-{uuid.uuid4().hex[:8]}",
                student_id=s.id,
                ts=sim_now,
                kind="ESCALATE_MENTOR",
                signals=signals,
                rationale="Student remained inactive across consecutive follow-ups; escalating to human mentor.",
                message=None,
                cta=None,
                target_problem_id=None,
                concept_id=None,
                roadmap_diff=None,
                guardrail_log=[],
                status="escalated",
                followup_due=None,
            )
        elif next_action_kind == "MICRO_LESSON":
            concept_cid = last_eval.concept_id
            concept_obj = get_concept(concept_cid) if concept_cid else None
            msg, cta = generate_template_nudge("MICRO_LESSON", concept=concept_obj)
            intervention = Intervention(
                id=f"int-{uuid.uuid4().hex[:8]}",
                student_id=s.id,
                ts=sim_now,
                kind="MICRO_LESSON",
                signals=signals,
                rationale=f"Escalated from partial recovery on {last_eval.target_problem_id or 'problem'}; assigning focused micro-lesson.",
                message=msg,
                cta=cta,
                target_problem_id=last_eval.target_problem_id,
                concept_id=concept_cid,
                roadmap_diff=None,
                guardrail_log=[],
                status="pending_followup",
                followup_due=sim_now + timedelta(hours=FOLLOWUP_WINDOW_HOURS),
            )
        elif next_action_kind == "REENGAGE" and last_eval.status == "no_response":
            # 2nd step re-engagement: smaller 5-minute task
            first_name = s.name.split()[0] if s and s.name else "there"
            msg = (
                f"Hi {first_name}, great to see you! "
                f"Let's get your study momentum rolling today with a quick {REENGAGE_SECOND_TASK_MIN}-minute warm-up exercise."
            )
            cta = f"Start {REENGAGE_SECOND_TASK_MIN}-Min Task"
            pace_drift_trig = any(sig.name == "pace_drift" and sig.triggered for sig in signals)
            rdiff = replan_roadmap(roadmap, kind="REPLAN_DOWN", current_week=current_week) if pace_drift_trig else None
            intervention = Intervention(
                id=f"int-{uuid.uuid4().hex[:8]}",
                student_id=s.id,
                ts=sim_now,
                kind="REENGAGE",
                signals=signals,
                rationale="First no-response; sending low-friction 5-minute warm-up task to restore cadence.",
                message=msg,
                cta=cta,
                target_problem_id=None,
                concept_id=None,
                roadmap_diff=rdiff,
                guardrail_log=[],
                status="pending_followup",
                followup_due=sim_now + timedelta(hours=FOLLOWUP_WINDOW_HOURS),
            )

    if intervention is None:
        intervention = decide_intervention(
            student=s,
            roadmap=roadmap,
            signals=signals,
            current_week=current_week,
            target_problem_id=target_problem_id,
            now=sim_now,
            previous_interventions=past_interventions,
            events=events,
        )

    # 4. Apply guardrails
    target_problem = next((item for item in roadmap if item.id == target_problem_id), None)
    passed, guardrailed_intervention = apply_guardrails(
        intervention=intervention,
        student=s,
        history=past_interventions,
        now=sim_now,
        problem=target_problem,
    )

    # 5. Apply roadmap adjustments if present
    if guardrailed_intervention.roadmap_diff:
        updated_roadmap = apply_roadmap_diff(roadmap, guardrailed_intervention.roadmap_diff)
        store.update_roadmap(s.id, updated_roadmap)

    # 6. Save intervention
    store.save_intervention(guardrailed_intervention)

    return EvaluateResponse(
        intervention=guardrailed_intervention,
        signals=signals,
        roadmap=store.get_roadmap(s.id),
        followup_updates=followup_updates,
    )


@router.get("/students/{id}/interventions", response_model=list[Intervention])
def get_student_interventions(id: str) -> list[Intervention]:
    """Return chronological intervention history for a student."""
    s = store.get_student(id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Student '{id}' not found")
    return store.get_interventions(id)


@router.post("/sim/advance", response_model=SimAdvanceResponse)
def post_sim_advance(req: SimAdvanceRequest) -> SimAdvanceResponse:
    """Advance simulation clock and execute scripted student actions."""
    result = advance_clock(store=store, days=req.days)
    return SimAdvanceResponse(
        sim_now=result["sim_now"],
        days_advanced=result["days_advanced"],
        events_generated=result["events_generated"],
    )


@router.post("/sim/reset")
def post_sim_reset() -> dict[str, str]:
    """Reset simulation store and clock to baseline seed state."""
    reset_simulation(store=store)
    return {
        "status": "ok",
        "message": "Simulation reset to seed state",
        "sim_now": store.get_sim_now().isoformat(),
    }


@router.get("/cohort", response_model=CohortResponse)
def get_cohort() -> CohortResponse:
    """Return at-risk students for mentor and college placement view."""
    students = store.get_students()
    sim_now = store.get_sim_now()
    at_risk: list[CohortStudentItem] = []

    for s in students:
        roadmap = store.get_roadmap(s.id)
        events = store.get_events(s.id)
        interventions = store.get_interventions(s.id)
        current_week = _determine_current_week(roadmap)
        target_problem_id = _find_target_problem_id(roadmap, events)
        signals = detect_all_signals(
            student=s,
            roadmap=roadmap,
            events=events,
            current_week=current_week,
            target_problem_id=target_problem_id,
            now=sim_now,
        )
        sig_map = {sig.name: sig for sig in signals}

        is_escalated = any(i.status == "escalated" or i.kind == "ESCALATE_MENTOR" for i in interventions)
        stuck_trig = bool(sig_map.get("stuck") and sig_map["stuck"].triggered)
        disengaged_trig = bool(sig_map.get("disengaged") and sig_map["disengaged"].triggered)

        session_events = [e for e in events if e.type == "session"]
        last_session_ts = max((e.ts for e in session_events), default=sim_now - timedelta(days=6))
        inactivity_days = max(0.0, (sim_now - last_session_ts).total_seconds() / 86400.0)

        if is_escalated or stuck_trig or disengaged_trig or not s.consent_nudges:
            if is_escalated:
                status_str = "escalated"
                primary_sig = "Escalated to mentor (consecutive no response)"
                action = "Urgent: Mentor/placement cell outreach required"
                days_at_risk = round(inactivity_days, 1)
            elif not s.consent_nudges and stuck_trig:
                status_str = "consent_blocked"
                primary_sig = f"Stuck on {target_problem_id or 'graded problem'} (proactive nudges opted out)"
                action = "Conduct 1-on-1 mentor sync; proactive nudges disabled"
                days_at_risk = 3.0
            elif stuck_trig:
                status_str = "stuck"
                primary_sig = sig_map["stuck"].evidence
                action = "Review prerequisite concepts or provide micro-lesson"
                days_at_risk = 2.0
            elif disengaged_trig:
                status_str = "disengaged"
                primary_sig = sig_map["disengaged"].evidence
                action = "Send low-friction 10m re-engagement task"
                days_at_risk = round(inactivity_days, 1)
            else:
                status_str = "at_risk"
                primary_sig = "Pace drift detected"
                action = "Adjust weekly workload capacity"
                days_at_risk = 1.0

            at_risk.append(CohortStudentItem(
                id=s.id,
                name=s.name,
                primary_signal=primary_sig,
                suggested_action=action,
                days_at_risk=days_at_risk,
                status=status_str,
                consent_nudges=s.consent_nudges,
            ))

    return CohortResponse(at_risk_students=at_risk)


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics() -> MetricsResponse:
    """Return aggregate proactive effectiveness metrics."""
    interventions = store.get_all_interventions()
    total_int = len(interventions)
    if total_int == 0:
        return MetricsResponse(
            recovery_rate=0.0,
            avg_hours_to_recover=0.0,
            nudges_sent=0,
            nudges_blocked=0,
            escalations=0,
        )

    # Exclude blocked and deferred interventions from evaluated outcomes and recovery rate
    evaluated = [
        i for i in interventions
        if i.status in ["recovered", "partial", "no_response", "escalated"]
        and i.status not in ["blocked", "deferred"]
        and i.kind != "NONE"
    ]
    recovered = [i for i in evaluated if i.status == "recovered"]
    recovery_rate = len(recovered) / len(evaluated) if len(evaluated) > 0 else 0.0
    recovery_rate = min(1.0, max(0.0, recovery_rate))

    recovery_durations = [
        (i.outcome_ts - i.ts).total_seconds() / 3600.0
        for i in recovered
        if i.outcome_ts and i.outcome_ts >= i.ts
    ]
    avg_hours = sum(recovery_durations) / len(recovery_durations) if recovery_durations else 0.0

    # nudges_sent: delivered messages (exclude blocked and deferred)
    nudges_sent = sum(
        1 for i in interventions
        if i.message is not None and i.status not in ["blocked", "deferred"] and i.kind != "NONE"
    )

    # nudges_blocked: counted under nudges_blocked only
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

    escalations = sum(1 for i in interventions if i.status == "escalated" or i.kind == "ESCALATE_MENTOR")

    return MetricsResponse(
        recovery_rate=round(recovery_rate, 2),
        avg_hours_to_recover=round(avg_hours, 1),
        nudges_sent=nudges_sent,
        nudges_blocked=nudges_blocked,
        escalations=escalations,
    )
