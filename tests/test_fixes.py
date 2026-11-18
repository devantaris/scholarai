"""Scholar Pulse: Regression and Feature Verification Test Suite for Recent Fixes.

Covers:
1. Blocked/deferred interventions: status='blocked' or 'deferred', followup_due=None,
   excluded from recovery_rate and nudges_sent, counted in nudges_blocked only.
2. 'Nudges Used' counter: reflects delivered messages in last 7 days and increments after delivered nudge.
3. Recent-success suppression: suppresses REENGAGE within 72h after attempt_pass or recovered outcome;
   inactivity evidence specifies real trigger point.
4. UI polish & Replanner ordering: prerequisite micro-lesson placed before target problem;
   existing concepts on roadmap are not duplicated; student display name verified.
5. Reset confirmation: verifies POST /api/sim/reset completely clears interventions, cooldowns, and nudge counts.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
import pytest

from app.api import get_metrics
from app.config import (
    DEFAULT_ACTIVE_HOURS,
    INACTIVITY_ABSOLUTE_DAYS_TRIGGER,
    INACTIVITY_THRESHOLD,
)
from app.guardrails import apply_guardrails
from app.models import (
    Event,
    Intervention,
    RoadmapDiff,
    RoadmapItem,
    SignalResult,
    Student,
)
from app.policy import decide_intervention
from app.replanner import apply_roadmap_diff, replan_roadmap
from app.signals import compute_inactivity
from app.store import store


# ============================================================================
# Fix 1 Tests: Blocked & Deferred Interventions
# ============================================================================

def test_fix_1_consent_blocked_status_and_metrics(client: Any):
    """When consent is disabled, intervention status is 'blocked', followup_due is None,

    and it is counted under nudges_blocked only (excluded from nudges_sent and recovery_rate).
    """
    client.post("/api/sim/reset")

    # Priya has consent_nudges = False
    res = client.post("/api/students/priya/evaluate")
    assert res.status_code == 200
    intv = res.json()["intervention"]
    assert intv["status"] == "blocked"
    assert intv["followup_due"] is None
    assert intv["message"] is None

    # Check metrics
    metrics = client.get("/api/metrics").json()
    assert metrics["nudges_sent"] == 0
    assert metrics["nudges_blocked"] >= 1
    assert metrics["recovery_rate"] == 0.0


def test_fix_1_quiet_hours_deferred_status_and_metrics(make_student: Any):
    """When quiet hours applies, intervention status is 'deferred', followup_due is None."""
    student = make_student(active_hours=(9, 22))
    night_time = datetime(2026, 11, 18, 23, 30, tzinfo=timezone.utc)
    candidate = Intervention(
        id="int-night",
        student_id=student.id,
        ts=night_time,
        kind="CONCEPT_NUDGE",
        signals=[],
        rationale="Stuck at night",
        message="Review concept tomorrow",
        cta="Start",
    )
    passed, modified = apply_guardrails(candidate, student, history=[], now=night_time)
    assert not passed
    assert modified.status == "deferred"
    assert modified.followup_due is None
    # Message body is preserved for next morning window
    assert modified.message == "Review concept tomorrow"


def test_fix_1_cooldown_blocked_status_and_metrics(client: Any):
    """When 24h cooldown triggers, second intervention is stored with status 'blocked'."""
    client.post("/api/sim/reset")

    # First evaluation delivers CONCEPT_NUDGE for Aarav
    res1 = client.post("/api/students/aarav/evaluate")
    assert res1.status_code == 200
    assert res1.json()["intervention"]["status"] == "pending_followup"

    # Immediate second evaluation triggers 24h cooldown
    res2 = client.post("/api/students/aarav/evaluate")
    assert res2.status_code == 200
    intv2 = res2.json()["intervention"]
    assert intv2["status"] == "blocked"
    assert intv2["followup_due"] is None

    # Metrics verify nudges_sent is 1, nudges_blocked is 1
    metrics = client.get("/api/metrics").json()
    assert metrics["nudges_sent"] == 1
    assert metrics["nudges_blocked"] == 1


# ============================================================================
# Fix 2 Tests: 'Nudges Used' Counter (Rolling 7 Days & UI Increments)
# ============================================================================

def test_fix_2_nudges_used_counter_increments_after_delivery(client: Any):
    """Nudges Used counter equals delivered messages in last 7 days and increments after delivered nudge."""
    client.post("/api/sim/reset")

    # Initial state: 0 nudges used
    student_init = client.get("/api/students/aarav").json()["student"]
    assert student_init["weekly_nudge_count"] == 0

    # Deliver first nudge
    client.post("/api/students/aarav/evaluate")
    student_after_1 = client.get("/api/students/aarav").json()["student"]
    assert student_after_1["weekly_nudge_count"] == 1

    # Cooldown blocked nudge does NOT increment weekly_nudge_count
    client.post("/api/students/aarav/evaluate")
    student_after_blocked = client.get("/api/students/aarav").json()["student"]
    assert student_after_blocked["weekly_nudge_count"] == 1


# ============================================================================
# Fix 3 Tests: Recent-Success Suppression & Inactivity Evidence
# ============================================================================

def test_fix_3_recent_attempt_pass_suppresses_reengage(base_now: datetime, make_student: Any, make_roadmap: Any):
    """Recent attempt_pass within 72h suppresses REENGAGE/disengaged interventions."""
    student = make_student(id="std-recent-pass")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Inactivity detected"),
        SignalResult(name="pace_drift", score=0.5, triggered=True, evidence="Behind schedule"),
    ]
    # Pass event happened 24h ago
    recent_events = [
        Event(student_id=student.id, ts=base_now - timedelta(hours=24), type="attempt_pass", problem_id="P-001"),
    ]

    intv = decide_intervention(
        student=student,
        roadmap=roadmap,
        signals=signals,
        now=base_now,
        events=recent_events,
    )
    # REENGAGE should NOT fire; suppressed to NONE
    assert intv.kind == "NONE"
    assert "pass" in intv.rationale.lower() or "suppress" in intv.rationale.lower()


def test_fix_3_recent_recovered_outcome_suppresses_reengage(base_now: datetime, make_student: Any, make_roadmap: Any):
    """Recent recovered outcome within 72h suppresses REENGAGE/disengaged interventions."""
    student = make_student(id="std-recent-rec")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Inactivity detected"),
    ]
    priors = [
        Intervention(
            id="int-rec",
            student_id=student.id,
            ts=base_now - timedelta(hours=36),
            kind="CONCEPT_NUDGE",
            signals=[],
            rationale="Recovered",
            status="recovered",
            outcome_ts=base_now - timedelta(hours=20),
        )
    ]

    intv = decide_intervention(
        student=student,
        roadmap=roadmap,
        signals=signals,
        now=base_now,
        previous_interventions=priors,
    )
    assert intv.kind == "NONE"
    assert "recovery" in intv.rationale.lower() or "suppress" in intv.rationale.lower()


def test_fix_3_inactivity_evidence_states_real_trigger_point(base_now: datetime, make_student: Any):
    """Inactivity evidence text states the real trigger point."""
    student = make_student(median_gap_days=1.5)
    events = [
        Event(student_id=student.id, ts=base_now - timedelta(days=6.0), type="session", duration_min=60)
    ]
    result = compute_inactivity(events, student, base_now)
    assert "trigger threshold" in result.evidence.lower() or "triggers at" in result.evidence.lower()
    assert "6.0 days since last session" in result.evidence


# ============================================================================
# Fix 4 Tests: UI Polish, Micro-Lesson Placement & Duplicate Prevention
# ============================================================================

def test_fix_4_micro_lesson_placed_before_target_problem():
    """apply_roadmap_diff places inserted micro-lesson before the problem it is a prerequisite for within the week."""
    roadmap = [
        RoadmapItem(
            id="P-014",
            week=2,
            title="Async Pipeline Problem",
            type="problem",
            est_minutes=120,
            status="todo",
            graded=True,
            prereq_concept_ids=["promises-error-handling"],
        )
    ]
    diff = RoadmapDiff(
        added=[
            RoadmapItem(
                id="promises-error-handling",
                week=2,
                title="Micro-Lesson: Promises",
                type="concept",
                est_minutes=30,
                status="todo",
                graded=False,
            )
        ]
    )
    updated = apply_roadmap_diff(roadmap, diff)
    ids = [it.id for it in updated if it.week == 2]
    # Prerequisite concept must appear before problem P-014 in the item order
    assert ids.index("promises-error-handling") < ids.index("P-014")


def test_fix_4_existing_concept_not_duplicated():
    """apply_roadmap_diff does not insert duplicate items if already present in roadmap."""
    roadmap = [
        RoadmapItem(id="C-103", week=2, title="Promises", type="concept", est_minutes=60, status="todo"),
    ]
    diff = RoadmapDiff(
        added=[
            RoadmapItem(id="C-103", week=2, title="Promises", type="concept", est_minutes=60, status="todo"),
        ]
    )
    updated = apply_roadmap_diff(roadmap, diff)
    c103_items = [it for it in updated if it.id == "C-103"]
    assert len(c103_items) == 1


# ============================================================================
# Fix 5 Tests: Simulation Reset Clears Interventions, Cooldowns & Counts
# ============================================================================

def test_fix_5_reset_clears_interventions_cooldowns_and_nudge_counts(client: Any):
    """POST /api/sim/reset completely clears interventions, cooldowns, and resets nudge counts."""
    # 1. Deliver nudge and create cooldown
    client.post("/api/students/aarav/evaluate")
    student = client.get("/api/students/aarav").json()["student"]
    assert student["weekly_nudge_count"] == 1
    interventions = client.get("/api/students/aarav/interventions").json()
    assert len(interventions) == 1

    # 2. Reset simulation
    res_reset = client.post("/api/sim/reset")
    assert res_reset.status_code == 200
    assert res_reset.json()["status"] == "ok"

    # 3. Interventions cleared
    interventions_after = client.get("/api/students/aarav/interventions").json()
    assert len(interventions_after) == 0

    # 4. Weekly nudge count reset to 0
    student_after = client.get("/api/students/aarav").json()["student"]
    assert student_after["weekly_nudge_count"] == 0

    # 5. Cooldown cleared: can evaluate and deliver immediately
    res_eval_new = client.post("/api/students/aarav/evaluate")
    assert res_eval_new.status_code == 200
    intv_new = res_eval_new.json()["intervention"]
    assert intv_new["status"] == "pending_followup"
    assert intv_new["message"] is not None

    # 6. Metrics reset
    client.post("/api/sim/reset")
    metrics = client.get("/api/metrics").json()
    assert metrics["nudges_sent"] == 0
    assert metrics["nudges_blocked"] == 0
    assert metrics["recovery_rate"] == 0.0


# ============================================================================
# Fix 6 Tests: UI & Simulation Follow-up Polish (Screenshots 1-7 Verification)
# ============================================================================

def test_fix_6_reengage_second_task_personalized_name(client: Any):
    """2nd-step re-engagement nudge addresses student by first name, not 'there'."""
    client.post("/api/sim/reset")

    # Day 0 (Nov 18): Evaluate Aarav -> CONCEPT_NUDGE
    res0 = client.post("/api/students/aarav/evaluate")
    assert res0.status_code == 200
    assert res0.json()["intervention"]["kind"] == "CONCEPT_NUDGE"

    # Advance 3 days (Nov 21): Aarav passes P-014, resolving stuck state
    client.post("/api/sim/advance", json={"days": 3})

    # Advance 6 more days (Nov 27): Aarav becomes inactive for 7+ days
    client.post("/api/sim/advance", json={"days": 6})

    # First re-engagement evaluation on Nov 27
    res1 = client.post("/api/students/aarav/evaluate")
    assert res1.status_code == 200
    intv1 = res1.json()["intervention"]
    assert intv1["kind"] == "REENGAGE"
    assert "Hi Aarav" in intv1["message"]

    # Advance clock past 48h window to Dec 6 with no student activity -> no_response
    client.post("/api/sim/advance", json={"days": 9})

    # Second evaluation triggers 2nd-step re-engagement (5-min task)
    res2 = client.post("/api/students/aarav/evaluate")
    assert res2.status_code == 200
    intv2 = res2.json()["intervention"]
    assert intv2["kind"] == "REENGAGE"
    assert "5-minute" in intv2["message"]
    # Must be personalized: 'Hi Aarav', NEVER 'Hi there'
    assert "Hi Aarav" in intv2["message"]
    assert "Hi there" not in intv2["message"]


def test_fix_6_inactivity_considers_attempt_pass_as_activity(base_now: datetime, make_student: Any):
    """Inactivity detector treats attempt_pass and attempt_fail as platform activity."""
    student = make_student(median_gap_days=1.5)
    # Student had session 4 days ago, but took and passed an assessment 1.0 day ago
    events = [
        Event(student_id=student.id, ts=base_now - timedelta(days=4), type="session", duration_min=45),
        Event(student_id=student.id, ts=base_now - timedelta(days=1), type="attempt_pass", problem_id="P-014", duration_min=30),
    ]
    res = compute_inactivity(events, student, base_now)
    # 1.0 day since last active event < 1.8 threshold
    assert res.triggered is False
    assert res.score == pytest.approx(1.0 / 3.0, abs=1e-2)
    assert "1.0 days since last session" in res.evidence


def test_fix_6_sim_advance_3_days_aarav_recovered_healthy(client: Any):
    """Advancing 3 days after CONCEPT_NUDGE marks Aarav recovered and keeps engagement healthy."""
    client.post("/api/sim/reset")

    # Day 0: Evaluate Aarav -> CONCEPT_NUDGE
    res_eval = client.post("/api/students/aarav/evaluate")
    assert res_eval.status_code == 200
    assert res_eval.json()["intervention"]["kind"] == "CONCEPT_NUDGE"

    # Advance 3 days: scripts Aarav's study session and pass on P-014
    res_adv = client.post("/api/sim/advance", json={"days": 3})
    assert res_adv.status_code == 200

    # Verify Aarav's intervention is marked recovered
    interventions = client.get("/api/students/aarav/interventions").json()
    assert len(interventions) >= 1
    recovered_nudge = next(i for i in interventions if i["kind"] == "CONCEPT_NUDGE")
    assert recovered_nudge["status"] == "recovered"

    # Detail check: signals should be healthy
    detail = client.get("/api/students/aarav").json()
    signals = {s["name"]: s for s in detail["signals"]}
    assert signals["stuck"]["triggered"] is False
    assert signals["disengaged"]["triggered"] is False

    # Evaluating on Day +3 returns kind='NONE'
    res_eval3 = client.post("/api/students/aarav/evaluate")
    assert res_eval3.status_code == 200
    intv3 = res_eval3.json()["intervention"]
    assert intv3["kind"] == "NONE"
