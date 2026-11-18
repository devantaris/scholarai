"""Unit tests for Scholar Pulse Follow-up Engine & Escalation Ladder.

Tests cover:
- Outcome evaluation at < 48h, == 48h, > 48h
- Outcome classification: recovered (pass, 2+ sessions), partial (1 session, fails), no_response
- Temporal window event filtering (pre-nudge vs post-nudge)
- Escalation ladder: partial -> MICRO_LESSON
- Escalation ladder: 1st no_response -> REENGAGE (5 min task)
- Escalation ladder: 2nd consecutive no_response -> ESCALATE_MENTOR (no student message)
- Reset of consecutive escalation state on intermediate recovery
- Batch processing of student pending follow-ups
- Recovery metrics computation
- Polymorphic aliases and model serialization
"""

from datetime import datetime, timedelta, timezone
from typing import Any
import pytest

from app.followup import (
    FollowupResult,
    check_followup,
    compute_recovery_metrics,
    determine_escalation,
    evaluate_followup,
    evaluate_outcome,
    evaluate_student_pending_followups,
)
from app.models import Event, Intervention


@pytest.fixture
def sim_base_time() -> datetime:
    return datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)


def test_followup_status_pending_when_under_48h(sim_base_time: datetime):
    """Intervention status must remain pending_followup when sim_now < followup_due."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        signals=[],
        rationale="Stuck",
        status="pending_followup",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    res = evaluate_followup(intv, events=[], sim_now=sim_base_time + timedelta(hours=24))
    assert res.status == "pending_followup"
    assert res.is_due is False


def test_followup_evaluated_at_exact_48h(sim_base_time: datetime):
    """Evaluation triggers at exactly 48h00m."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        signals=[],
        rationale="Stuck",
        status="pending_followup",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    res = evaluate_followup(intv, events=[], sim_now=sim_base_time + timedelta(hours=48))
    assert res.status != "pending_followup"
    assert res.is_due is True


def test_outcome_recovered_on_target_pass(sim_base_time: datetime):
    """Target problem passed post-nudge triggers recovered outcome."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        target_problem_id="P-014",
        signals=[],
        rationale="Stuck",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    events = [
        Event(student_id="aarav", ts=sim_base_time + timedelta(hours=24), type="attempt_pass", problem_id="P-014")
    ]
    res = evaluate_followup(intv, events=events, sim_now=sim_base_time + timedelta(hours=48))
    assert res.status == "recovered"
    assert res.outcome_ts == sim_base_time + timedelta(hours=48)
    assert res.next_kind == "REPLAN_UP"


def test_outcome_recovered_on_two_sessions(sim_base_time: datetime):
    """2+ study sessions post-nudge trigger recovered even without pass."""
    intv = Intervention(
        id="i1",
        student_id="meera",
        ts=sim_base_time,
        kind="REENGAGE",
        signals=[],
        rationale="Disengaged",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    events = [
        Event(student_id="meera", ts=sim_base_time + timedelta(hours=10), type="session", duration_min=30),
        Event(student_id="meera", ts=sim_base_time + timedelta(hours=30), type="session", duration_min=45),
    ]
    res = evaluate_followup(intv, events=events, sim_now=sim_base_time + timedelta(hours=50))
    assert res.status == "recovered"


def test_outcome_partial_on_single_session(sim_base_time: datetime):
    """1 session without passing target problem triggers partial outcome."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        target_problem_id="P-014",
        signals=[],
        rationale="Stuck",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    events = [
        Event(student_id="aarav", ts=sim_base_time + timedelta(hours=15), type="session", duration_min=45)
    ]
    res = evaluate_followup(intv, events=events, sim_now=sim_base_time + timedelta(hours=50))
    assert res.status == "partial"
    assert res.next_kind == "MICRO_LESSON"


def test_outcome_partial_on_fail_attempts_only(sim_base_time: datetime):
    """Activity with only failed attempts results in partial outcome."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        target_problem_id="P-014",
        signals=[],
        rationale="Stuck",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    events = [
        Event(student_id="aarav", ts=sim_base_time + timedelta(hours=15), type="attempt_fail", problem_id="P-014")
    ]
    res = evaluate_followup(intv, events=events, sim_now=sim_base_time + timedelta(hours=50))
    assert res.status == "partial"


def test_outcome_no_response_on_zero_events(sim_base_time: datetime):
    """Zero events in 48h results in no_response outcome."""
    intv = Intervention(
        id="i1",
        student_id="meera",
        ts=sim_base_time,
        kind="REENGAGE",
        signals=[],
        rationale="Disengaged",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    res = evaluate_followup(intv, events=[], sim_now=sim_base_time + timedelta(hours=48))
    assert res.status == "no_response"
    assert res.next_kind == "REENGAGE"


def test_pass_event_prior_to_nudge_ignored(sim_base_time: datetime):
    """Pass events before intervention ts are ignored."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        target_problem_id="P-014",
        signals=[],
        rationale="Stuck",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    events = [
        Event(student_id="aarav", ts=sim_base_time - timedelta(minutes=10), type="attempt_pass", problem_id="P-014")
    ]
    res = evaluate_followup(intv, events=events, sim_now=sim_base_time + timedelta(hours=48))
    assert res.status == "no_response"


def test_pass_event_different_student_ignored(sim_base_time: datetime):
    """Pass events belonging to another student are ignored."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        target_problem_id="P-014",
        signals=[],
        rationale="Stuck",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    events = [
        Event(student_id="meera", ts=sim_base_time + timedelta(hours=20), type="attempt_pass", problem_id="P-014")
    ]
    res = evaluate_followup(intv, events=events, sim_now=sim_base_time + timedelta(hours=48))
    assert res.status == "no_response"


def test_pass_event_different_problem_ignored(sim_base_time: datetime):
    """Passing a different problem does not satisfy target problem pass."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        target_problem_id="P-014",
        signals=[],
        rationale="Stuck",
        followup_due=sim_base_time + timedelta(hours=48),
    )
    events = [
        Event(student_id="aarav", ts=sim_base_time + timedelta(hours=20), type="attempt_pass", problem_id="P-099")
    ]
    res = evaluate_followup(intv, events=events, sim_now=sim_base_time + timedelta(hours=48))
    # It counts as an event post-nudge, so status is partial, not recovered!
    assert res.status == "partial"


def test_escalation_ladder_partial_to_micro_lesson(sim_base_time: datetime):
    """Intervention with status='partial' escalates to MICRO_LESSON."""
    intv = Intervention(
        id="i1",
        student_id="aarav",
        ts=sim_base_time,
        kind="CONCEPT_NUDGE",
        concept_id="promises-error-handling",
        signals=[],
        rationale="Stuck",
        status="partial",
    )
    res = evaluate_followup(intv, events=[], sim_now=sim_base_time + timedelta(hours=48))
    assert res.kind == "MICRO_LESSON"
    assert res.next_kind == "MICRO_LESSON"
    assert res.message is not None


def test_escalation_ladder_first_no_response_to_reengage(sim_base_time: datetime):
    """First no_response intervention sends smaller 5-minute task."""
    intv = Intervention(
        id="i1",
        student_id="meera",
        ts=sim_base_time,
        kind="REENGAGE",
        signals=[],
        rationale="Inactive",
        status="no_response",
    )
    res = evaluate_followup(intv, events=[], history=[], sim_now=sim_base_time + timedelta(hours=48))
    assert res.kind == "REENGAGE"
    assert res.next_kind == "REENGAGE"
    assert "5-minute" in res.message


def test_escalation_ladder_second_consecutive_no_response_to_mentor(sim_base_time: datetime):
    """Second consecutive no_response escalates to ESCALATE_MENTOR with no message."""
    prior = Intervention(
        id="p1",
        student_id="meera",
        ts=sim_base_time - timedelta(days=3),
        kind="REENGAGE",
        signals=[],
        rationale="Inactive 1",
        status="no_response",
    )
    curr = Intervention(
        id="c1",
        student_id="meera",
        ts=sim_base_time - timedelta(days=1),
        kind="REENGAGE",
        signals=[],
        rationale="Inactive 2",
        status="no_response",
        followup_due=sim_base_time,
    )
    res = evaluate_followup(curr, events=[], history=[prior], sim_now=sim_base_time)
    assert res.kind == "ESCALATE_MENTOR"
    assert res.status == "escalated"
    assert res.message is None
    assert res.cta is None


def test_escalation_ladder_recovery_resets_streak(sim_base_time: datetime):
    """An intermediate recovery resets the consecutive no_response counter."""
    int_1 = Intervention(id="1", student_id="s", ts=sim_base_time - timedelta(days=10), kind="REENGAGE", signals=[], rationale="r", status="no_response")
    int_2 = Intervention(id="2", student_id="s", ts=sim_base_time - timedelta(days=5), kind="CONCEPT_NUDGE", signals=[], rationale="r", status="recovered")
    int_3 = Intervention(id="3", student_id="s", ts=sim_base_time - timedelta(days=1), kind="REENGAGE", signals=[], rationale="r", status="no_response", followup_due=sim_base_time)
    res = evaluate_followup(int_3, events=[], history=[int_1, int_2], sim_now=sim_base_time)
    assert res.kind != "ESCALATE_MENTOR"
    assert res.next_kind == "REENGAGE"


def test_determine_escalation_helper(sim_base_time: datetime):
    """determine_escalation helper identifies the appropriate next action."""
    i_part = Intervention(id="1", student_id="s", ts=sim_base_time, kind="CONCEPT_NUDGE", signals=[], rationale="r", status="partial")
    assert determine_escalation(i_part) == "MICRO_LESSON"

    i_rec = Intervention(id="2", student_id="s", ts=sim_base_time, kind="CONCEPT_NUDGE", signals=[], rationale="r", status="recovered")
    assert determine_escalation(i_rec) == "REPLAN_UP"

    i_no = Intervention(id="3", student_id="s", ts=sim_base_time, kind="REENGAGE", signals=[], rationale="r", status="no_response")
    assert determine_escalation(i_no) == "REENGAGE"

    p_no = Intervention(id="p", student_id="s", ts=sim_base_time - timedelta(days=2), kind="REENGAGE", signals=[], rationale="r", status="no_response")
    assert determine_escalation(i_no, prior_interventions=[p_no]) == "ESCALATE_MENTOR"


def test_evaluate_student_pending_followups_batch(sim_base_time: datetime):
    """Batch evaluations update all due pending interventions."""
    int_due = Intervention(
        id="due_1", student_id="aarav", ts=sim_base_time - timedelta(hours=50),
        kind="CONCEPT_NUDGE", signals=[], rationale="r", status="pending_followup",
        followup_due=sim_base_time - timedelta(hours=2)
    )
    int_not_due = Intervention(
        id="not_due", student_id="aarav", ts=sim_base_time - timedelta(hours=10),
        kind="HINT_LADDER", signals=[], rationale="r", status="pending_followup",
        followup_due=sim_base_time + timedelta(hours=38)
    )
    events = [Event(student_id="aarav", ts=sim_base_time - timedelta(hours=20), type="session", duration_min=45)]
    updated, logs = evaluate_student_pending_followups("aarav", [int_due, int_not_due], events, sim_base_time)
    
    assert len(logs) == 1
    assert logs[0]["intervention_id"] == "due_1"
    assert logs[0]["new_status"] == "partial"
    status_map = {i.id: i.status for i in updated}
    assert status_map["due_1"] == "partial"
    assert status_map["not_due"] == "pending_followup"


def test_compute_recovery_metrics_empty():
    """Metrics calculation handles zero interventions cleanly."""
    metrics = compute_recovery_metrics([])
    assert metrics["recovery_rate"] == 0.0
    assert metrics["avg_hours_to_recover"] == 0.0
    assert metrics["nudges_sent"] == 0
    assert metrics["nudges_blocked"] == 0
    assert metrics["escalations"] == 0


def test_compute_recovery_metrics_normal(sim_base_time: datetime):
    """Metrics accurately aggregate recovery rate, duration, and blocks."""
    int_rec = Intervention(
        id="1", student_id="aarav", ts=sim_base_time, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", message="Hello", status="recovered",
        outcome_ts=sim_base_time + timedelta(hours=36)
    )
    int_blocked = Intervention(
        id="2", student_id="priya", ts=sim_base_time, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", message=None, guardrail_log=["[GUARDRAIL BLOCKED] Consent disabled"],
        status="no_response", outcome_ts=sim_base_time + timedelta(hours=48)
    )
    int_esc = Intervention(
        id="3", student_id="meera", ts=sim_base_time, kind="ESCALATE_MENTOR",
        signals=[], rationale="r", message=None, status="escalated"
    )

    metrics = compute_recovery_metrics([int_rec, int_blocked, int_esc])
    assert metrics["nudges_sent"] == 1
    assert metrics["nudges_blocked"] == 1
    assert metrics["escalations"] == 1
    assert round(metrics["recovery_rate"], 2) == 0.33
    assert metrics["avg_hours_to_recover"] == 36.0


def test_followup_aliases_compatibility(sim_base_time: datetime):
    """Function aliases evaluate_outcome and check_followup match evaluate_followup."""
    intv = Intervention(
        id="i1", student_id="aarav", ts=sim_base_time, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", status="pending_followup",
        followup_due=sim_base_time + timedelta(hours=48)
    )
    res1 = evaluate_outcome(intv, events=[], sim_now=sim_base_time + timedelta(hours=24))
    res2 = check_followup(intv, events=[], sim_now=sim_base_time + timedelta(hours=24))
    assert res1.status == "pending_followup"
    assert res2.status == "pending_followup"


def test_followup_result_pydantic_serialization(sim_base_time: datetime):
    """FollowupResult serializes cleanly to dict and JSON."""
    intv = Intervention(
        id="i1", student_id="aarav", ts=sim_base_time, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", status="pending_followup",
        followup_due=sim_base_time + timedelta(hours=48)
    )
    res = evaluate_followup(intv, events=[], sim_now=sim_base_time + timedelta(hours=50))
    dumped = res.model_dump()
    assert dumped["id"] == "i1"
    assert dumped["status"] == "no_response"
    assert dumped["next_kind"] == "REENGAGE"
    assert dumped["is_due"] is True
