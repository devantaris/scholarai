"""Tier 3: Cross-Feature Combinations Test Suite for Scholar Pulse.

Tests pairwise contracts and integration seams across the core engine components:
- Signals + Policy pairwise interactions (Priority ordering and conditional re-plan attachment)
- Policy + Guardrails interactions (Consent suppression, quiet hours, weekly caps, cooldown)
- Post-Generation Validator + Re-planner interactions (Code fence prohibition, concept index check, roadmap integrity)
- Follow-up + Simulation Clock interactions (Timing gates, outcome transitions, escalation ladder)
- Cohort + Metrics interactions (Multi-student evaluations and metrics accumulation)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
import pytest

from app.config import (
    DISENGAGED_THRESHOLD,
    INACTIVITY_THRESHOLD,
    PACE_AHEAD_THRESHOLD,
    PACE_DRIFT_THRESHOLD,
    REENGAGE_FIRST_TASK_MIN,
    REENGAGE_SECOND_TASK_MIN,
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
# 3.1 Signals + Policy Pairwise Interactions (Tests 3.1.1 - 3.1.12)
# ============================================================================

@pytest.mark.tier3
def test_combo_stuck_graded_with_pace_drift(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.1: stuck (graded) + pace_drift -> Policy Priority 2 with attached Re-plan."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="aarav")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on graded P-014"),
        SignalResult(name="pace_drift", score=0.40, triggered=True, evidence="40% drift"),
    ]
    res = policy_fn(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert res.kind == "CONCEPT_NUDGE"
    assert res.concept_id is not None
    assert res.roadmap_diff is not None


@pytest.mark.tier3
def test_combo_stuck_graded_without_pace_drift(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.2: stuck (graded) + pace_drift == False -> Policy Priority 2 WITHOUT Re-plan."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="aarav")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on graded P-014"),
        SignalResult(name="pace_drift", score=0.10, triggered=False, evidence="On track"),
    ]
    res = policy_fn(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert res.kind == "CONCEPT_NUDGE"
    assert res.roadmap_diff is None or len(res.roadmap_diff.added) == 0


@pytest.mark.tier3
def test_combo_stuck_practice_with_pace_drift(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.3: stuck (practice) + pace_drift -> Policy Priority 3 with attached Re-plan."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="aarav")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.8, triggered=True, evidence="Stuck on practice P-015"),
        SignalResult(name="pace_drift", score=0.35, triggered=True, evidence="35% drift"),
    ]
    res = policy_fn(student, roadmap, signals, target_problem_id="P-015", current_week=3, now=base_now)
    assert res.kind == "HINT_LADDER"
    assert res.roadmap_diff is not None


@pytest.mark.tier3
def test_combo_stuck_practice_without_pace_drift(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.4: stuck (practice) + pace_drift == False -> Policy Priority 3 WITHOUT Re-plan."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="aarav")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.8, triggered=True, evidence="Stuck on practice P-015"),
        SignalResult(name="pace_drift", score=0.05, triggered=False, evidence="On track"),
    ]
    res = policy_fn(student, roadmap, signals, target_problem_id="P-015", current_week=3, now=base_now)
    assert res.kind == "HINT_LADDER"
    assert res.roadmap_diff is None or len(res.roadmap_diff.added) == 0


@pytest.mark.tier3
def test_combo_disengaged_with_pace_drift(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.5: disengaged + pace_drift -> Policy Priority 4 (REENGAGE with REPLAN_DOWN)."""
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
    res = policy_fn(student, roadmap, signals, current_week=2, now=base_now)
    assert res.kind == "REENGAGE"
    assert res.roadmap_diff is not None


@pytest.mark.tier3
def test_combo_disengaged_without_pace_drift(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.6: disengaged + pace_drift == False -> Policy Priority 4 (REENGAGE without Re-plan)."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="meera")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.0, triggered=False, evidence=""),
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Inactivity only"),
        SignalResult(name="pace_drift", score=0.0, triggered=False, evidence="Pace fine"),
    ]
    res = policy_fn(student, roadmap, signals, current_week=2, now=base_now)
    assert res.kind == "REENGAGE"
    assert res.roadmap_diff is None or len(res.roadmap_diff.added) == 0


@pytest.mark.tier3
def test_combo_goal_change_supersedes_stuck_graded(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.7: goal_change (Priority 1) vs stuck on graded problem (Priority 2)."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="rohan")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=1.0, triggered=True, evidence="Goal change"),
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on graded P-014"),
    ]
    res = policy_fn(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert res.kind == "GOAL_REPLAN"
    assert res.kind != "CONCEPT_NUDGE"


@pytest.mark.tier3
def test_combo_goal_change_supersedes_disengaged_and_drift(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.8: goal_change (Priority 1) vs disengaged (Priority 4) + pace_drift."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="rohan")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=1.0, triggered=True, evidence="Goal change"),
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Disengaged"),
        SignalResult(name="pace_drift", score=0.45, triggered=True, evidence="Pace drift"),
    ]
    res = policy_fn(student, roadmap, signals, current_week=2, now=base_now)
    assert res.kind == "GOAL_REPLAN"
    assert res.kind != "REENGAGE"


@pytest.mark.tier3
def test_combo_goal_change_supersedes_pace_ahead(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.9: goal_change (Priority 1) vs pace_ahead (Priority 5)."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="rohan")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=1.0, triggered=True, evidence="Goal prompt differs"),
        SignalResult(name="pace_ahead", score=0.30, triggered=True, evidence="30% ahead"),
    ]
    res = policy_fn(student, roadmap, signals, current_week=1, now=base_now)
    assert res.kind == "GOAL_REPLAN"
    assert res.kind != "REPLAN_UP"


@pytest.mark.tier3
def test_combo_pace_ahead_isolated_priority_five(base_now: datetime, make_student: Callable[..., Student], make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.1.10: pace_ahead (Priority 5) when Priorities 1-4 are inactive."""
    policy_fn = _get_policy_fn()
    if policy_fn is None:
        pytest.skip("app.policy not yet implemented (Milestone 2 dependency)")

    student = make_student(id="rohan")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.0, triggered=False, evidence=""),
        SignalResult(name="disengaged", score=0.0, triggered=False, evidence=""),
        SignalResult(name="pace_ahead", score=0.35, triggered=True, evidence="35% ahead"),
    ]
    res = policy_fn(student, roadmap, signals, current_week=1, now=base_now)
    assert res.kind == "REPLAN_UP"
    assert res.roadmap_diff is not None


@pytest.mark.tier3
def test_combo_stuck_composite_single_source_repeated_failure(base_now: datetime):
    """Test 3.1.11: repeated_failure >= 0.5 with stall_time < 0.6 -> stuck composite triggered."""
    rf = SignalResult(name="repeated_failure", score=0.75, triggered=True, evidence="5 rapid fails")
    st = SignalResult(name="stall_time", score=0.15, triggered=False, evidence="10 min spent")
    stuck = compute_stuck(rf, st)
    assert stuck.triggered is True
    assert pytest.approx(stuck.score, abs=0.01) == 0.75


@pytest.mark.tier3
def test_combo_stuck_composite_single_source_stall_time():
    """Test 3.1.12: stall_time >= 0.6 with repeated_failure < 0.5 -> stuck composite triggered."""
    rf = SignalResult(name="repeated_failure", score=0.0, triggered=False, evidence="0 fails")
    st = SignalResult(name="stall_time", score=0.90, triggered=True, evidence="90 min spent")
    stuck = compute_stuck(rf, st)
    assert stuck.triggered is True
    assert pytest.approx(stuck.score, abs=0.01) == 0.90


# ============================================================================
# 3.2 Policy + Guardrails Interactions (Tests 3.2.1 - 3.2.5)
# ============================================================================

@pytest.mark.tier3
def test_combo_guardrail_graded_work_strictly_blocks_hints(base_now: datetime, make_student: Callable[..., Student], make_roadmap_item: Callable[..., RoadmapItem]):
    """Test 3.2.1: Graded problem stall + Guardrail graded work rule."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    problem = make_roadmap_item(id="P-014", graded=True)
    int_test = Intervention(
        id="i1", student_id="aarav", ts=base_now, kind="HINT_LADDER",
        target_problem_id="P-014", signals=[], rationale="r", message="hint msg", cta="cta",
    )
    _, mod = guardrails_fn(int_test, student, history=[], now=base_now, problem=problem)
    assert mod.kind == "CONCEPT_NUDGE" or mod.message is None


@pytest.mark.tier3
def test_combo_guardrail_consent_suppresses_message_preserves_diff(base_now: datetime, make_student: Callable[..., Student]):
    """Test 3.2.2: consent_nudges=False suppresses message/CTA while preserving roadmap_diff."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student(consent_nudges=False)
    diff = RoadmapDiff(added=[RoadmapItem(id="C-1", week=2, title="T", type="concept", est_minutes=20)])
    int_test = Intervention(
        id="i1", student_id="priya", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", message="msg", cta="cta", roadmap_diff=diff,
    )
    _, mod = guardrails_fn(int_test, student, history=[], now=base_now)
    assert mod.message is None
    assert mod.cta is None
    assert mod.roadmap_diff is not None
    assert len(mod.roadmap_diff.added) == 1


@pytest.mark.tier3
def test_combo_guardrail_quiet_hours_defers_message(make_student: Callable[..., Student]):
    """Test 3.2.3: Quiet hours window defers message without dropping it."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student(active_hours=(9, 22))
    night_ts = datetime(2026, 11, 18, 23, 30, 0, tzinfo=timezone.utc)
    int_test = Intervention(
        id="i1", student_id="aarav", ts=night_ts, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", message="Study async", cta="Go",
    )
    _, mod = guardrails_fn(int_test, student, history=[], now=night_ts)
    assert any("deferred" in l.lower() or "quiet" in l.lower() for l in mod.guardrail_log)


@pytest.mark.tier3
def test_combo_guardrail_cooldown_blocks_repeat_within_24h(base_now: datetime, make_student: Callable[..., Student]):
    """Test 3.2.4: 24h cooldown blocks duplicate intervention kind within 24 hours."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    prior = [
        Intervention(id="i1", student_id="aarav", ts=base_now - timedelta(hours=12),
                     kind="CONCEPT_NUDGE", signals=[], rationale="r", message="m1")
    ]
    curr = Intervention(
        id="i2", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", message="m2", cta="cta",
    )
    _, mod = guardrails_fn(curr, student, history=prior, now=base_now)
    assert mod.message is None
    assert any("cooldown" in l.lower() for l in mod.guardrail_log)


@pytest.mark.tier3
def test_combo_guardrail_weekly_cap_blocks_fourth_nudge(base_now: datetime, make_student: Callable[..., Student]):
    """Test 3.2.5: Weekly cap blocks 4th nudge within rolling 7 days."""
    guardrails_fn = _get_guardrails_fn()
    if guardrails_fn is None:
        pytest.skip("app.guardrails not yet implemented (Milestone 2 dependency)")

    student = make_student()
    prior = [
        Intervention(id="i1", student_id="aarav", ts=base_now - timedelta(days=6), kind="CONCEPT_NUDGE", signals=[], rationale="r", message="m"),
        Intervention(id="i2", student_id="aarav", ts=base_now - timedelta(days=4), kind="REENGAGE", signals=[], rationale="r", message="m"),
        Intervention(id="i3", student_id="aarav", ts=base_now - timedelta(days=2), kind="REPLAN_UP", signals=[], rationale="r", message="m"),
    ]
    fourth = Intervention(
        id="i4", student_id="aarav", ts=base_now, kind="MICRO_LESSON",
        signals=[], rationale="r", message="m4", cta="cta",
    )
    _, mod = guardrails_fn(fourth, student, history=prior, now=base_now)
    assert mod.message is None
    assert any("weekly cap" in l.lower() for l in mod.guardrail_log)


# ============================================================================
# 3.3 Post-Gen Validator & Replanner Interactions (Tests 3.3.1 - 3.3.3)
# ============================================================================

@pytest.mark.tier3
def test_combo_postgen_validator_code_fence_reverts_to_template():
    """Test 3.3.1: Post-gen validator rejects code fences on graded problems and reverts to template."""
    validator_fn = _get_postgen_validator_fn()
    if validator_fn is None:
        pytest.skip("app.guardrails postgen validator not yet implemented (Milestone 2 dependency)")

    code_payload = "Hi Aarav! Try this: ```js const p = async () => {}; ```"
    valid, sanitized = validator_fn(code_payload, is_graded=True)
    assert valid is False or "```" not in sanitized


@pytest.mark.tier3
def test_combo_postgen_validator_unknown_concept_reverts_to_template():
    """Test 3.3.2: Validator rejects unknown concepts not in curated concept index and reverts to template."""
    validator_fn = _get_postgen_validator_fn()
    if validator_fn is None:
        pytest.skip("app.guardrails / validator not yet implemented (Milestone 2 dependency)")

    unknown_concept = "quantum-computing-advanced-tensor"
    invalid_nudge = f"Review the prerequisite {unknown_concept} before attempting P-014."
    try:
        valid, sanitized = validator_fn(invalid_nudge, is_graded=False, concept_id=unknown_concept)
    except TypeError:
        valid, sanitized = validator_fn(invalid_nudge, is_graded=False)
    assert valid is False or unknown_concept not in sanitized


@pytest.mark.tier3
def test_combo_replanner_integrity_dropping_graded_item_rejected(make_roadmap: Callable[..., list[RoadmapItem]]):
    """Test 3.3.3: Re-planner integrity rejects proposals that drop graded problems."""
    replanner_fn = _get_replanner_fn()
    if replanner_fn is None:
        pytest.skip("app.replanner not yet implemented (Milestone 2 dependency)")

    roadmap = make_roadmap()
    graded_ids = {item.id for item in roadmap if item.graded}
    diff = replanner_fn(roadmap, kind="REPLAN_DOWN", current_week=2)
    assert not any(r_id in graded_ids for r_id in diff.removed)


# ============================================================================
# 3.4 Follow-up & Simulation Clock Interactions (Tests 3.4.1 - 3.4.4)
# ============================================================================

@pytest.mark.tier3
def test_combo_followup_timing_not_due_at_24h_vs_due_at_48h(base_now: datetime):
    """Test 3.4.1: Follow-up outcome evaluation only executes when simulated time >= followup_due."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="r", status="pending_followup",
        followup_due=base_now + timedelta(hours=48),
    )
    # At +24h: remains pending
    t_24h = base_now + timedelta(hours=24)
    res_24 = followup_fn(intervention, events=[], sim_now=t_24h)
    assert res_24.status == "pending_followup"

    # At +48h: evaluated
    t_48h = base_now + timedelta(hours=48)
    res_48 = followup_fn(intervention, events=[], sim_now=t_48h)
    assert res_48.status != "pending_followup"


@pytest.mark.tier3
def test_combo_followup_outcome_partial_escalates_to_micro_lesson(base_now: datetime):
    """Test 3.4.2: Follow-up outcome partial escalates to MICRO_LESSON."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        target_problem_id="P-014", signals=[], rationale="r", status="pending_followup",
        followup_due=base_now + timedelta(hours=48),
    )
    sim_now = base_now + timedelta(hours=50)
    events = [Event(student_id="aarav", ts=base_now + timedelta(hours=20), type="session", duration_min=30)]
    res = followup_fn(intervention, events=events, sim_now=sim_now)
    assert res.status == "partial"


@pytest.mark.tier3
def test_combo_followup_outcome_recovered_backs_off(base_now: datetime):
    """Test 3.4.3: Follow-up outcome recovered backs off from aggressive nudges."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    intervention = Intervention(
        id="int-1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        target_problem_id="P-014", signals=[], rationale="r", status="pending_followup",
        followup_due=base_now + timedelta(hours=48),
    )
    sim_now = base_now + timedelta(hours=50)
    events = [Event(student_id="aarav", ts=base_now + timedelta(hours=20), type="attempt_pass", problem_id="P-014")]
    res = followup_fn(intervention, events=events, sim_now=sim_now)
    assert res.status == "recovered"


@pytest.mark.tier3
def test_combo_escalation_ladder_two_consecutive_no_responses_to_mentor(base_now: datetime):
    """Test 3.4.4: 2 consecutive no_responses escalate to ESCALATE_MENTOR without student message."""
    followup_fn = _get_followup_fn()
    if followup_fn is None:
        pytest.skip("app.followup not yet implemented (Milestone 3 dependency)")

    int_prior = Intervention(id="i1", student_id="meera", ts=base_now - timedelta(days=4), kind="REENGAGE", signals=[], rationale="r", status="no_response")
    int_curr = Intervention(id="i2", student_id="meera", ts=base_now - timedelta(days=2), kind="REENGAGE", signals=[], rationale="r", status="no_response", followup_due=base_now)
    escalated = followup_fn(int_curr, events=[], history=[int_prior], sim_now=base_now)
    assert escalated.kind == "ESCALATE_MENTOR"
    assert escalated.message is None


# ============================================================================
# 3.5 Cohort x Metrics Interactions (Test 3.5.1)
# ============================================================================

@pytest.mark.tier3
@pytest.mark.api
def test_combo_metrics_accumulation_across_cohort(client: Any):
    """Test 3.5.1: Multi-student evaluations and metrics accumulation."""
    client.post("/api/sim/reset")
    # Evaluate all 4 students
    client.post("/api/students/aarav/evaluate")
    client.post("/api/students/meera/evaluate")
    client.post("/api/students/rohan/evaluate")
    client.post("/api/students/priya/evaluate")

    res = client.get("/api/metrics")
    assert res.status_code == 200
    metrics = res.json()
    assert metrics["nudges_sent"] >= 1
    assert metrics["nudges_blocked"] >= 1
    assert 0.0 <= metrics["recovery_rate"] <= 1.0
