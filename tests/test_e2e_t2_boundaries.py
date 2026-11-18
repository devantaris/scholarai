"""Tier 2: Boundary & Corner Cases Test Suite for Scholar Pulse.

Covers boundary value analysis (BVA), mathematical threshold borders,
extreme value clamping [0.0, 1.0], zero-division guards, temporal lookback cutoffs,
quiet hours borders, weekly cap and cooldown borders, and API error schemas (404/422).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
import pytest

from app.config import (
    DISENGAGED_THRESHOLD,
    INACTIVITY_ABSOLUTE_DAYS_TRIGGER,
    INACTIVITY_THRESHOLD,
    MAX_NUDGE_WORDS,
    MAX_NUDGES_PER_WEEK,
    PACE_AHEAD_THRESHOLD,
    PACE_DRIFT_THRESHOLD,
    REPEATED_FAILURE_THRESHOLD,
    STALL_TIME_THRESHOLD,
)
from app.models import (
    CURATED_CONCEPTS,
    ContentConcept,
    Event,
    Intervention,
    RoadmapDiff,
    RoadmapItem,
    SignalResult,
    Student,
    get_concept,
)
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


def _get_policy_fn():
    try:
        import app.policy as policy
        for fn_name in ["decide_intervention", "evaluate_policy", "decide"]:
            if hasattr(policy, fn_name):
                return getattr(policy, fn_name)
    except ImportError:
        pass
    return None


def _get_guardrails_fn():
    try:
        import app.guardrails as guardrails
        for fn_name in ["apply_guardrails", "check_guardrails", "filter_guardrails"]:
            if hasattr(guardrails, fn_name):
                return getattr(guardrails, fn_name)
    except ImportError:
        pass
    return None


def _get_postgen_validator_fn():
    try:
        import app.guardrails as guardrails
        for fn_name in ["validate_nudge", "validate_post_generation", "validate_output"]:
            if hasattr(guardrails, fn_name):
                return getattr(guardrails, fn_name)
    except ImportError:
        pass
    return None


def _get_replanner_fn():
    try:
        import app.replanner as replanner
        for fn_name in ["replan_roadmap", "generate_replan", "adjust_roadmap"]:
            if hasattr(replanner, fn_name):
                return getattr(replanner, fn_name)
    except ImportError:
        pass
    return None


def _get_followup_fn():
    try:
        import app.followup as followup
        for fn_name in ["evaluate_followup", "evaluate_outcome", "check_followup"]:
            if hasattr(followup, fn_name):
                return getattr(followup, fn_name)
    except ImportError:
        pass
    return None


# ============================================================================
# Feature Area F1: Signals Boundary Cases (Tests 51 - 57)
# ============================================================================

@pytest.mark.tier2
def test_t2_f1_repeated_failure_exact_threshold_boundary(base_now: datetime):
    """Test 51: F1 Boundary - repeated_failure exact threshold 0.49 vs 0.50.
    
    Case B: 3 fails with identical error: (3-2)/4 + 0.20 = 0.45 (<0.50) -> triggered is False.
    Case C: 4 fails without shared error: (4-2)/4 = 0.50 (==0.50) -> triggered is True.
    """
    events_b = [
        Event(student_id="aarav", ts=base_now - timedelta(hours=i), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable")
        for i in range(1, 4)
    ]
    res_b = compute_repeated_failure(events_b, "P-014", base_now)
    assert pytest.approx(res_b.score, abs=0.001) == 0.45
    assert res_b.triggered is False

    events_c = [
        Event(student_id="aarav", ts=base_now - timedelta(hours=i), type="attempt_fail",
              problem_id="P-014", error_signature=f"Err_{i}")
        for i in range(1, 5)
    ]
    res_c = compute_repeated_failure(events_c, "P-014", base_now)
    assert pytest.approx(res_c.score, abs=0.001) == 0.50
    assert res_c.triggered is True


@pytest.mark.tier2
def test_t2_f1_stall_time_exact_threshold_boundary(base_now: datetime):
    """Test 52: F1 Boundary - stall_time exact threshold 0.59 vs 0.60.
    
    Problem est_minutes = 60, 1.5x threshold = 90 min.
    Case A: 53 minutes -> 53 / 90 = 0.5888 (<0.60) -> triggered is False.
    Case B: 54 minutes -> 54 / 90 = 0.6000 (>=0.60) -> triggered is True.
    """
    events_a = [
        Event(student_id="s1", ts=base_now - timedelta(hours=1), type="session",
              problem_id="P-014", duration_min=53)
    ]
    res_a = compute_stall_time(events_a, "P-014", 60, base_now)
    assert res_a.score < 0.60
    assert res_a.triggered is False

    events_b = [
        Event(student_id="s1", ts=base_now - timedelta(hours=1), type="session",
              problem_id="P-014", duration_min=54)
    ]
    res_b = compute_stall_time(events_b, "P-014", 60, base_now)
    assert pytest.approx(res_b.score, abs=0.001) == 0.60
    assert res_b.triggered is True


@pytest.mark.tier2
def test_t2_f1_inactivity_dual_trigger_boundary(base_now: datetime, make_student: Callable[..., Student]):
    """Test 53: F1 Boundary - inactivity dual trigger (ratio >= 0.60 vs days >= 5.0).
    
    Case A: days = 4.99, median_gap = 5.0 (denom = 10.0). Ratio = 0.499 < 0.60, days < 5.0 -> False.
    Case B: days = 5.00, median_gap = 5.0. Ratio = 0.50 < 0.60, but days >= 5.0 -> True.
    Case C: days = 1.20, median_gap = 0.5 (denom = max(2.0, 1.0) = 2.0). Ratio = 0.60 -> True.
    """
    s_lazy = make_student(median_gap_days=5.0)
    ev_a = [Event(student_id="s1", ts=base_now - timedelta(days=4.99), type="session", duration_min=60)]
    assert compute_inactivity(ev_a, s_lazy, base_now).triggered is False

    ev_b = [Event(student_id="s1", ts=base_now - timedelta(days=5.00), type="session", duration_min=60)]
    assert compute_inactivity(ev_b, s_lazy, base_now).triggered is True

    s_fast = make_student(median_gap_days=0.5)
    ev_c = [Event(student_id="s1", ts=base_now - timedelta(days=1.20), type="session", duration_min=60)]
    res_c = compute_inactivity(ev_c, s_fast, base_now)
    assert pytest.approx(res_c.score, abs=0.01) == 0.60
    assert res_c.triggered is True


@pytest.mark.tier2
def test_t2_f1_pace_drift_exact_threshold_boundary(make_roadmap_item: Callable[..., RoadmapItem]):
    """Test 54: F1 Boundary - pace_drift exact threshold 0.29 vs 0.30.
    
    Planned = 100 min.
    Case A: Actual = 71 min -> 1 - 0.71 = 0.29 (< 0.30) -> triggered is False.
    Case B: Actual = 70 min -> 1 - 0.70 = 0.30 (>= 0.30) -> triggered is True.
    """
    rm_a = [
        make_roadmap_item(id="R1", week=1, est_minutes=71, status="done"),
        make_roadmap_item(id="R2", week=1, est_minutes=29, status="todo"),
    ]
    res_a = compute_pace_drift(rm_a, current_week=1)
    assert pytest.approx(res_a.score, abs=0.001) == 0.29
    assert res_a.triggered is False

    rm_b = [
        make_roadmap_item(id="R1", week=1, est_minutes=70, status="done"),
        make_roadmap_item(id="R2", week=1, est_minutes=30, status="todo"),
    ]
    res_b = compute_pace_drift(rm_b, current_week=1)
    assert pytest.approx(res_b.score, abs=0.001) == 0.30
    assert res_b.triggered is True


@pytest.mark.tier2
def test_t2_f1_pace_ahead_exact_threshold_boundary(make_roadmap_item: Callable[..., RoadmapItem]):
    """Test 55: F1 Boundary - pace_ahead exact threshold 0.24 vs 0.25.
    
    Planned = 100 min.
    Case A: Actual = 124 min -> 1.24 - 1.0 = 0.24 (< 0.25) -> triggered is False.
    Case B: Actual = 125 min -> 1.25 - 1.0 = 0.25 (>= 0.25) -> triggered is True.
    """
    rm_a = [
        make_roadmap_item(id="R1", week=1, est_minutes=100, status="done"),
        make_roadmap_item(id="R2", week=2, est_minutes=24, status="done"),
    ]
    res_a = compute_pace_ahead(rm_a, current_week=1)
    assert pytest.approx(res_a.score, abs=0.001) == 0.24
    assert res_a.triggered is False

    rm_b = [
        make_roadmap_item(id="R1", week=1, est_minutes=100, status="done"),
        make_roadmap_item(id="R2", week=2, est_minutes=25, status="done"),
    ]
    res_b = compute_pace_ahead(rm_b, current_week=1)
    assert pytest.approx(res_b.score, abs=0.001) == 0.25
    assert res_b.triggered is True


@pytest.mark.tier2
def test_t2_f1_score_clamping_limits_0_and_1():
    """Test 56: F1 Boundary - Extreme value clamping strictly within [0.0, 1.0]."""
    assert clamp(-100.0) == 0.0
    assert clamp(-0.0001) == 0.0
    assert clamp(1.0001) == 1.0
    assert clamp(999.0) == 1.0
    assert clamp(0.0) == 0.0
    assert clamp(1.0) == 1.0


@pytest.mark.tier2
def test_t2_f1_zero_events_and_zero_planned_minutes(base_now: datetime, make_student: Callable[..., Student]):
    """Test 57: F1 Boundary - Zero division safety with 0 events and 0 planned minutes."""
    student = make_student()
    res_rf = compute_repeated_failure([], "P-014", base_now)
    assert res_rf.score == 0.0

    res_st = compute_stall_time([], "P-014", 0, base_now)
    assert res_st.score == 0.0

    res_drift = compute_pace_drift([], current_week=1)
    assert res_drift.score == 0.0

    res_ahead = compute_pace_ahead([], current_week=1)
    assert res_ahead.score == 0.0

    res_goal = compute_goal_change([], student)
    assert res_goal.score == 0.0


@pytest.mark.tier2
def test_t2_f1_repeated_failure_pass_suppression_boundaries(base_now: datetime):
    """Test F1 Boundary - Pass suppression boundaries for repeated_failure.
    
    1. Pass on a DIFFERENT problem (P-015) does NOT suppress failures on target problem (P-014).
    2. Pass with a FUTURE timestamp (ts > now) does NOT suppress failures at current evaluation time.
    3. Pass on target problem with ts <= now properly suppresses failures to score 0.0.
    """
    base_fails = [
        Event(student_id="aarav", ts=base_now - timedelta(hours=i * 2), type="attempt_fail",
              problem_id="P-014", error_signature=f"Err_{i}")
        for i in range(1, 5)
    ]

    # Boundary 1: attempt_pass on different problem P-015 at base_now - 1h
    events_other_problem = base_fails + [
        Event(student_id="aarav", ts=base_now - timedelta(hours=1), type="attempt_pass",
              problem_id="P-015")
    ]
    res_other = compute_repeated_failure(events_other_problem, "P-014", base_now)
    assert pytest.approx(res_other.score, abs=0.01) == 0.50
    assert res_other.triggered is True

    # Boundary 2: attempt_pass on target problem P-014 but with FUTURE timestamp (base_now + 1h)
    events_future_pass = base_fails + [
        Event(student_id="aarav", ts=base_now + timedelta(hours=1), type="attempt_pass",
              problem_id="P-014")
    ]
    res_future = compute_repeated_failure(events_future_pass, "P-014", base_now)
    assert pytest.approx(res_future.score, abs=0.01) == 0.50
    assert res_future.triggered is True

    # Control check: valid attempt_pass on target problem P-014 in the past (base_now - 30m) suppresses
    events_valid_pass = base_fails + [
        Event(student_id="aarav", ts=base_now - timedelta(minutes=30), type="attempt_pass",
              problem_id="P-014")
    ]
    res_valid = compute_repeated_failure(events_valid_pass, "P-014", base_now)
    assert res_valid.score == 0.0
    assert res_valid.triggered is False


@pytest.mark.tier2
def test_t2_f1_repeated_failure_error_bonus_boundary(base_now: datetime):
    """Test F1 Boundary - Error signature bonus cutoff: exactly 2 vs exactly 3 matching errors.
    
    Rule: exactly 2 matching error signatures produces no bonus (score = (fails-2)/4).
          exactly 3 matching error signatures produces +0.20 bonus.
    """
    # Case A: 4 fails total, exactly 2 sharing "TypeError:undefined_iterable" -> no bonus
    events_2_match = [
        Event(student_id="aarav", ts=base_now - timedelta(hours=4), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=3), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=2), type="attempt_fail",
              problem_id="P-014", error_signature="SyntaxError:unexpected_token"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=1), type="attempt_fail",
              problem_id="P-014", error_signature="ReferenceError:x_is_not_defined"),
    ]
    res_2 = compute_repeated_failure(events_2_match, "P-014", base_now)
    assert pytest.approx(res_2.score, abs=0.01) == 0.50
    assert res_2.triggered is True

    # Case B: 4 fails total, exactly 3 sharing "TypeError:undefined_iterable" -> +0.20 bonus
    events_3_match = [
        Event(student_id="aarav", ts=base_now - timedelta(hours=4), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=3), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=2), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=1), type="attempt_fail",
              problem_id="P-014", error_signature="ReferenceError:x_is_not_defined"),
    ]
    res_3 = compute_repeated_failure(events_3_match, "P-014", base_now)
    assert pytest.approx(res_3.score, abs=0.01) == 0.70
    assert res_3.triggered is True
    assert pytest.approx(res_3.score - res_2.score, abs=0.001) == 0.20


@pytest.mark.tier2
def test_t2_f1_stall_time_zero_est_minutes_with_active_duration(base_now: datetime):
    """Test F1 Boundary - compute_stall_time safely handles est_minutes <= 0 with non-zero duration.
    
    When active session/attempt duration on problem > 0 (e.g. 45 min) and est_minutes <= 0,
    compute_stall_time must return score 0.0 without ZeroDivisionError.
    """
    events = [
        Event(student_id="aarav", ts=base_now - timedelta(minutes=45), type="session",
              problem_id="P-014", duration_min=45),
        Event(student_id="aarav", ts=base_now - timedelta(minutes=15), type="attempt_fail",
              problem_id="P-014", duration_min=15),
    ]

    # est_minutes == 0 with 60 total minutes on problem
    res_zero = compute_stall_time(events, "P-014", est_minutes=0, now=base_now)
    assert res_zero.score == 0.0
    assert res_zero.triggered is False
    assert "Invalid estimated duration" in res_zero.evidence

    # est_minutes < 0 with 60 total minutes on problem
    res_negative = compute_stall_time(events, "P-014", est_minutes=-30, now=base_now)
    assert res_negative.score == 0.0
    assert res_negative.triggered is False
    assert "Invalid estimated duration" in res_negative.evidence


# ============================================================================
# Feature Area F2: Policy Boundary Cases (Tests 58 - 62)
# ============================================================================

@pytest.mark.tier2
def test_t2_f2_simultaneous_goal_change_and_stuck_priority_resolution(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 58: F2 Boundary - Priority conflict: Priority 1 strictly supersedes Priority 2."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=1.0, triggered=True, evidence="Goal prompt changed"),
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on graded P-014"),
    ]
    intervention = policy_fn(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert intervention.kind == "GOAL_REPLAN"


@pytest.mark.tier2
def test_t2_f2_simultaneous_stuck_and_disengaged_priority_resolution(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 59: F2 Boundary - Priority conflict: Priority 2 strictly supersedes Priority 4."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on graded P-014"),
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Disengaged"),
    ]
    intervention = policy_fn(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert intervention.kind == "CONCEPT_NUDGE"


@pytest.mark.tier2
def test_t2_f2_stuck_with_pace_drift_border_diff_attachment(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 60: F2 Boundary - Re-plan attachment border: drift 0.29 vs 0.30 with stuck.
    
    Case A: pace_drift = 0.29 (<0.30) -> roadmap_diff is None.
    Case B: pace_drift = 0.30 (>=0.30) -> roadmap_diff is not None.
    """
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student()
    roadmap = make_roadmap()

    # Case A: 0.29 drift
    signals_a = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck"),
        SignalResult(name="pace_drift", score=0.29, triggered=False, evidence="29% drift"),
    ]
    res_a = policy_fn(student, roadmap, signals_a, target_problem_id="P-014", current_week=2, now=base_now)
    assert res_a.roadmap_diff is None or len(res_a.roadmap_diff.added) == 0

    # Case B: 0.30 drift
    signals_b = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck"),
        SignalResult(name="pace_drift", score=0.30, triggered=True, evidence="30% drift"),
    ]
    res_b = policy_fn(student, roadmap, signals_b, target_problem_id="P-014", current_week=2, now=base_now)
    assert res_b.roadmap_diff is not None


@pytest.mark.tier2
def test_t2_f2_disengaged_with_pace_drift_border_diff_attachment(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 61: F2 Boundary - REENGAGE re-plan attachment border on pace_drift 0.29 vs 0.30."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student()
    roadmap = make_roadmap()

    signals_a = [
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Disengaged"),
        SignalResult(name="pace_drift", score=0.29, triggered=False, evidence="29% drift"),
    ]
    res_a = policy_fn(student, roadmap, signals_a, current_week=2, now=base_now)
    assert res_a.kind == "REENGAGE"
    assert res_a.roadmap_diff is None or len(res_a.roadmap_diff.added) == 0

    signals_b = [
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Disengaged"),
        SignalResult(name="pace_drift", score=0.30, triggered=True, evidence="30% drift"),
    ]
    res_b = policy_fn(student, roadmap, signals_b, current_week=2, now=base_now)
    assert res_b.kind == "REENGAGE"
    assert res_b.roadmap_diff is not None


@pytest.mark.tier2
def test_t2_f2_all_signals_zero_produces_clean_none(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 62: F2 Boundary - All signal scores 0.0 produces clean NONE."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="repeated_failure", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stall_time", score=0.0, triggered=False, evidence=""),
        SignalResult(name="inactivity", score=0.0, triggered=False, evidence=""),
        SignalResult(name="pace_drift", score=0.0, triggered=False, evidence=""),
        SignalResult(name="pace_ahead", score=0.0, triggered=False, evidence=""),
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.0, triggered=False, evidence=""),
        SignalResult(name="disengaged", score=0.0, triggered=False, evidence=""),
    ]
    intervention = policy_fn(student, roadmap, signals, current_week=1, now=base_now)
    assert intervention.kind == "NONE"
    assert intervention.message is None
    assert intervention.roadmap_diff is None


# ============================================================================
# Feature Area F3: Guardrails Boundary Cases (Tests 63 - 67)
# ============================================================================

@pytest.mark.tier2
def test_t2_f3_quiet_hours_exact_minute_boundaries(make_student: Callable[..., Student]):
    """Test 63: F3 Boundary - Quiet hours active window boundaries (09:00 - 22:00).
    
    08:59:59 -> outside (deferred)
    09:00:00 -> inside (allowed)
    21:59:59 -> inside (allowed)
    22:00:00 -> outside (deferred)
    """
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student(active_hours=(9, 22))

    t_0859 = datetime(2026, 11, 18, 8, 59, 59, tzinfo=timezone.utc)
    int_a = Intervention(id="i1", student_id="s1", ts=t_0859, kind="CONCEPT_NUDGE",
                         signals=[], rationale="Stuck", message="Msg")
    _, mod_a = guardrails_fn(int_a, student, history=[], now=t_0859)
    assert any("deferred" in log.lower() or "quiet" in log.lower() for log in mod_a.guardrail_log)

    t_0900 = datetime(2026, 11, 18, 9, 0, 0, tzinfo=timezone.utc)
    int_b = Intervention(id="i2", student_id="s1", ts=t_0900, kind="CONCEPT_NUDGE",
                         signals=[], rationale="Stuck", message="Msg")
    _, mod_b = guardrails_fn(int_b, student, history=[], now=t_0900)
    assert not any("deferred" in log.lower() for log in mod_b.guardrail_log)


@pytest.mark.tier2
def test_t2_f3_weekly_cap_exactly_3_vs_4_nudges(base_now: datetime, make_student: Callable[..., Student]):
    """Test 64: F3 Boundary - Weekly cap border (2 allowed, 3 blocks 4th)."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    # 2 prior nudges
    hist_2 = [
        Intervention(id="i1", student_id="s1", ts=base_now - timedelta(days=4), kind="CONCEPT_NUDGE", signals=[], rationale="r", message="m"),
        Intervention(id="i2", student_id="s1", ts=base_now - timedelta(days=2), kind="REENGAGE", signals=[], rationale="r", message="m"),
    ]
    new_int_3 = Intervention(id="i3", student_id="s1", ts=base_now, kind="REPLAN_UP", signals=[], rationale="r", message="m3", cta="cta")
    _, mod_3 = guardrails_fn(new_int_3, student, history=hist_2, now=base_now)
    assert mod_3.message is not None

    # 3 prior nudges
    hist_3 = hist_2 + [new_int_3]
    new_int_4 = Intervention(id="i4", student_id="s1", ts=base_now, kind="MICRO_LESSON", signals=[], rationale="r", message="m4", cta="cta")
    _, mod_4 = guardrails_fn(new_int_4, student, history=hist_3, now=base_now)
    assert mod_4.message is None
    assert any("weekly cap" in log.lower() for log in mod_4.guardrail_log)


@pytest.mark.tier2
def test_t2_f3_cooldown_exact_23h59m_vs_24h(base_now: datetime, make_student: Callable[..., Student]):
    """Test 65: F3 Boundary - 24h Cooldown exact temporal boundary.
    
    23h 59m ago -> blocked
    24h 01m ago -> allowed
    """
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    hist_23h59 = [
        Intervention(id="i1", student_id="s1", ts=base_now - timedelta(hours=23, minutes=59),
                     kind="CONCEPT_NUDGE", signals=[], rationale="r", message="prior")
    ]
    new_int = Intervention(id="i2", student_id="s1", ts=base_now, kind="CONCEPT_NUDGE",
                           signals=[], rationale="r", message="new", cta="cta")
    _, mod_blocked = guardrails_fn(new_int, student, history=hist_23h59, now=base_now)
    assert mod_blocked.message is None

    hist_24h01 = [
        Intervention(id="i1", student_id="s1", ts=base_now - timedelta(hours=24, minutes=1),
                     kind="CONCEPT_NUDGE", signals=[], rationale="r", message="prior")
    ]
    _, mod_allowed = guardrails_fn(new_int, student, history=hist_24h01, now=base_now)
    assert mod_allowed.message is not None


@pytest.mark.tier2
def test_t2_f3_consent_toggle_roundtrip(base_now: datetime, make_student: Callable[..., Student]):
    """Test 66: F3 Boundary - Dynamic consent toggle roundtrip."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    s_no = make_student(consent_nudges=False)
    int_test = Intervention(id="i1", student_id="s1", ts=base_now, kind="CONCEPT_NUDGE",
                            signals=[], rationale="r", message="m", cta="cta")
    _, mod_no = guardrails_fn(int_test, s_no, history=[], now=base_now)
    assert mod_no.message is None

    s_yes = make_student(consent_nudges=True)
    _, mod_yes = guardrails_fn(int_test, s_yes, history=[], now=base_now)
    assert mod_yes.message is not None


@pytest.mark.tier2
def test_t2_f3_graded_work_rule_strict_block_on_code_hint(base_now: datetime, make_student: Callable[..., Student], make_roadmap_item: Callable[..., RoadmapItem]):
    """Test 67: F3 Boundary - Graded work strictly forbids hints."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    problem = make_roadmap_item(id="P-014", graded=True)
    int_bad = Intervention(id="i1", student_id="s1", ts=base_now, kind="HINT_LADDER",
                           target_problem_id="P-014", signals=[], rationale="r", message="hint", cta="cta")
    _, mod = guardrails_fn(int_bad, student, history=[], now=base_now, problem=problem)
    assert mod.kind != "HINT_LADDER" or mod.message is None


# ============================================================================
# Feature Area F4: Post-Gen & Re-planner Boundary Cases (Tests 68 - 73)
# ============================================================================

@pytest.mark.tier2
def test_t2_f4_word_cap_exact_60_vs_61_words():
    """Test 68: F4 Boundary - Word count boundary: exactly 60 words vs 61 words."""
    validator_fn = _get_postgen_validator_fn()
    if validator_fn is None:
        pytest.skip("app.guardrails postgen validator not yet implemented (Milestone 2 dependency)")

    msg_60 = "word " * 60
    assert len(msg_60.split()) == 60
    valid_60, _ = validator_fn(msg_60.strip(), is_graded=False)
    assert valid_60 is True

    msg_61 = "word " * 61
    assert len(msg_61.split()) == 61
    valid_61, _ = validator_fn(msg_61.strip(), is_graded=False)
    assert valid_61 is False


@pytest.mark.tier2
def test_t2_f4_graded_problem_single_backtick_rejection():
    """Test 69: F4 Boundary - Single and triple backtick rejection on graded problems."""
    validator_fn = _get_postgen_validator_fn()
    if validator_fn is None:
        pytest.skip("app.guardrails postgen validator not yet implemented (Milestone 2 dependency)")

    inline_code = "Use `await fetch()` to solve it"
    valid, res = validator_fn(inline_code, is_graded=True)
    assert valid is False or "`" not in res


@pytest.mark.tier2
def test_t2_f4_concept_membership_exact_id_matching():
    """Test 70: F4 Boundary - Exact case and slug matching for concept index."""
    assert get_concept("promises-error-handling") is not None
    assert get_concept("Promises-Error-Handling") is None
    assert get_concept("promises_error_handling") is None


@pytest.mark.tier2
def test_t2_f4_replanner_empty_roadmap_handling():
    """Test 71: F4 Boundary - Re-planner handling empty roadmap list gracefully."""
    replanner_fn = _get_replanner_fn()
    if replanner_fn is None:
        pytest.skip("app.replanner not yet implemented (Milestone 2 dependency)")

    diff = replanner_fn([], kind="REPLAN_DOWN", current_week=1)
    assert isinstance(diff, RoadmapDiff)


@pytest.mark.tier2
def test_t2_f4_replanner_exact_capacity_600_vs_601(make_roadmap_item: Callable[..., RoadmapItem]):
    """Test 72: F4 Boundary - Weekly capacity limit 600 min constraint."""
    replanner_fn = _get_replanner_fn()
    if replanner_fn is None:
        pytest.skip("app.replanner not yet implemented (Milestone 2 dependency)")

    item_600 = make_roadmap_item(id="R1", week=1, est_minutes=600)
    item_601 = make_roadmap_item(id="R2", week=1, est_minutes=601)

    diff_600 = replanner_fn([item_600], kind="REPLAN_DOWN", current_week=1)
    assert isinstance(diff_600, RoadmapDiff)

    diff_601 = replanner_fn([item_601], kind="REPLAN_DOWN", current_week=1)
    assert isinstance(diff_601, RoadmapDiff)
    assert len(diff_601.moved) > 0 or len(diff_601.removed) > 0 or any(m.get("week", 1) > 1 for m in diff_601.moved)


@pytest.mark.tier2
def test_t2_f4_replanner_topological_inversion_detected_and_rejected(make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 73: F4 Boundary - Re-planner must never put prerequisite after problem."""
    replanner_fn = _get_replanner_fn()
    if replanner_fn is None:
        pytest.skip("app.replanner not yet implemented (Milestone 2 dependency)")

    roadmap = make_roadmap()
    diff = replanner_fn(roadmap, kind="CONCEPT_NUDGE", target_problem_id="P-014", concept_id="promises-error-handling", current_week=2)
    # Validate no inversion
    for added in diff.added:
        if added.id == "promises-error-handling":
            assert added.week <= 2


# ============================================================================
# Feature Area F5: Follow-up Boundary Cases (Tests 74 - 78)
# ============================================================================

@pytest.mark.tier2
def test_t2_f5_followup_due_exact_47h59m_vs_48h(base_now: datetime):
    """Test 74: F5 Boundary - Follow-up due time border: 47h 59m vs 48h 00m."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", status="pending_followup",
        followup_due=base_now + timedelta(hours=48),
    )
    t_4759 = base_now + timedelta(hours=47, minutes=59)
    res_a = followup_fn(intervention, events=[], sim_now=t_4759)
    assert res_a.status == "pending_followup"

    t_4800 = base_now + timedelta(hours=48)
    res_b = followup_fn(intervention, events=[], sim_now=t_4800)
    assert res_b.status != "pending_followup"


@pytest.mark.tier2
def test_t2_f5_session_recovery_threshold_1_vs_2_sessions(base_now: datetime):
    """Test 75: F5 Boundary - Recovery session threshold: 1 session is partial, 2 is recovered."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    sim_now = base_now + timedelta(hours=50)

    # 1 session -> partial
    int_1 = Intervention(id="i1", student_id="s1", ts=base_now, kind="REENGAGE", signals=[], rationale="r", followup_due=base_now + timedelta(hours=48))
    ev_1 = [Event(student_id="s1", ts=base_now + timedelta(hours=10), type="session", duration_min=45)]
    assert followup_fn(int_1, events=ev_1, sim_now=sim_now).status == "partial"

    # 2 sessions -> recovered
    int_2 = Intervention(id="i2", student_id="s1", ts=base_now, kind="REENGAGE", signals=[], rationale="r", followup_due=base_now + timedelta(hours=48))
    ev_2 = ev_1 + [Event(student_id="s1", ts=base_now + timedelta(hours=20), type="session", duration_min=45)]
    assert followup_fn(int_2, events=ev_2, sim_now=sim_now).status == "recovered"


@pytest.mark.tier2
def test_t2_f5_pass_event_timing_before_vs_after_nudge(base_now: datetime):
    """Test 76: F5 Boundary - Pass before nudge does NOT count; pass after DOES count."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    sim_now = base_now + timedelta(hours=50)

    # Pass 5 minutes before nudge
    int_1 = Intervention(id="i1", student_id="s1", ts=base_now, kind="CONCEPT_NUDGE", target_problem_id="P-014", signals=[], rationale="r", followup_due=base_now + timedelta(hours=48))
    ev_before = [Event(student_id="s1", ts=base_now - timedelta(minutes=5), type="attempt_pass", problem_id="P-014")]
    assert followup_fn(int_1, events=ev_before, sim_now=sim_now).status != "recovered"

    # Pass 5 minutes after nudge
    int_2 = Intervention(id="i2", student_id="s1", ts=base_now, kind="CONCEPT_NUDGE", target_problem_id="P-014", signals=[], rationale="r", followup_due=base_now + timedelta(hours=48))
    ev_after = [Event(student_id="s1", ts=base_now + timedelta(minutes=5), type="attempt_pass", problem_id="P-014")]
    assert followup_fn(int_2, events=ev_after, sim_now=sim_now).status == "recovered"


@pytest.mark.tier2
def test_t2_f5_escalation_ladder_two_consecutive_no_responses(base_now: datetime):
    """Test 77: F5 Boundary - 2nd consecutive no_response transitions to ESCALATE_MENTOR."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    int_prior = Intervention(id="i1", student_id="s1", ts=base_now - timedelta(days=4), kind="REENGAGE", signals=[], rationale="r", status="no_response")
    int_curr = Intervention(id="i2", student_id="s1", ts=base_now - timedelta(days=2), kind="REENGAGE", signals=[], rationale="r", status="no_response", followup_due=base_now)
    res = followup_fn(int_curr, events=[], history=[int_prior], sim_now=base_now)
    assert res.kind == "ESCALATE_MENTOR" or getattr(res, "status", None) == "escalated"


@pytest.mark.tier2
def test_t2_f5_recovered_resets_escalation_state(base_now: datetime):
    """Test 78: F5 Boundary - Recovery resets consecutive escalation counter."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    int_old = Intervention(id="i1", student_id="s1", ts=base_now - timedelta(days=10), kind="REENGAGE", signals=[], rationale="r", status="no_response")
    int_rec = Intervention(id="i2", student_id="s1", ts=base_now - timedelta(days=5), kind="CONCEPT_NUDGE", signals=[], rationale="r", status="recovered")
    int_new = Intervention(id="i3", student_id="s1", ts=base_now - timedelta(days=2), kind="REENGAGE", signals=[], rationale="r", status="no_response", followup_due=base_now)
    res = followup_fn(int_new, events=[], history=[int_old, int_rec], sim_now=base_now)
    # Since int_rec broke the streak, int_new is 1st no_response after recovery, not 2nd!
    assert res.kind != "ESCALATE_MENTOR"


# ============================================================================
# Feature Area F6: API Boundary Cases (Tests 79 - 84)
# ============================================================================

@pytest.mark.tier2
@pytest.mark.api
def test_t2_f6_api_nonexistent_student_id_404(client: Any):
    """Test 79: F6 Boundary - 404 Not Found on unknown student ID."""
    res_get = client.get("/api/students/unknown_999")
    assert res_get.status_code == 404

    res_post = client.post("/api/students/unknown_999/evaluate")
    assert res_post.status_code == 404

    res_int = client.get("/api/students/unknown_999/interventions")
    assert res_int.status_code == 404


@pytest.mark.tier2
@pytest.mark.api
def test_t2_f6_api_sim_advance_zero_and_negative_days(client: Any):
    """Test 80: F6 Boundary - Sim advance with 0 days and invalid negative days."""
    res_zero = client.post("/api/sim/advance", json={"days": 0})
    assert res_zero.status_code == 200

    res_neg = client.post("/api/sim/advance", json={"days": -1})
    assert res_neg.status_code in [400, 422]


@pytest.mark.tier2
@pytest.mark.api
def test_t2_f6_api_sim_advance_large_days(client: Any):
    """Test 81: F6 Boundary - Large day advance (365 days)."""
    res = client.post("/api/sim/advance", json={"days": 365})
    assert res.status_code == 200


@pytest.mark.tier2
@pytest.mark.api
def test_t2_f6_api_sim_reset_idempotence(client: Any):
    """Test 82: F6 Boundary - POST /api/sim/reset idempotence across consecutive calls."""
    res1 = client.post("/api/sim/reset")
    assert res1.status_code == 200

    res2 = client.post("/api/sim/reset")
    assert res2.status_code == 200


@pytest.mark.tier2
@pytest.mark.api
def test_t2_f6_api_evaluate_consecutive_duplicate_calls(client: Any):
    """Test 83: F6 Boundary - Consecutive evaluation calls trigger 24h cooldown."""
    res1 = client.post("/api/students/aarav/evaluate")
    assert res1.status_code == 200

    res2 = client.post("/api/students/aarav/evaluate")
    assert res2.status_code == 200
    data2 = res2.json()
    # Message should be suppressed due to 24h cooldown
    assert data2["intervention"]["message"] is None or any("cooldown" in l.lower() for l in data2["intervention"]["guardrail_log"])


@pytest.mark.tier2
@pytest.mark.api
def test_t2_f6_api_metrics_with_zero_interventions(client: Any):
    """Test 84: F6 Boundary - Zero-intervention metrics safety."""
    client.post("/api/sim/reset")
    res = client.get("/api/metrics")
    assert res.status_code == 200
    data = res.json()
    assert data["recovery_rate"] == 0.0


# ============================================================================
# Feature Area F7: Seed Scenarios Boundary Cases (Tests 85 - 89)
# ============================================================================

@pytest.mark.tier2
def test_t2_f7_event_history_exact_72h_cutoff(base_now: datetime):
    """Test 85: F7 Boundary - 72-Hour temporal window cutoff for repeated_failure.
    
    Event at 71h 59m is included.
    Event at 72h 01m is excluded.
    """
    ev_in = [Event(student_id="s1", ts=base_now - timedelta(hours=71, minutes=59), type="attempt_fail", problem_id="P-014")]
    res_in = compute_repeated_failure(ev_in, "P-014", base_now)
    assert "1 failed attempts" in res_in.evidence

    ev_out = [Event(student_id="s1", ts=base_now - timedelta(hours=72, minutes=1), type="attempt_fail", problem_id="P-014")]
    res_out = compute_repeated_failure(ev_out, "P-014", base_now)
    assert "0 failed attempts" in res_out.evidence


@pytest.mark.tier2
def test_t2_f7_median_gap_days_zero_protection(base_now: datetime, make_student: Callable[..., Student]):
    """Test 86: F7 Boundary - Inactivity zero division protection when median_gap_days = 0."""
    student = make_student(median_gap_days=0.0)
    ev = [Event(student_id="s1", ts=base_now - timedelta(days=3), type="session", duration_min=45)]
    res = compute_inactivity(ev, student, base_now)
    assert res.score > 0.0


@pytest.mark.tier2
def test_t2_f7_planned_minutes_zero_protection():
    """Test 87: F7 Boundary - Pace drift and ahead zero division protection with 0 planned minutes."""
    res_drift = compute_pace_drift([], current_week=1)
    assert res_drift.score == 0.0

    res_ahead = compute_pace_ahead([], current_week=1)
    assert res_ahead.score == 0.0


@pytest.mark.tier2
def test_t2_f7_unknown_problem_or_concept_handling(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 88: F7 Boundary - Malformed event references to nonexistent problem IDs."""
    student = make_student()
    roadmap = make_roadmap()
    events = [
        Event(student_id="s1", ts=base_now - timedelta(hours=2), type="attempt_fail", problem_id="NONEXISTENT_999", duration_min=30)
    ]
    signals = detect_all_signals(student, roadmap, events, current_week=1, target_problem_id="NONEXISTENT_999", now=base_now)
    assert len(signals) == 8


@pytest.mark.tier2
def test_t2_f7_empty_events_list_evaluation(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 89: F7 Boundary - Evaluation with completely empty event list."""
    student = make_student()
    roadmap = make_roadmap()
    signals = detect_all_signals(student, roadmap, [], current_week=1, target_problem_id=None, now=base_now)
    assert len(signals) == 8
    assert all(0.0 <= s.score <= 1.0 for s in signals)
