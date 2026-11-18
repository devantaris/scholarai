"""Tier 1: Feature Coverage Test Suite for Scholar Pulse.

Covers all 7 feature areas (F1 through F7) with at least 5 isolated tests per area:
- F1: Signal Detection Engine (6 signals + 2 composite scores)
- F2: Policy Decision Engine (Priorities 1-6 and plain-English rationales)
- F3: Pre-Generation Guardrails (Consent, quiet hours, weekly cap, cooldown, graded rule)
- F4: Post-Generation Validation (Word cap, code fence ban, concept membership, roadmap integrity)
- F5: Follow-up & Escalation (+48h outcome evaluation, escalation ladder)
- F6: Backend REST API & Store (Endpoints /api/mode, /api/students, /api/sim/*, /api/cohort, /api/metrics)
- F7: Seed Scenarios & Simulation (Aarav, Meera, Rohan, Priya baselines and concept index)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
import pytest

from app.config import (
    DISENGAGED_DRIFT_WEIGHT,
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


# ============================================================================
# Helpers for Progressive Milestone Testability
# ============================================================================

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
# Feature Area F1: Signal Detection Engine (Tests 1 - 9)
# ============================================================================

@pytest.mark.tier1
def test_t1_f1_repeated_failure_detection(base_now: datetime, make_student: Callable[..., Student]):
    """Test 1: F1 - repeated_failure detector.
    
    4 fail attempts on P-014 within 72h without pass produces score (4-2)/4 = 0.50 and triggered=True.
    """
    events = [
        Event(student_id="aarav", ts=base_now - timedelta(hours=i * 5), type="attempt_fail",
              problem_id="P-014", error_signature=f"Err_{i}")
        for i in range(1, 5)
    ]
    result = compute_repeated_failure(events, "P-014", base_now)
    assert result.name == "repeated_failure"
    assert pytest.approx(result.score, abs=0.01) == 0.50
    assert result.triggered is True
    assert "4 failed attempts on P-014" in result.evidence


@pytest.mark.tier1
def test_t1_f1_repeated_failure_identical_error_bonus(base_now: datetime):
    """Test 2: F1 - repeated_failure identical error signature bonus (+0.20).
    
    5 fails with 3 sharing TypeError:undefined_iterable -> (5-2)/4 + 0.20 = 0.95.
    """
    events = [
        Event(student_id="aarav", ts=base_now - timedelta(hours=10), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=8), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=6), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=4), type="attempt_fail",
              problem_id="P-014", error_signature="SyntaxError:other"),
        Event(student_id="aarav", ts=base_now - timedelta(hours=2), type="attempt_fail",
              problem_id="P-014", error_signature="ReferenceError:other2"),
    ]
    result = compute_repeated_failure(events, "P-014", base_now)
    assert pytest.approx(result.score, abs=0.01) == 0.95
    assert result.triggered is True
    assert "3 with the same error TypeError:undefined_iterable" in result.evidence


@pytest.mark.tier1
def test_t1_f1_repeated_failure_suppression_on_pass(base_now: datetime):
    """Test 3: F1 - repeated_failure suppression on attempt_pass.
    
    6 fails followed by 1 pass on P-014 resets repeated_failure to 0.0 and triggered=False.
    """
    events = [
        Event(student_id="aarav", ts=base_now - timedelta(hours=i * 4), type="attempt_fail",
              problem_id="P-014", error_signature="Err")
        for i in range(1, 7)
    ]
    events.append(
        Event(student_id="aarav", ts=base_now - timedelta(hours=1), type="attempt_pass",
              problem_id="P-014")
    )
    result = compute_repeated_failure(events, "P-014", base_now)
    assert result.score == 0.0
    assert result.triggered is False
    assert "passed" in result.evidence.lower()


@pytest.mark.tier1
def test_t1_f1_stall_time_detection(base_now: datetime):
    """Test 4: F1 - stall_time detector.
    
    Cumulative time on unpassed problem exceeding 1.5 * est_minutes triggers stall_time.
    est = 60 min, threshold = 90 min. 90 min on problem -> score = 90/90 = 1.0 >= 0.60.
    """
    events = [
        Event(student_id="std-01", ts=base_now - timedelta(hours=4), type="session",
              problem_id="P-014", duration_min=50),
        Event(student_id="std-01", ts=base_now - timedelta(hours=2), type="session",
              problem_id="P-014", duration_min=40),
    ]
    result = compute_stall_time(events, "P-014", 60, base_now)
    assert result.name == "stall_time"
    assert pytest.approx(result.score, abs=0.01) == 1.0
    assert result.triggered is True
    assert "Spent 90 minutes on P-014" in result.evidence


@pytest.mark.tier1
def test_t1_f1_inactivity_detection(base_now: datetime, make_student: Callable[..., Student]):
    """Test 5: F1 - inactivity detector.
    
    Elapsed days since last session vs median gap. median_gap=2.0d, last session 6.0d ago.
    Formula: 6.0 / max(2.0, 2.0*2.0) = 6.0 / 4.0 = 1.5 -> clamped to 1.0 >= 0.60.
    """
    student = make_student(id="meera", median_gap_days=2.0)
    events = [
        Event(student_id="meera", ts=base_now - timedelta(days=6.0), type="session", duration_min=60)
    ]
    result = compute_inactivity(events, student, base_now)
    assert result.name == "inactivity"
    assert pytest.approx(result.score, abs=0.01) == 1.0
    assert result.triggered is True
    assert "6.0 days since last session" in result.evidence


@pytest.mark.tier1
def test_t1_f1_pace_drift_detection(make_roadmap_item: Callable[..., RoadmapItem]):
    """Test 6: F1 - pace_drift detector.
    
    Planned = 600 min, actual = 360 min. Formula: 1.0 - (360/600) = 0.40 >= 0.30.
    """
    roadmap = [
        make_roadmap_item(id="R-1", week=1, title="Item 1", type="concept", est_minutes=360, status="done"),
        make_roadmap_item(id="R-2", week=1, title="Item 2", type="problem", est_minutes=240, status="todo"),
    ]
    result = compute_pace_drift(roadmap, current_week=1)
    assert result.name == "pace_drift"
    assert pytest.approx(result.score, abs=0.01) == 0.40
    assert result.triggered is True
    assert "40% behind plan" in result.evidence


@pytest.mark.tier1
def test_t1_f1_pace_ahead_detection(make_roadmap_item: Callable[..., RoadmapItem]):
    """Test 7: F1 - pace_ahead detector.
    
    Planned = 500 min, actual = 650 min. Formula: (650/500) - 1.0 = 0.30 >= 0.25.
    """
    roadmap = [
        make_roadmap_item(id="R-1", week=1, title="Item 1", type="concept", est_minutes=500, status="done"),
        make_roadmap_item(id="R-2", week=2, title="Item 2", type="concept", est_minutes=150, status="done"),
    ]
    result = compute_pace_ahead(roadmap, current_week=1)
    assert result.name == "pace_ahead"
    assert pytest.approx(result.score, abs=0.01) == 0.30
    assert result.triggered is True
    assert "30% ahead of schedule" in result.evidence


@pytest.mark.tier1
def test_t1_f1_goal_change_detection(base_now: datetime, make_student: Callable[..., Student]):
    """Test 8: F1 - goal_change detector.
    
    Student roadmap_goal differs from latest goal_prompt event -> score 1.0, triggered True.
    """
    student = make_student(id="rohan", roadmap_goal="Full-Stack Web Development")
    events = [
        Event(student_id="rohan", ts=base_now - timedelta(hours=2), type="goal_prompt",
              goal_text="Data Engineering and Distributed Systems")
    ]
    result = compute_goal_change(events, student)
    assert result.name == "goal_change"
    assert result.score == 1.0
    assert result.triggered is True
    assert "differs from current roadmap goal" in result.evidence


@pytest.mark.tier1
def test_t1_f1_composite_signals_computation():
    """Test 9: F1 - Composite signals (stuck and disengaged).
    
    stuck = max(repeated_failure, stall_time) with OR trigger.
    disengaged = max(inactivity, 0.8 * pace_drift) with OR trigger.
    """
    rf = SignalResult(name="repeated_failure", score=0.70, triggered=True, evidence="rf ev")
    st = SignalResult(name="stall_time", score=0.40, triggered=False, evidence="st ev")
    stuck = compute_stuck(rf, st)
    assert stuck.name == "stuck"
    assert pytest.approx(stuck.score, abs=0.01) == 0.70
    assert stuck.triggered is True

    inact = SignalResult(name="inactivity", score=0.50, triggered=False, evidence="inact ev")
    drift = SignalResult(name="pace_drift", score=0.80, triggered=True, evidence="drift ev")
    disengaged = compute_disengaged(inact, drift)
    assert disengaged.name == "disengaged"
    assert pytest.approx(disengaged.score, abs=0.01) == 0.64
    assert disengaged.triggered is True


# ============================================================================
# Feature Area F2: Policy Decision Engine (Tests 10 - 16)
# ============================================================================

@pytest.mark.tier1
def test_t1_f2_priority_1_goal_change(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 10: F2 - Priority 1: GOAL_REPLAN.
    
    When goal_change triggers, GOAL_REPLAN is chosen and attaches full re-plan.
    """
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="rohan", roadmap_goal="Frontend Track")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=1.0, triggered=True, evidence="Goal prompt differs"),
        SignalResult(name="stuck", score=0.8, triggered=True, evidence="Stuck"),
    ]
    intervention = policy_fn(student, roadmap, signals, current_week=1, now=base_now)
    assert intervention.kind == "GOAL_REPLAN"
    assert intervention.roadmap_diff is not None
    assert "goal" in intervention.rationale.lower()


@pytest.mark.tier1
def test_t1_f2_priority_2_stuck_graded_problem(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 11: F2 - Priority 2: CONCEPT_NUDGE on graded problem.
    
    stuck on graded problem produces CONCEPT_NUDGE (never hints or code).
    """
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="aarav")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on P-014"),
        SignalResult(name="pace_drift", score=0.40, triggered=True, evidence="Drift 40%"),
    ]
    intervention = policy_fn(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert intervention.kind == "CONCEPT_NUDGE"
    assert intervention.concept_id is not None
    assert intervention.roadmap_diff is not None


@pytest.mark.tier1
def test_t1_f2_priority_3_stuck_practice_problem(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 12: F2 - Priority 3: HINT_LADDER on practice problem.
    
    stuck on practice problem (graded=False) produces HINT_LADDER.
    """
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="aarav")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.8, triggered=True, evidence="Stuck on P-015"),
    ]
    intervention = policy_fn(student, roadmap, signals, target_problem_id="P-015", current_week=3, now=base_now)
    assert intervention.kind == "HINT_LADDER"


@pytest.mark.tier1
def test_t1_f2_priority_4_disengaged_reengage(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 13: F2 - Priority 4: REENGAGE on disengaged student.
    
    disengaged triggers REENGAGE with small 10m task; attaches REPLAN_DOWN if drift also triggers.
    """
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="meera")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.0, triggered=False, evidence=""),
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="6d inactive"),
        SignalResult(name="pace_drift", score=0.35, triggered=True, evidence="35% drift"),
    ]
    intervention = policy_fn(student, roadmap, signals, current_week=2, now=base_now)
    assert intervention.kind == "REENGAGE"
    assert intervention.roadmap_diff is not None


@pytest.mark.tier1
def test_t1_f2_priority_5_pace_ahead_replan_up(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 14: F2 - Priority 5: REPLAN_UP when student is ahead of schedule."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="rohan")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.0, triggered=False, evidence=""),
        SignalResult(name="disengaged", score=0.0, triggered=False, evidence=""),
        SignalResult(name="pace_ahead", score=0.30, triggered=True, evidence="30% ahead"),
    ]
    intervention = policy_fn(student, roadmap, signals, current_week=1, now=base_now)
    assert intervention.kind == "REPLAN_UP"
    assert intervention.roadmap_diff is not None


@pytest.mark.tier1
def test_t1_f2_priority_6_default_none(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 15: F2 - Priority 6: NONE when no signals are triggered."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="healthy")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.2, triggered=False, evidence=""),
        SignalResult(name="disengaged", score=0.1, triggered=False, evidence=""),
        SignalResult(name="pace_ahead", score=0.1, triggered=False, evidence=""),
    ]
    intervention = policy_fn(student, roadmap, signals, current_week=1, now=base_now)
    assert intervention.kind == "NONE"
    assert intervention.message is None
    assert intervention.roadmap_diff is None


@pytest.mark.tier1
def test_t1_f2_rationale_plain_english_generation(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 16: F2 - Deterministic plain-English rationale generation."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="aarav")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="6 fails on P-014"),
    ]
    intervention = policy_fn(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert len(intervention.rationale) > 10
    assert "P-014" in intervention.rationale or "fail" in intervention.rationale.lower()


# ============================================================================
# Feature Area F3: Pre-Generation Guardrails (Tests 17 - 21)
# ============================================================================

@pytest.mark.tier1
def test_t1_f3_consent_disabled_suppresses_message(base_now: datetime, make_student: Callable[..., Student]):
    """Test 17: F3 - Guardrail 1: Consent opt-out suppresses message & CTA."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student(consent_nudges=False)
    intervention = Intervention(
        id="int-1", student_id="priya", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="Stuck", message="Review async", cta="Start Review",
        roadmap_diff=RoadmapDiff(),
    )
    passed, modified = guardrails_fn(intervention, student, history=[], now=base_now)
    assert modified.message is None
    assert modified.cta is None
    assert any("consent" in log.lower() for log in modified.guardrail_log)
    assert modified.roadmap_diff is not None


@pytest.mark.tier1
def test_t1_f3_quiet_hours_defers_message(make_student: Callable[..., Student]):
    """Test 18: F3 - Guardrail 2: Quiet hours window (09:00 - 22:00).
    
    Evaluation at 23:30 defers message to next morning window.
    """
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student(active_hours=(9, 22))
    late_time = datetime(2026, 11, 18, 23, 30, 0, tzinfo=timezone.utc)
    intervention = Intervention(
        id="int-1", student_id="aarav", ts=late_time, kind="CONCEPT_NUDGE",
        signals=[], rationale="Stuck", message="Review promises", cta="Start",
    )
    passed, modified = guardrails_fn(intervention, student, history=[], now=late_time)
    assert any("quiet" in log.lower() or "deferred" in log.lower() for log in modified.guardrail_log)


@pytest.mark.tier1
def test_t1_f3_weekly_cap_enforcement(base_now: datetime, make_student: Callable[..., Student]):
    """Test 19: F3 - Guardrail 3: Weekly cap (max 3 nudges in rolling 7 days)."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    history = [
        Intervention(id=f"int-{i}", student_id="aarav", ts=base_now - timedelta(days=i * 2),
                     kind="CONCEPT_NUDGE", signals=[], rationale="Old", message="Old nudge")
        for i in range(1, 4)
    ]
    new_int = Intervention(
        id="int-4", student_id="aarav", ts=base_now, kind="REENGAGE",
        signals=[], rationale="New", message="New nudge", cta="Go",
    )
    passed, modified = guardrails_fn(new_int, student, history=history, now=base_now)
    assert modified.message is None
    assert any("weekly cap" in log.lower() for log in modified.guardrail_log)


@pytest.mark.tier1
def test_t1_f3_cooldown_24h_enforcement(base_now: datetime, make_student: Callable[..., Student]):
    """Test 20: F3 - Guardrail 4: 24h cooldown prevents duplicate intervention kind."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    history = [
        Intervention(id="int-1", student_id="aarav", ts=base_now - timedelta(hours=6),
                     kind="CONCEPT_NUDGE", signals=[], rationale="Prior", message="Prior nudge")
    ]
    new_int = Intervention(
        id="int-2", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="Stuck again", message="New nudge", cta="Go",
    )
    passed, modified = guardrails_fn(new_int, student, history=history, now=base_now)
    assert modified.message is None
    assert any("cooldown" in log.lower() for log in modified.guardrail_log)


@pytest.mark.tier1
def test_t1_f3_graded_work_blocks_hint_ladder(base_now: datetime, make_student: Callable[..., Student], make_roadmap_item: Callable[..., RoadmapItem]):
    """Test 21: F3 - Guardrail 5: Graded work strictly forbids hints and solutions."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    problem = make_roadmap_item(id="P-014", graded=True)
    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="HINT_LADDER",
        target_problem_id="P-014", signals=[], rationale="Hint requested",
        message="Here is hint 1", cta="Try Hint",
    )
    passed, modified = guardrails_fn(intervention, student, history=[], now=base_now, problem=problem)
    assert modified.kind != "HINT_LADDER" or modified.message is None
    assert any("graded" in log.lower() for log in modified.guardrail_log)


# ============================================================================
# Feature Area F4: Post-Gen & Re-planner Validation (Tests 22 - 27)
# ============================================================================

@pytest.mark.tier1
def test_t1_f4_word_cap_validation():
    """Test 22: F4 - Word count validation (<= 60 words)."""
    validator_fn = _get_postgen_validator_fn()
    if validator_fn is None:
        pytest.skip("app.guardrails postgen validator not yet implemented (Milestone 2 dependency)")

    short_msg = "Hello Aarav, you are doing great. Take a look at this prerequisite concept to get unstuck."
    assert len(short_msg.split()) <= MAX_NUDGE_WORDS
    valid, res = validator_fn(short_msg, is_graded=False)
    assert valid is True

    long_msg = "word " * 65
    valid_long, res_long = validator_fn(long_msg, is_graded=False)
    assert valid_long is False or len(res_long.split()) <= MAX_NUDGE_WORDS


@pytest.mark.tier1
def test_t1_f4_code_fence_ban_on_graded_work():
    """Test 23: F4 - Code fence prohibition on graded items."""
    validator_fn = _get_postgen_validator_fn()
    if validator_fn is None:
        pytest.skip("app.guardrails postgen validator not yet implemented (Milestone 2 dependency)")

    bad_msg = "Try this: ```js\nconst x = await fetch();\n```"
    valid, res = validator_fn(bad_msg, is_graded=True)
    assert valid is False or "```" not in res


@pytest.mark.tier1
def test_t1_f4_concept_index_membership():
    """Test 24: F4 - Concept index membership validation."""
    assert get_concept("promises-error-handling") is not None
    assert get_concept("nonexistent-concept-xyz") is None


@pytest.mark.tier1
def test_t1_f4_replanner_preserves_graded_items(make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 25: F4 - Re-planner must never drop graded problem items."""
    replanner_fn = _get_replanner_fn()
    if replanner_fn is None:
        pytest.skip("app.replanner not yet implemented (Milestone 2 dependency)")

    original_roadmap = make_roadmap()
    graded_ids = {item.id for item in original_roadmap if item.graded}
    assert len(graded_ids) > 0

    diff = replanner_fn(original_roadmap, kind="REPLAN_DOWN", current_week=2)
    assert not any(removed_id in graded_ids for removed_id in diff.removed)


@pytest.mark.tier1
def test_t1_f4_replanner_topological_prereq_ordering(make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 26: F4 - Re-planner must ensure prerequisite concepts precede dependent problems."""
    replanner_fn = _get_replanner_fn()
    if replanner_fn is None:
        pytest.skip("app.replanner not yet implemented (Milestone 2 dependency)")

    roadmap = make_roadmap()
    diff = replanner_fn(roadmap, kind="CONCEPT_NUDGE", target_problem_id="P-014", concept_id="promises-error-handling", current_week=2)
    # The inserted concept must be in week <= P-014's week
    for added in diff.added:
        if added.id == "promises-error-handling":
            assert added.week <= 2


@pytest.mark.tier1
def test_t1_f4_replanner_weekly_capacity_constraint(
    make_student: Callable[..., Student],
    make_roadmap_item: Callable[..., RoadmapItem],
):
    """Test 27: F4 - Weekly capacity constraint (<= 600 min per week)."""
    replanner_fn = _get_replanner_fn()
    if replanner_fn is None:
        pytest.skip("app.replanner not yet implemented (Milestone 2 dependency)")

    student = make_student(weekly_capacity_min=600)
    overloaded_roadmap = [
        make_roadmap_item(id="R1", week=1, est_minutes=300),
        make_roadmap_item(id="R2", week=1, est_minutes=300),
        make_roadmap_item(id="R3", week=1, est_minutes=150),
    ]
    diff = replanner_fn(overloaded_roadmap, kind="REPLAN_DOWN", current_week=1)
    assert isinstance(diff, RoadmapDiff)
    assert len(diff.moved) > 0 or len(diff.removed) > 0


# ============================================================================
# Feature Area F5: Follow-up & Escalation (Tests 28 - 34)
# ============================================================================

@pytest.mark.tier1
def test_t1_f5_followup_status_pending_under_48h(base_now: datetime):
    """Test 28: F5 - Follow-up status remains pending_followup when < 48h elapsed."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="Stuck", status="pending_followup",
        followup_due=base_now + timedelta(hours=48),
    )
    sim_now = base_now + timedelta(hours=24)
    updated = followup_fn(intervention, events=[], sim_now=sim_now)
    assert updated.status == "pending_followup"


@pytest.mark.tier1
def test_t1_f5_followup_outcome_recovered_on_pass(base_now: datetime):
    """Test 29: F5 - Outcome recovered when target problem is passed after nudge."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        target_problem_id="P-014", signals=[], rationale="Stuck",
        followup_due=base_now + timedelta(hours=48),
    )
    sim_now = base_now + timedelta(hours=50)
    events = [
        Event(student_id="aarav", ts=base_now + timedelta(hours=36), type="attempt_pass", problem_id="P-014")
    ]
    updated = followup_fn(intervention, events=events, sim_now=sim_now)
    assert updated.status == "recovered"


@pytest.mark.tier1
def test_t1_f5_followup_outcome_recovered_on_two_sessions(base_now: datetime):
    """Test 30: F5 - Outcome recovered when student completes 2+ sessions post-nudge."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="meera", ts=base_now, kind="REENGAGE",
        signals=[], rationale="Inactive", followup_due=base_now + timedelta(hours=48),
    )
    sim_now = base_now + timedelta(hours=49)
    events = [
        Event(student_id="meera", ts=base_now + timedelta(hours=10), type="session", duration_min=45),
        Event(student_id="meera", ts=base_now + timedelta(hours=25), type="session", duration_min=45),
    ]
    updated = followup_fn(intervention, events=events, sim_now=sim_now)
    assert updated.status == "recovered"


@pytest.mark.tier1
def test_t1_f5_followup_outcome_partial_on_single_session(base_now: datetime):
    """Test 31: F5 - Outcome partial when only 1 session completed and no pass."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        target_problem_id="P-014", signals=[], rationale="Stuck",
        followup_due=base_now + timedelta(hours=48),
    )
    sim_now = base_now + timedelta(hours=50)
    events = [
        Event(student_id="aarav", ts=base_now + timedelta(hours=12), type="session", duration_min=30)
    ]
    updated = followup_fn(intervention, events=events, sim_now=sim_now)
    assert updated.status == "partial"


@pytest.mark.tier1
def test_t1_f5_followup_outcome_no_response(base_now: datetime):
    """Test 32: F5 - Outcome no_response when 0 events occur in +48h."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="meera", ts=base_now, kind="REENGAGE",
        signals=[], rationale="Inactive", followup_due=base_now + timedelta(hours=48),
    )
    sim_now = base_now + timedelta(hours=50)
    updated = followup_fn(intervention, events=[], sim_now=sim_now)
    assert updated.status == "no_response"


@pytest.mark.tier1
def test_t1_f5_escalation_ladder_partial_to_microlesson(base_now: datetime):
    """Test 33: F5 - Escalation ladder: partial outcome escalates to MICRO_LESSON."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        target_problem_id="P-014", signals=[], rationale="Stuck", status="partial",
    )
    escalated = followup_fn(intervention, events=[], sim_now=base_now + timedelta(hours=48))
    assert escalated.kind == "MICRO_LESSON" or getattr(escalated, "next_kind", None) == "MICRO_LESSON"


@pytest.mark.tier1
def test_t1_f5_escalation_ladder_consecutive_no_response_to_mentor(base_now: datetime):
    """Test 34: F5 - Escalation ladder: 2 consecutive no_response outcomes escalate to ESCALATE_MENTOR."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    int1 = Intervention(id="int-1", student_id="meera", ts=base_now - timedelta(days=4),
                        kind="REENGAGE", signals=[], rationale="Inactive", status="no_response")
    int2 = Intervention(id="int-2", student_id="meera", ts=base_now - timedelta(days=2),
                        kind="REENGAGE", signals=[], rationale="Inactive 2", status="no_response")
    next_action = followup_fn(int2, events=[], history=[int1], sim_now=base_now)
    assert next_action.kind == "ESCALATE_MENTOR" or getattr(next_action, "status", None) == "escalated"


# ============================================================================
# Feature Area F6: Backend REST API & Store (Tests 35 - 43)
# ============================================================================

@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_get_mode(client: Any):
    """Test 35: F6 - GET /api/mode returns LLM mode and sim_now ISO string."""
    res = client.get("/api/mode")
    assert res.status_code == 200
    data = res.json()
    assert data["llm"] in ["live", "template"]
    assert "sim_now" in data


@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_get_students(client: Any):
    """Test 36: F6 - GET /api/students returns 4 seeded students with risk summaries."""
    res = client.get("/api/students")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 4
    student_ids = {s["id"] for s in data}
    assert "aarav" in student_ids
    assert "meera" in student_ids
    assert "rohan" in student_ids


@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_get_student_detail(client: Any):
    """Test 37: F6 - GET /api/students/{id} returns details, roadmap, events, and signals."""
    res = client.get("/api/students/aarav")
    assert res.status_code == 200
    data = res.json()
    assert data["student"]["id"] == "aarav"
    assert "roadmap" in data
    assert "events" in data
    assert "signals" in data


@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_post_evaluate(client: Any):
    """Test 38: F6 - POST /api/students/{id}/evaluate executes full proactive loop."""
    res = client.post("/api/students/aarav/evaluate")
    assert res.status_code == 200
    data = res.json()
    assert "intervention" in data
    assert data["intervention"]["student_id"] == "aarav"
    assert data["intervention"]["kind"] in ["CONCEPT_NUDGE", "HINT_LADDER", "REENGAGE", "GOAL_REPLAN", "NONE"]


@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_get_interventions(client: Any):
    """Test 39: F6 - GET /api/students/{id}/interventions retrieves intervention history."""
    client.post("/api/students/aarav/evaluate")
    res = client.get("/api/students/aarav/interventions")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_post_sim_advance(client: Any):
    """Test 40: F6 - POST /api/sim/advance advances simulated clock."""
    mode_before = client.get("/api/mode").json()
    res = client.post("/api/sim/advance", json={"days": 1})
    assert res.status_code == 200
    mode_after = client.get("/api/mode").json()
    assert mode_after["sim_now"] > mode_before["sim_now"]


@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_post_sim_reset(client: Any):
    """Test 41: F6 - POST /api/sim/reset restores baseline seed state."""
    client.post("/api/sim/advance", json={"days": 5})
    res = client.post("/api/sim/reset")
    assert res.status_code == 200


@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_get_cohort(client: Any):
    """Test 42: F6 - GET /api/cohort returns at-risk students for college dashboard."""
    res = client.get("/api/cohort")
    assert res.status_code == 200
    data = res.json()
    assert "at_risk_students" in data


@pytest.mark.tier1
@pytest.mark.api
def test_t1_f6_api_get_metrics(client: Any):
    """Test 43: F6 - GET /api/metrics returns aggregate recovery and intervention stats."""
    res = client.get("/api/metrics")
    assert res.status_code == 200
    data = res.json()
    for field in ["recovery_rate", "avg_hours_to_recover", "nudges_sent", "nudges_blocked", "escalations"]:
        assert field in data


# ============================================================================
# Feature Area F7: Seed Scenarios & Baseline Simulation (Tests 44 - 50)
# ============================================================================

@pytest.mark.tier1
def test_t1_f7_aarav_baseline_state(aarav_scenario_data: dict[str, Any]):
    """Test 44: F7 - Aarav baseline scenario state verification.
    
    6 fails in 72h on graded P-014, cumulative stall time, pace drift 40%.
    """
    signals = detect_all_signals(
        student=aarav_scenario_data["student"],
        roadmap=aarav_scenario_data["roadmap"],
        events=aarav_scenario_data["events"],
        current_week=2,
        target_problem_id=aarav_scenario_data["target_problem_id"],
        now=aarav_scenario_data["now"],
    )
    sig_map = {s.name: s for s in signals}
    assert sig_map["repeated_failure"].score >= REPEATED_FAILURE_THRESHOLD
    assert sig_map["stall_time"].score >= STALL_TIME_THRESHOLD
    assert sig_map["stuck"].triggered is True
    assert sig_map["pace_drift"].score >= PACE_DRIFT_THRESHOLD


@pytest.mark.tier1
def test_t1_f7_aarav_evaluation_output(client: Any):
    """Test 45: F7 - Aarav evaluation produces CONCEPT_NUDGE with REPLAN_DOWN."""
    res = client.post("/api/students/aarav/evaluate")
    assert res.status_code == 200
    data = res.json()
    assert data["intervention"]["kind"] == "CONCEPT_NUDGE"
    assert data["intervention"]["concept_id"] == "promises-error-handling"
    assert data["intervention"]["roadmap_diff"] is not None


@pytest.mark.tier1
def test_t1_f7_meera_baseline_state(meera_scenario_data: dict[str, Any]):
    """Test 46: F7 - Meera baseline scenario state verification.
    
    6 days inactive, median gap 1.5d -> inactivity and disengaged triggered.
    """
    signals = detect_all_signals(
        student=meera_scenario_data["student"],
        roadmap=meera_scenario_data["roadmap"],
        events=meera_scenario_data["events"],
        current_week=1,
        target_problem_id=None,
        now=meera_scenario_data["now"],
    )
    sig_map = {s.name: s for s in signals}
    assert sig_map["inactivity"].triggered is True
    assert sig_map["disengaged"].triggered is True


@pytest.mark.tier1
def test_t1_f7_meera_evaluation_output(client: Any):
    """Test 47: F7 - Meera evaluation produces REENGAGE intervention with small task."""
    res = client.post("/api/students/meera/evaluate")
    assert res.status_code == 200
    data = res.json()
    assert data["intervention"]["kind"] == "REENGAGE"


@pytest.mark.tier1
def test_t1_f7_rohan_baseline_and_goal_shift(rohan_scenario_data: dict[str, Any]):
    """Test 48: F7 - Rohan baseline and goal shift detection."""
    student = rohan_scenario_data["student"]
    events = rohan_scenario_data["events"]
    goal_res = compute_goal_change(events, student)
    assert goal_res.score == 1.0
    assert goal_res.triggered is True


@pytest.mark.tier1
def test_t1_f7_consent_disabled_student_behavior(priya_consent_disabled_scenario_data: dict[str, Any]):
    """Test 49: F7 - Consent-disabled student (Priya) flag and signal verification."""
    student = priya_consent_disabled_scenario_data["student"]
    assert student.consent_nudges is False
    signals = detect_all_signals(
        student=student,
        roadmap=priya_consent_disabled_scenario_data["roadmap"],
        events=priya_consent_disabled_scenario_data["events"],
        current_week=2,
        target_problem_id="P-014",
        now=priya_consent_disabled_scenario_data["now"],
    )
    sig_map = {s.name: s for s in signals}
    assert sig_map["repeated_failure"].triggered is True


@pytest.mark.tier1
def test_t1_f7_curated_content_index_completeness():
    """Test 50: F7 - Curated content index completeness.
    
    Exactly 12 concepts present, valid titles, summaries, and positive est_minutes.
    """
    assert len(CURATED_CONCEPTS) == 12
    required_ids = [
        "js-closures", "async-await", "promises-error-handling", "rest-basics",
        "http-status-codes", "sql-joins", "sql-indexes", "react-state",
        "react-effects", "node-modules", "git-branching", "auth-jwt"
    ]
    for cid in required_ids:
        concept = get_concept(cid)
        assert concept is not None
        assert len(concept.title) > 0
        assert len(concept.summary) > 0
        assert concept.est_minutes > 0
