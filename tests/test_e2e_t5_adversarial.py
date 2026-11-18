"""Tier 5: Adversarial, Concurrency & Security Stress Test Suite for Scholar Pulse.

Adversarial verification testing across all 5 system layers:
1. Signals Engine:
   - Boundary exactness, out-of-order event arrivals, future-event immunity, and pass-clearing logic.
   - Extreme duration clamping (10,000+ min) and division-by-zero resilience on zero est_minutes.
2. Guardrails & Policy Decision Matrix:
   - Rolling 7-day window accuracy evaluating only delivered messages.
   - Second-precision 24h cooldown enforcement with kind-specific scoping.
   - Graded work hint immunity pre-generation and code-fence/backtick filtering post-generation.
   - Word count cap and curated concept catalog validation.
3. Replanner Topological Invariants:
   - Cyclic prerequisite resolution without infinite recursion or hangs.
   - Deep 5-level dependency cascade propagation.
   - Graded assessment preservation during GOAL_REPLAN re-alignment.
   - Heavy load cascading spillover across multiple weeks under strict weekly capacity.
4. Follow-up Outcome Evaluation & Escalation:
   - Pending evaluation before 48-hour cutoff.
   - Full outcome transition matrix (recovered pass, recovered sessions, partial, no_response).
   - Consecutive no_response escalation ladder to ESCALATE_MENTOR.
   - Cross-student history isolation preventing false escalation.
5. REST API Endpoint Robustness:
   - Input fuzzing (negative, string, object, array, null, float) on /api/sim/advance.
   - Upper bound validation (days <= 36500) preventing timedelta year overflow.
   - SQL-injection, traversal, and script strings in student {id} path parameter.
   - Unicode, emoji, foreign scripts, and injection strings in Student models.
   - Large payload resilience with 65KB+ goal prompts.
   - Unsupported HTTP methods (405 Method Not Allowed).
   - Rapid repeated evaluations suppressed by 24h cooldown.
6. Store & Clock Concurrency & Stress:
   - Negative and fractional days rejection (422 / ValueError).
   - Zero days idempotence.
   - Long-range simulation (150+ and 365+ days) and timestamp validity.
   - Reset simulation mid-timeline.
   - Multithreaded store reads, writes, and resets (750 operations with 0 errors).
   - Concurrent API requests under load (200 requests with 0 deadlocks or race conditions).
7. Multi-Student Cohort Metrics & Security:
   - Denominator safety at 0 interventions vs 0% recovered vs 100% recovered.
   - Accurate average recovery hours calculation and resilience against corrupt timestamps (outcome_ts < ts).
   - Cohort at-risk student deduplication.
   - Directory path traversal attacks blocked (/static/../../app/config.py).
   - Standard Content-Type headers for HTML, CSS, JavaScript, and JSON.
"""

from datetime import datetime, timedelta, timezone
import threading
from typing import Any
import pytest
from fastapi.testclient import TestClient

from app.config import (
    COOLDOWN_HOURS,
    DEFAULT_WEEKLY_CAPACITY_MIN,
    MAX_NUDGE_WORDS,
    MAX_NUDGES_PER_WEEK,
    REPEATED_FAILURE_WINDOW_HOURS,
    WEEKLY_CAP_WINDOW_DAYS,
)
from app.followup import determine_escalation, evaluate_followup
from app.guardrails import apply_guardrails, validate_nudge, validate_roadmap_diff
from app.main import app
from app.models import (
    CURATED_CONCEPTS,
    ContentConcept,
    Event,
    Intervention,
    RoadmapDiff,
    RoadmapItem,
    SignalResult,
    Student,
)
from app.policy import decide_intervention
from app.replanner import apply_roadmap_diff, replan_roadmap
from app.signals import (
    clamp,
    compute_disengaged,
    compute_goal_change,
    compute_inactivity,
    compute_pace_ahead,
    compute_pace_drift,
    compute_repeated_failure,
    compute_stall_time,
    compute_stuck,
    detect_all_signals,
)
from app.sim import advance_clock
from app.store import Store, store


# =============================================================================
# Group 1: Signals Engine Adversarial & Boundary Stress
# =============================================================================

def test_adversarial_repeated_failure_unordered_timestamps():
    """Verify repeated failure calculation is invariant to event arrival order."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    target_pid = "P-ADV-01"

    # Failures arriving out of chronological order
    shuffled_events = [
        Event(student_id="s1", ts=now - timedelta(hours=10), type="attempt_fail", problem_id=target_pid, error_signature="TypeError"),
        Event(student_id="s1", ts=now - timedelta(hours=50), type="attempt_fail", problem_id=target_pid, error_signature="TypeError"),
        Event(student_id="s1", ts=now - timedelta(hours=2), type="attempt_fail", problem_id=target_pid, error_signature="TypeError"),
        Event(student_id="s1", ts=now - timedelta(hours=35), type="attempt_fail", problem_id=target_pid, error_signature="TypeError"),
    ]

    res = compute_repeated_failure(shuffled_events, target_pid, now)
    assert res.triggered is True
    # (4 - 2) / 4 + 0.2 error bonus = 0.5 + 0.2 = 0.7
    assert res.score == pytest.approx(0.7, abs=1e-3)
    assert "4 with the same error TypeError" in res.evidence


def test_adversarial_repeated_failure_window_exact_cutoff():
    """Verify exact temporal boundary cutoffs for the 72h lookback window."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    target_pid = "P-ADV-02"

    events = [
        # Exactly at 72h cutoff (inclusive boundary)
        Event(student_id="s1", ts=now - timedelta(hours=72), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
        # Just inside 72h
        Event(student_id="s1", ts=now - timedelta(hours=71, minutes=59), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
        # Just outside 72h (must be excluded)
        Event(student_id="s1", ts=now - timedelta(hours=72, seconds=1), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
        # Future event (must be excluded)
        Event(student_id="s1", ts=now + timedelta(hours=2), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
    ]

    res = compute_repeated_failure(events, target_pid, now)
    # Only 2 events fall inside [now - 72h, now]
    assert "2 failed attempts" in res.evidence
    # raw_score = (2 - 2)/4 + 0.0 = 0.0
    assert res.score == 0.0
    assert res.triggered is False


def test_adversarial_repeated_failure_historic_pass_clears_signal():
    """Verify that any attempt_pass on the target problem zeroes repeated_failure."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    target_pid = "P-ADV-03"

    events = [
        # Pass occurred 5 days ago
        Event(student_id="s1", ts=now - timedelta(days=5), type="attempt_pass", problem_id=target_pid),
        # 5 recent failures in last 24h
        Event(student_id="s1", ts=now - timedelta(hours=20), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
        Event(student_id="s1", ts=now - timedelta(hours=15), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
        Event(student_id="s1", ts=now - timedelta(hours=10), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
        Event(student_id="s1", ts=now - timedelta(hours=5), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
        Event(student_id="s1", ts=now - timedelta(hours=1), type="attempt_fail", problem_id=target_pid, error_signature="E1"),
    ]

    res = compute_repeated_failure(events, target_pid, now)
    assert res.score == 0.0
    assert res.triggered is False
    assert "passed; repeated failure cleared" in res.evidence


def test_adversarial_stall_time_massive_duration_and_zero_estimate():
    """Verify stall time clamping on extreme durations and safety on zero est_minutes."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    target_pid = "P-ADV-04"

    # Extreme duration: 10,000 minutes spent
    events_massive = [
        Event(student_id="s1", ts=now - timedelta(hours=5), type="session", problem_id=target_pid, duration_min=10000)
    ]
    res_massive = compute_stall_time(events_massive, target_pid, est_minutes=30, now=now)
    assert res_massive.score == 1.0  # Clamped cleanly at 1.0
    assert res_massive.triggered is True

    # Zero or negative est_minutes
    res_zero_est = compute_stall_time(events_massive, target_pid, est_minutes=0, now=now)
    assert res_zero_est.score == 0.0
    assert res_zero_est.triggered is False
    assert "Invalid estimated duration" in res_zero_est.evidence


def test_adversarial_inactivity_future_events_and_non_session_fallback():
    """Verify inactivity ignores future events and falls back gracefully when session events are absent."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    student = Student(
        id="s1", name="Adversarial Student", goal_prompt="Web", roadmap_goal="Web",
        consent_nudges=True, active_hours=(9, 22), weekly_capacity_min=600, median_gap_days=2.0
    )

    # Only non-session events exist (e.g., attempt_fail 2 days ago) + future event
    events = [
        Event(student_id="s1", ts=now - timedelta(days=2), type="attempt_fail", problem_id="P1"),
        Event(student_id="s1", ts=now + timedelta(days=3), type="session", duration_min=30),  # Future: ignored
    ]

    res = compute_inactivity(events, student, now)
    # 2 days since last event. Denom: max(2.0, 2.0 * 2.0) = 4.0 days.
    # Score: 2.0 / 4.0 = 0.5. Trigger: score >= 0.6 or days >= 5.0 -> False.
    assert res.score == pytest.approx(0.5, abs=1e-3)
    assert res.triggered is False
    assert "2.0 days since last session" in res.evidence


def test_adversarial_pace_drift_out_of_order_completion():
    """Verify pace drift and pace ahead with non-sequential item completion."""
    roadmap = [
        RoadmapItem(id="W1_1", week=1, title="W1 Item", type="problem", est_minutes=100, status="todo", graded=False, prereq_concept_ids=[]),
        RoadmapItem(id="W4_1", week=4, title="W4 Advanced", type="problem", est_minutes=300, status="done", graded=False, prereq_concept_ids=[]),
    ]

    # At current_week = 1: planned is 100 min, actual is 300 min (done)
    drift = compute_pace_drift(roadmap, current_week=1)
    ahead = compute_pace_ahead(roadmap, current_week=1)

    assert drift.score == 0.0
    assert drift.triggered is False
    assert ahead.triggered is True
    assert ahead.score == 1.0  # Clamped at 1.0


# =============================================================================
# Group 2: Guardrails & Policy Decision Matrix Stress
# =============================================================================

def test_adversarial_guardrails_weekly_cap_rolling_window():
    """Verify that weekly cap evaluates exactly rolling 7 days and only delivered nudges."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    student = Student(
        id="s1", name="Cap Student", goal_prompt="Web", roadmap_goal="Web",
        consent_nudges=True, active_hours=(9, 22), weekly_capacity_min=600, median_gap_days=2.0
    )

    history = [
        # Delivered 6 days ago (inside window)
        Intervention(id="i1", student_id="s1", ts=now - timedelta(days=6), kind="CONCEPT_NUDGE", signals=[], rationale="r", message="Nudge 1", cta="cta", status="pending_followup"),
        # Blocked 5 days ago (message=None; must NOT count)
        Intervention(id="i2", student_id="s1", ts=now - timedelta(days=5), kind="CONCEPT_NUDGE", signals=[], rationale="r", message=None, cta=None, status="pending_followup"),
        # Delivered 3 days ago (inside window)
        Intervention(id="i3", student_id="s1", ts=now - timedelta(days=3), kind="REENGAGE", signals=[], rationale="r", message="Nudge 2", cta="cta", status="pending_followup"),
        # Delivered 8 days ago (outside rolling 7-day window; must NOT count)
        Intervention(id="i4", student_id="s1", ts=now - timedelta(days=8), kind="CONCEPT_NUDGE", signals=[], rationale="r", message="Old Nudge", cta="cta", status="recovered"),
    ]

    # Current candidate intervention
    candidate = Intervention(
        id="cand", student_id="s1", ts=now, kind="REPLAN_UP", signals=[],
        rationale="r", message="Candidate Nudge", cta="cta", status="pending_followup"
    )

    # Only 2 delivered within rolling 7 days; 3rd delivered nudge must pass
    res = apply_guardrails(candidate, student, history=history, now=now)
    assert res.passed is True
    assert res.intervention.message is not None

    # Now add a 3rd delivered nudge inside window
    history.append(
        Intervention(id="i5", student_id="s1", ts=now - timedelta(days=1), kind="REPLAN_DOWN", signals=[], rationale="r", message="Nudge 3", cta="cta", status="pending_followup")
    )
    res_blocked = apply_guardrails(candidate, student, history=history, now=now)
    assert res_blocked.passed is False
    assert res_blocked.intervention.message is None
    assert any("Weekly cap check: BLOCKED" in log for log in res_blocked.intervention.guardrail_log)


def test_adversarial_guardrails_cooldown_second_precision():
    """Verify 24h cooldown precision to the second and kind-specific scope."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    student = Student(
        id="s1", name="CD Student", goal_prompt="Web", roadmap_goal="Web",
        consent_nudges=True, active_hours=(9, 22), weekly_capacity_min=600, median_gap_days=2.0
    )

    # Prior CONCEPT_NUDGE sent 23h 59m 50s ago
    history = [
        Intervention(id="i1", student_id="s1", ts=now - timedelta(hours=23, minutes=59, seconds=50),
                     kind="CONCEPT_NUDGE", signals=[], rationale="r", message="Msg", cta="cta", status="pending_followup")
    ]

    cand_same_kind = Intervention(
        id="c1", student_id="s1", ts=now, kind="CONCEPT_NUDGE", signals=[],
        rationale="r", message="New Msg", cta="cta", status="pending_followup"
    )
    # Duplicate kind within 24h: BLOCKED
    res_same = apply_guardrails(cand_same_kind, student, history=history, now=now)
    assert res_same.passed is False
    assert res_same.intervention.message is None

    # Different kind within 24h: ALLOWED
    cand_diff_kind = Intervention(
        id="c2", student_id="s1", ts=now, kind="REENGAGE", signals=[],
        rationale="r", message="New Msg", cta="cta", status="pending_followup"
    )
    res_diff = apply_guardrails(cand_diff_kind, student, history=history, now=now)
    assert res_diff.passed is True
    assert res_diff.intervention.message is not None


def test_adversarial_guardrails_graded_problem_hint_immunity():
    """Verify graded problems strictly reject HINT_LADDER pre-generation."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    student = Student(
        id="s1", name="Graded Student", goal_prompt="Web", roadmap_goal="Web",
        consent_nudges=True, active_hours=(9, 22), weekly_capacity_min=600, median_gap_days=2.0
    )
    graded_prob = RoadmapItem(
        id="EXAM-1", week=2, title="Final Exam", type="problem", est_minutes=90,
        status="in_progress", graded=True, prereq_concept_ids=[]
    )

    cand_hint = Intervention(
        id="c_hint", student_id="s1", ts=now, kind="HINT_LADDER", signals=[],
        rationale="r", message="Here is a hint", cta="hint", status="pending_followup"
    )
    res = apply_guardrails(cand_hint, student, history=[], now=now, problem=graded_prob)
    assert res.passed is False
    assert res.intervention.message is None
    assert any("Graded-work rule: BLOCKED" in log for log in res.intervention.guardrail_log)


def test_adversarial_post_gen_validation_backtick_and_word_cap():
    """Verify post-generation validation on boundary word counts and syntax restrictions."""
    # Word cap boundaries
    text_60 = "word " * 60
    ok_60, _ = validate_nudge(text_60, is_graded=False)
    assert ok_60 is True

    text_61 = text_60 + "extra"
    ok_61, r_61 = validate_nudge(text_61, is_graded=False)
    assert ok_61 is False
    assert "exceeds maximum of 60 words" in r_61

    # Backtick checks: graded vs practice
    ok_grad_tick, _ = validate_nudge("Check `Array.map` docs", is_graded=True)
    assert ok_grad_tick is False  # Any backtick strictly forbidden on graded

    ok_prac_tick, _ = validate_nudge("Check `Array.map` docs", is_graded=False)
    assert ok_prac_tick is True   # Single backtick permitted on practice

    ok_prac_fence, _ = validate_nudge("Here is code: ```python\nx=1```", is_graded=False)
    assert ok_prac_fence is False # Triple backticks forbidden on all problems

    # Concept index membership check
    ok_concept, _ = validate_nudge("Valid message", concept_id="promises-error-handling")
    assert ok_concept is True

    ok_bad_concept, r_bad = validate_nudge("Valid message", concept_id="non-existent-concept-xyz")
    assert ok_bad_concept is False
    assert "not found in CURATED_CONCEPTS" in r_bad


# =============================================================================
# Group 3: Replanner Topological Invariants & Stress
# =============================================================================

def test_adversarial_replanner_cyclic_dependencies_no_hang():
    """Verify replanner terminates and resolves cyclic prerequisite relationships."""
    item_a = RoadmapItem(id="A", week=1, title="Item A", type="problem", est_minutes=100, status="todo", graded=False, prereq_concept_ids=["B"])
    item_b = RoadmapItem(id="B", week=2, title="Item B", type="problem", est_minutes=100, status="todo", graded=False, prereq_concept_ids=["A"])

    diff = replan_roadmap([item_a, item_b], kind="REPLAN_DOWN", current_week=1)
    applied = apply_roadmap_diff([item_a, item_b], diff)

    # Both items must be reconciled to the same week to satisfy mutual requirement
    assert applied[0].week == applied[1].week
    valid, errors = validate_roadmap_diff([item_a, item_b], diff)
    assert valid is True
    assert errors == []


def test_adversarial_replanner_deep_cascade_chain():
    """Verify multi-level dependency cascades preserve topological order."""
    chain = [
        RoadmapItem(id="A", week=1, title="A", type="concept", est_minutes=40, status="todo", graded=False, prereq_concept_ids=[]),
        RoadmapItem(id="B", week=1, title="B", type="concept", est_minutes=40, status="todo", graded=False, prereq_concept_ids=["A"]),
        RoadmapItem(id="C", week=1, title="C", type="concept", est_minutes=40, status="todo", graded=False, prereq_concept_ids=["B"]),
        RoadmapItem(id="D", week=1, title="D", type="concept", est_minutes=40, status="todo", graded=False, prereq_concept_ids=["C"]),
        RoadmapItem(id="E", week=1, title="E", type="concept", est_minutes=40, status="todo", graded=False, prereq_concept_ids=["D"]),
    ]

    # Shift root item A to week 3
    chain[0].week = 3
    diff = replan_roadmap(chain, kind="REPLAN_DOWN", current_week=1)
    applied = apply_roadmap_diff(chain, diff)

    # Every dependent item must be at week >= 3
    for it in applied:
        assert it.week >= 3
    valid, errors = validate_roadmap_diff(chain, diff)
    assert valid is True


def test_adversarial_replanner_graded_preservation_under_goal_replan():
    """Verify GOAL_REPLAN never drops graded items or completed items."""
    roadmap = [
        RoadmapItem(id="G_TODO", week=1, title="Graded Assessment", type="problem", est_minutes=120, status="todo", graded=True, prereq_concept_ids=[]),
        RoadmapItem(id="P_DONE", week=1, title="Completed Exercise", type="problem", est_minutes=60, status="done", graded=False, prereq_concept_ids=[]),
        RoadmapItem(id="P_TODO", week=1, title="Replaceable Practice", type="problem", est_minutes=60, status="todo", graded=False, prereq_concept_ids=[]),
    ]

    diff = replan_roadmap(roadmap, kind="GOAL_REPLAN", current_week=1, new_goal="backend and distributed systems")
    assert "G_TODO" not in diff.removed
    assert "P_DONE" not in diff.removed
    assert "P_TODO" in diff.removed

    valid, errors = validate_roadmap_diff(roadmap, diff)
    assert valid is True


def test_adversarial_replanner_heavy_capacity_cascading_spillover():
    """Verify cascading capacity spillover across multiple weeks."""
    # 5 heavy items of 250 min = 1250 min in week 1 (capacity: 600 min)
    items = [
        RoadmapItem(id=f"H_{i}", week=1, title=f"Heavy {i}", type="problem", est_minutes=250, status="todo", graded=False, prereq_concept_ids=[])
        for i in range(5)
    ]
    diff = replan_roadmap(items, kind="REPLAN_DOWN", current_week=1, weekly_capacity_min=600)
    applied = apply_roadmap_diff(items, diff)

    # Check weekly loads
    load_w1 = sum(it.est_minutes for it in applied if it.week == 1)
    load_w2 = sum(it.est_minutes for it in applied if it.week == 2)
    load_w3 = sum(it.est_minutes for it in applied if it.week == 3)

    assert load_w1 <= 600
    assert load_w2 <= 600
    assert load_w3 <= 600
    assert load_w1 + load_w2 + load_w3 == 1250

    valid, errors = validate_roadmap_diff(items, diff, weekly_capacity_min=600)
    assert valid is True


# =============================================================================
# Group 4: Follow-up Outcome Evaluation & Escalation Hardening
# =============================================================================

def test_adversarial_followup_pending_before_48h():
    """Verify follow-up checks remain pending_followup when elapsed time < 48h."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    intv = Intervention(
        id="i_f1", student_id="s1", ts=now - timedelta(hours=24),
        followup_due=now + timedelta(hours=24), kind="CONCEPT_NUDGE",
        status="pending_followup", signals=[], rationale="r"
    )

    res = evaluate_followup(intv, events=[], sim_now=now)
    assert res.is_due is False
    assert res.status == "pending_followup"
    assert res.next_kind is None


def test_adversarial_followup_outcome_recovery_matrix():
    """Verify outcome transitions for recovered (pass), recovered (2 sessions), partial (1 session), and no_response."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    base_intv = Intervention(
        id="i_base", student_id="s1", ts=now - timedelta(hours=48),
        followup_due=now - timedelta(hours=1), kind="CONCEPT_NUDGE",
        target_problem_id="P-01", status="pending_followup", signals=[], rationale="r"
    )

    # Case A: Target problem passed
    ev_pass = [Event(student_id="s1", ts=now - timedelta(hours=10), type="attempt_pass", problem_id="P-01")]
    res_pass = evaluate_followup(base_intv, events=ev_pass, sim_now=now)
    assert res_pass.status == "recovered"
    assert res_pass.next_kind == "REPLAN_UP"

    # Case B: 2 study sessions without target pass -> recovered
    ev_2sess = [
        Event(student_id="s1", ts=now - timedelta(hours=20), type="session", duration_min=30),
        Event(student_id="s1", ts=now - timedelta(hours=10), type="session", duration_min=30),
    ]
    res_2sess = evaluate_followup(base_intv, events=ev_2sess, sim_now=now)
    assert res_2sess.status == "recovered"

    # Case C: 1 study session without target pass -> partial
    ev_1sess = [Event(student_id="s1", ts=now - timedelta(hours=10), type="session", duration_min=30)]
    res_1sess = evaluate_followup(base_intv, events=ev_1sess, sim_now=now)
    assert res_1sess.status == "partial"
    assert res_1sess.next_kind == "MICRO_LESSON"

    # Case D: Zero events in 48h -> no_response
    res_no_resp = evaluate_followup(base_intv, events=[], sim_now=now)
    assert res_no_resp.status == "no_response"
    assert res_no_resp.next_kind == "REENGAGE"


def test_adversarial_followup_consecutive_escalation_ladder():
    """Verify escalation ladder progression from first no_response to mentor escalation."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)

    # First no_response intervention
    intv_1 = Intervention(
        id="i_nr1", student_id="s1", ts=now - timedelta(hours=96),
        kind="REENGAGE", status="no_response", signals=[], rationale="r"
    )

    # Second intervention following 48h silence
    intv_2 = Intervention(
        id="i_nr2", student_id="s1", ts=now - timedelta(hours=48),
        followup_due=now - timedelta(hours=1), kind="REENGAGE",
        status="pending_followup", signals=[], rationale="r"
    )

    # Evaluating intv_2 with intv_1 in history triggers consecutive escalation
    res = evaluate_followup(intv_2, events=[], sim_now=now, history=[intv_1, intv_2])
    assert res.status == "escalated"
    assert res.kind == "ESCALATE_MENTOR"
    assert res.next_kind == "ESCALATE_MENTOR"
    assert "Second consecutive no_response" in res.outcome_details


def test_adversarial_followup_cross_student_isolation():
    """Verify that prior interventions of another student do NOT falsely escalate a new student."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)

    # Student A has an old no_response intervention
    intv_student_a = Intervention(
        id="i_std_a", student_id="student_A", ts=now - timedelta(hours=96),
        kind="REENGAGE", status="no_response", signals=[], rationale="r"
    )

    # Student B has a pending_followup intervention that receives 0 events (first no_response)
    intv_student_b = Intervention(
        id="i_std_b", student_id="student_B", ts=now - timedelta(hours=48),
        followup_due=now - timedelta(hours=1), kind="CONCEPT_NUDGE",
        status="pending_followup", signals=[], rationale="r"
    )

    # Mixed history passed containing Student A's no_response
    mixed_history = [intv_student_a, intv_student_b]

    res = evaluate_followup(intv_student_b, events=[], sim_now=now, history=mixed_history)
    # Student B must NOT be escalated to ESCALATE_MENTOR because Student A's history is isolated
    assert res.status == "no_response"
    assert res.next_kind == "REENGAGE"
    assert "No student activity recorded in 48-hour follow-up window" in res.outcome_details


# =============================================================================
# Group 5: REST API Endpoint Robustness & Boundary Validation
# =============================================================================

@pytest.mark.api
def test_adversarial_api_error_schemas_and_invalid_inputs(client: Any):
    """Verify REST API returns proper 404 and 422 error codes on invalid requests."""
    # 404 Not Found on unknown student
    res_get = client.get("/api/students/unknown_student_999")
    assert res_get.status_code == 404

    res_eval = client.post("/api/students/unknown_student_999/evaluate")
    assert res_eval.status_code == 404

    res_ints = client.get("/api/students/unknown_student_999/interventions")
    assert res_ints.status_code == 404

    # 422 Unprocessable Entity on negative sim advance
    res_adv_neg = client.post("/api/sim/advance", json={"days": -1})
    assert res_adv_neg.status_code == 422

    # 422 on string days
    res_adv_str = client.post("/api/sim/advance", json={"days": "two"})
    assert res_adv_str.status_code == 422


@pytest.mark.api
def test_adversarial_sim_advance_days_upper_bound(client: Any):
    """POST /api/sim/advance rejects days > 36500 with HTTP 422 to prevent timedelta overflow."""
    res_huge = client.post("/api/sim/advance", json={"days": 100_000})
    assert res_huge.status_code == 422

    res_billion = client.post("/api/sim/advance", json={"days": 10**9})
    assert res_billion.status_code == 422


@pytest.mark.api
def test_adversarial_api_rapid_evaluation_cooldown_safety(client: Any):
    """Verify that rapid repeated evaluations are safely suppressed by 24h cooldown."""
    client.post("/api/sim/reset")

    # First evaluation succeeds and delivers message
    res1 = client.post("/api/students/aarav/evaluate")
    assert res1.status_code == 200
    int1 = res1.json()["intervention"]
    assert int1["message"] is not None

    # Immediate second evaluation is blocked by cooldown
    res2 = client.post("/api/students/aarav/evaluate")
    assert res2.status_code == 200
    int2 = res2.json()["intervention"]
    assert int2["message"] is None
    assert any("cooldown" in log.lower() for log in int2["guardrail_log"])


@pytest.mark.api
def test_adv_t5_sql_injection_and_special_strings_in_student_ids(client: Any):
    """Path parameter {id} handles SQL injection, script tags, and traversal safely."""
    injection_strings = [
        "' OR '1'='1",
        "'; DROP TABLE students; --",
        "admin'--",
        "../../app/config.py",
        "%00nullbyte",
        "🚀_test_student_🎉",
        "A" * 500,
    ]
    for inj in injection_strings:
        # GET /api/students/{id}
        res_get = client.get(f"/api/students/{inj}")
        assert res_get.status_code in [404, 422], f"GET for {inj!r} failed with {res_get.status_code}"

        # POST /api/students/{id}/evaluate
        res_eval = client.post(f"/api/students/{inj}/evaluate")
        assert res_eval.status_code in [404, 422, 405], f"POST evaluate for {inj!r} failed with {res_eval.status_code}"

        # GET /api/students/{id}/interventions
        res_intv = client.get(f"/api/students/{inj}/interventions")
        assert res_intv.status_code in [404, 422], f"GET interventions for {inj!r} failed with {res_intv.status_code}"


@pytest.mark.api
def test_adv_t5_unicode_emoji_and_injection_in_student_profile(client: Any):
    """System safely handles unicode, emoji, and script tags inside Student models."""
    custom_student = Student(
        id="std-adv-unicode-🚀",
        name="Ananya 👩‍💻 Sharma 🌟 (अनन्या शर्मा)",
        goal_prompt="Mastering 🐍 Python & Rust 🦀; goal with <script>alert('xss')</script> and ' OR '1'='1",
        roadmap_goal="Full-stack AI Track",
        consent_nudges=True,
    )
    store.save_student(custom_student)

    # Verify retrieval
    res_get = client.get("/api/students/std-adv-unicode-🚀")
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["student"]["name"] == custom_student.name

    # Verify evaluation loop runs cleanly
    res_eval = client.post("/api/students/std-adv-unicode-🚀/evaluate")
    assert res_eval.status_code == 200
    eval_data = res_eval.json()
    assert eval_data["intervention"]["student_id"] == "std-adv-unicode-🚀"


@pytest.mark.api
def test_adv_t5_large_payload_goal_prompt_resilience(client: Any):
    """System gracefully evaluates students with 65KB+ goal prompts without crashing."""
    huge_prompt = "Scalable distributed machine learning systems " * 1500
    large_student = Student(
        id="std-adv-large",
        name="Large Payload Student",
        goal_prompt=huge_prompt,
        roadmap_goal="Machine Learning Track",
        consent_nudges=True,
    )
    store.save_student(large_student)

    res = client.post("/api/students/std-adv-large/evaluate")
    assert res.status_code == 200
    assert res.json()["intervention"]["id"] is not None


@pytest.mark.api
def test_adv_t5_unsupported_http_methods(client: Any):
    """Unsupported HTTP methods on REST endpoints return 405 Method Not Allowed."""
    assert client.delete("/api/students/aarav").status_code == 405
    assert client.put("/api/sim/advance").status_code == 405
    assert client.patch("/api/mode").status_code == 405


# =============================================================================
# Group 6: Store, Simulation Clock & Concurrency Stress
# =============================================================================

@pytest.mark.unit
def test_adv_t5_clock_negative_and_fractional_advancement_rejected(client: Any):
    """Simulation clock rejects negative and fractional days."""
    res_neg = client.post("/api/sim/advance", json={"days": -5})
    assert res_neg.status_code == 422

    res_frac = client.post("/api/sim/advance", json={"days": 1.25})
    assert res_frac.status_code == 422

    # Direct function call verification
    with pytest.raises(ValueError, match="Days must be non-negative"):
        advance_clock(store, -2)


@pytest.mark.unit
def test_adv_t5_clock_zero_days_idempotence(client: Any):
    """POST /api/sim/advance with days=0 leaves clock and state unchanged."""
    time_before = client.get("/api/mode").json()["sim_now"]
    res = client.post("/api/sim/advance", json={"days": 0})
    assert res.status_code == 200
    data = res.json()
    assert data["days_advanced"] == 0
    assert data["events_generated"] == 0
    assert data["sim_now"] == time_before


@pytest.mark.unit
def test_adv_t5_clock_long_range_simulation_150_days(client: Any):
    """Simulating 150+ days into the future maintains temporal and metrics integrity."""
    client.post("/api/sim/reset")
    base_iso = client.get("/api/mode").json()["sim_now"]
    base_dt = datetime.fromisoformat(base_iso)

    # Advance 150 days
    res = client.post("/api/sim/advance", json={"days": 150})
    assert res.status_code == 200
    now_dt = datetime.fromisoformat(res.json()["sim_now"])
    assert (now_dt - base_dt).days == 150

    # Past 30 days window expires initial seed events cleanly
    res_aarav = client.get("/api/students/aarav")
    assert res_aarav.status_code == 200
    assert len(res_aarav.json()["events"]) == 0

    # Cohort and metrics endpoints remain fully functional
    res_cohort = client.get("/api/cohort")
    assert res_cohort.status_code == 200
    assert len(res_cohort.json()["at_risk_students"]) > 0

    res_metrics = client.get("/api/metrics")
    assert res_metrics.status_code == 200
    metrics = res_metrics.json()
    assert 0.0 <= metrics["recovery_rate"] <= 1.0


@pytest.mark.unit
def test_adv_t5_simulation_reset_mid_timeline(client: Any):
    """POST /api/sim/reset cleanly restores baseline clock and student data."""
    # Mutate state
    client.post("/api/sim/advance", json={"days": 60})
    client.post("/api/students/aarav/evaluate")

    # Reset
    res_reset = client.post("/api/sim/reset")
    assert res_reset.status_code == 200
    assert res_reset.json()["status"] == "ok"

    # Verify baseline clock restored
    mode = client.get("/api/mode").json()
    assert "2026-11-18T10:00:00" in mode["sim_now"]

    # Verify seed events restored
    res_aarav = client.get("/api/students/aarav")
    assert len(res_aarav.json()["events"]) > 0


@pytest.mark.unit
def test_adv_t5_extreme_365_day_advancement(client: Any):
    """Advancing by a full year (365 days) computes cleanly across UTC boundaries."""
    client.post("/api/sim/reset")
    base_year = datetime.fromisoformat(client.get("/api/mode").json()["sim_now"]).year
    res = client.post("/api/sim/advance", json={"days": 365})
    assert res.status_code == 200
    new_year = datetime.fromisoformat(res.json()["sim_now"]).year
    assert new_year == base_year + 1


@pytest.mark.unit
def test_adv_t5_store_concurrent_multithreaded_read_write():
    """Store RLock prevents race conditions during 750 concurrent multithreaded operations."""
    custom_store = Store(auto_seed=True)
    thread_errors = []

    def worker(th_id: int):
        try:
            for i in range(50):
                op = i % 5
                if op == 0:
                    s = Student(id=f"std-{th_id}", name=f"Name {th_id}", goal_prompt="G", roadmap_goal="R")
                    custom_store.save_student(s)
                elif op == 1:
                    ev = Event(student_id=f"std-{th_id}", ts=custom_store.get_sim_now(), type="session", duration_min=15)
                    custom_store.add_event(ev)
                elif op == 2:
                    custom_store.get_students()
                    custom_store.get_events(f"std-{th_id}")
                elif op == 3:
                    intv = Intervention(id=f"i-{th_id}-{i}", student_id=f"std-{th_id}", ts=custom_store.get_sim_now(), kind="CONCEPT_NUDGE", rationale="R")
                    custom_store.save_intervention(intv)
                elif op == 4:
                    custom_store.get_all_interventions()
        except Exception as exc:
            thread_errors.append((th_id, exc))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(thread_errors) == 0, f"Concurrent store errors: {thread_errors}"


@pytest.mark.unit
def test_adv_t5_api_concurrent_requests_under_load(client: Any):
    """API endpoints remain responsive and error-free under 200 concurrent HTTP requests."""
    api_errors = []

    def api_worker(th_id: int):
        endpoints = [
            ("GET", "/api/students"),
            ("GET", "/api/students/aarav"),
            ("GET", "/api/cohort"),
            ("GET", "/api/metrics"),
            ("POST", "/api/students/aarav/evaluate", None),
            ("POST", "/api/students/meera/evaluate", None),
            ("POST", "/api/sim/advance", {"days": 1}),
            ("GET", "/api/mode"),
            ("POST", "/api/sim/reset", None),
        ]
        try:
            for i in range(20):
                method, path, *payload = endpoints[i % len(endpoints)]
                body = payload[0] if payload else None
                if method == "GET":
                    res = client.get(path)
                else:
                    res = client.post(path, json=body if body is not None else {})
                if res.status_code not in [200, 422]:
                    api_errors.append((th_id, path, res.status_code))
        except Exception as exc:
            api_errors.append((th_id, "exception", str(exc)))

    threads = [threading.Thread(target=api_worker, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(api_errors) == 0, f"API concurrency errors: {api_errors}"


# =============================================================================
# Group 7: Multi-Student Cohort Metrics & Security
# =============================================================================

@pytest.mark.unit
def test_adv_t5_metrics_zero_state_denominator_safety(client: Any):
    """GET /api/metrics safely calculates zero state with 0 total interventions."""
    client.post("/api/sim/reset")
    res = client.get("/api/metrics")
    assert res.status_code == 200
    m = res.json()
    assert m["recovery_rate"] == 0.0
    assert m["avg_hours_to_recover"] == 0.0
    assert m["nudges_sent"] == 0
    assert m["nudges_blocked"] == 0
    assert m["escalations"] == 0


@pytest.mark.unit
def test_adv_t5_metrics_zero_percent_recovery_with_interventions(client: Any):
    """GET /api/metrics accurately reflects 0% recovery when interventions exist but none recovered."""
    client.post("/api/sim/reset")
    now = store.get_sim_now()
    store.save_intervention(Intervention(
        id="i-p1", student_id="aarav", ts=now, kind="CONCEPT_NUDGE",
        rationale="r1", message="m1", status="pending_followup",
    ))
    store.save_intervention(Intervention(
        id="i-p2", student_id="meera", ts=now - timedelta(days=2), kind="REENGAGE",
        rationale="r2", message="m2", status="partial", outcome_ts=now,
    ))
    store.save_intervention(Intervention(
        id="i-p3", student_id="rohan", ts=now - timedelta(days=4), kind="ESCALATE_MENTOR",
        rationale="r3", message=None, status="escalated",
    ))
    store.save_intervention(Intervention(
        id="i-p4", student_id="priya", ts=now, kind="CONCEPT_NUDGE",
        rationale="r4", message=None, guardrail_log=["[Pre-gen] Consent check: FAILED."],
        status="pending_followup",
    ))

    res = client.get("/api/metrics")
    assert res.status_code == 200
    m = res.json()
    assert m["recovery_rate"] == 0.0
    assert m["avg_hours_to_recover"] == 0.0
    assert m["nudges_sent"] == 2
    assert m["nudges_blocked"] == 1
    assert m["escalations"] == 1


@pytest.mark.unit
def test_adv_t5_metrics_100_percent_recovery_and_avg_duration(client: Any):
    """GET /api/metrics accurately calculates 100% recovery rate and exact average hours."""
    client.post("/api/sim/reset")
    now = store.get_sim_now()
    store.save_intervention(Intervention(
        id="i-r1", student_id="aarav", ts=now - timedelta(hours=20), kind="CONCEPT_NUDGE",
        rationale="r", message="m", status="recovered", outcome_ts=now,
    ))
    store.save_intervention(Intervention(
        id="i-r2", student_id="aarav", ts=now - timedelta(hours=40), kind="CONCEPT_NUDGE",
        rationale="r", message="m", status="recovered", outcome_ts=now,
    ))

    res = client.get("/api/metrics")
    assert res.status_code == 200
    m = res.json()
    assert m["recovery_rate"] == 1.0
    assert m["avg_hours_to_recover"] == 30.0  # (20 + 40) / 2
    assert m["nudges_sent"] == 2
    assert m["nudges_blocked"] == 0


@pytest.mark.unit
def test_adv_t5_metrics_clock_skew_negative_duration_resilience(client: Any):
    """Corrupted interventions with outcome_ts < ts are excluded from avg recovery hours."""
    client.post("/api/sim/reset")
    now = store.get_sim_now()
    # Valid recovered intervention: 50h
    store.save_intervention(Intervention(
        id="i-valid", student_id="aarav", ts=now - timedelta(hours=50), kind="CONCEPT_NUDGE",
        rationale="r", message="m", status="recovered", outcome_ts=now,
    ))
    # Clock skewed intervention: outcome_ts 10h before ts
    store.save_intervention(Intervention(
        id="i-skew", student_id="aarav", ts=now, kind="CONCEPT_NUDGE",
        rationale="skew", message="m", status="recovered", outcome_ts=now - timedelta(hours=10),
    ))

    res = client.get("/api/metrics")
    assert res.status_code == 200
    m = res.json()
    assert m["recovery_rate"] == 1.0
    assert m["avg_hours_to_recover"] == 50.0  # Skewed duration excluded


@pytest.mark.unit
def test_adv_t5_cohort_at_risk_students_deduplication(client: Any):
    """GET /api/cohort contains at most one entry per enrolled student."""
    client.post("/api/sim/reset")
    # Trigger evaluations and add multiple interventions
    client.post("/api/students/aarav/evaluate")
    client.post("/api/students/meera/evaluate")

    res = client.get("/api/cohort")
    assert res.status_code == 200
    at_risk = res.json()["at_risk_students"]
    student_ids = [s["id"] for s in at_risk]
    assert len(student_ids) == len(set(student_ids)), "Duplicate student detected in cohort view"


@pytest.mark.unit
def test_adv_t5_static_directory_path_traversal_blocked(client: Any):
    """Static mount strictly blocks directory traversal attempts escaping static root."""
    traversal_attacks = [
        "/static/../../app/config.py",
        "/static/..%2f..%2fapp%2fconfig.py",
        "/static/%2e%2e/%2e%2e/app/config.py",
        "/static/../requirements.txt",
        "/static/../../.env.example",
        "/static/..\\..\\app\\config.py",
        "/static/..%5c..%5capp%5cconfig.py",
        "/../../app/config.py",
        "/%2e%2e/app/config.py",
        "/..%2f..%2fapp%2fconfig.py",
    ]
    for attack in traversal_attacks:
        res = client.get(attack)
        assert res.status_code in [400, 404], f"Path traversal {attack} should be blocked, got {res.status_code}"
        # Ensure no application source code leaked
        assert "class Student" not in res.text
        assert "ANTHROPIC_API_KEY" not in res.text


@pytest.mark.unit
def test_adv_t5_static_and_api_content_type_headers(client: Any):
    """Verifies standard Content-Type headers for HTML, CSS, JavaScript, and JSON."""
    type_checks = [
        ("/", "text/html"),
        ("/index.html", "text/html"),
        ("/static/index.html", "text/html"),
        ("/static/style.css", "text/css"),
        ("/static/app.js", ["application/javascript", "text/javascript"]),
        ("/api/mode", "application/json"),
        ("/api/cohort", "application/json"),
        ("/api/metrics", "application/json"),
    ]
    for path, expected in type_checks:
        res = client.get(path)
        assert res.status_code == 200, f"Path {path} returned status {res.status_code}"
        ct = res.headers.get("content-type", "")
        if isinstance(expected, list):
            assert any(exp in ct for exp in expected), f"Content-Type {ct} does not match any of {expected}"
        else:
            assert expected in ct, f"Content-Type {ct} does not match expected {expected}"
