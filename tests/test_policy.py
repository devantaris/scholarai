"""Unit Test Suite for Scholar Pulse Policy Engine (app/policy.py).

Verifies:
- Strict 1-to-6 priority hierarchy table (GOAL_REPLAN > CONCEPT_NUDGE > HINT_LADDER > REENGAGE > REPLAN_UP > NONE)
- Re-plan attachment conditions (pace_drift threshold 0.30)
- Target problem auto-discovery (in_progress vs todo)
- Prerequisite concept selection from CURATED_CONCEPTS
- Deterministic plain-English rationale generation
- Aliases and polymorphic call patterns
"""

from datetime import datetime, timezone
import pytest

from app.models import CURATED_CONCEPTS, RoadmapDiff, RoadmapItem, SignalResult, Student
from app.policy import decide, decide_intervention, evaluate_policy


# =============================================================================
# Priority Hierarchy & Conflict Resolution Tests
# =============================================================================

def test_priority_1_goal_change_triggers_goal_replan(base_now: datetime, make_student, make_roadmap):
    """Priority 1: goal_change produces GOAL_REPLAN with attached full re-plan."""
    student = make_student(roadmap_goal="Frontend Track", goal_prompt="Backend Systems")
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=1.0, triggered=True, evidence="Goal differs"),
    ]
    intv = decide_intervention(student, roadmap, signals, current_week=1, now=base_now)
    assert intv.kind == "GOAL_REPLAN"
    assert intv.roadmap_diff is not None
    assert "goal" in intv.rationale.lower()


def test_priority_1_supersedes_priority_2_stuck_graded(base_now: datetime, make_student, make_roadmap):
    """Priority 1 strictly supersedes Priority 2 when both fire simultaneously."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=1.0, triggered=True, evidence="Goal prompt changed"),
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on P-014"),
        SignalResult(name="pace_drift", score=0.40, triggered=True, evidence="Pace drift 40%"),
    ]
    intv = decide_intervention(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert intv.kind == "GOAL_REPLAN"
    assert intv.kind != "CONCEPT_NUDGE"


def test_priority_1_supersedes_priority_5_pace_ahead(base_now: datetime, make_student, make_roadmap):
    """Priority 1 strictly supersedes Priority 5 pace_ahead."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=1.0, triggered=True, evidence="Goal differs"),
        SignalResult(name="pace_ahead", score=0.35, triggered=True, evidence="35% ahead"),
    ]
    intv = decide_intervention(student, roadmap, signals, current_week=1, now=base_now)
    assert intv.kind == "GOAL_REPLAN"
    assert intv.kind != "REPLAN_UP"


def test_priority_2_stuck_graded_produces_concept_nudge(base_now: datetime, make_student, make_roadmap):
    """Priority 2: stuck on graded problem produces CONCEPT_NUDGE with prereq concept."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="6 fails on P-014"),
        SignalResult(name="pace_drift", score=0.40, triggered=True, evidence="40% drift"),
    ]
    intv = decide_intervention(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert intv.kind == "CONCEPT_NUDGE"
    assert intv.target_problem_id == "P-014"
    assert intv.concept_id == "promises-error-handling"
    assert intv.roadmap_diff is not None
    assert "P-014" in intv.rationale


def test_priority_2_stuck_graded_without_pace_drift_has_no_replan(base_now: datetime, make_student, make_roadmap):
    """Priority 2 without pace_drift attached diff is None (border check drift=0.29)."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on P-014"),
        SignalResult(name="pace_drift", score=0.29, triggered=False, evidence="29% drift"),
    ]
    intv = decide_intervention(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert intv.kind == "CONCEPT_NUDGE"
    assert intv.roadmap_diff is None


def test_priority_2_supersedes_priority_4_disengaged(base_now: datetime, make_student, make_roadmap):
    """Priority 2 strictly supersedes Priority 4 disengaged."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on P-014"),
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Inactivity 6 days"),
    ]
    intv = decide_intervention(student, roadmap, signals, target_problem_id="P-014", current_week=2, now=base_now)
    assert intv.kind == "CONCEPT_NUDGE"
    assert intv.kind != "REENGAGE"


def test_priority_3_stuck_practice_produces_hint_ladder(base_now: datetime, make_student, make_roadmap):
    """Priority 3: stuck on practice problem (graded=False) produces HINT_LADDER."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="stuck", score=0.8, triggered=True, evidence="Stuck on P-015"),
        SignalResult(name="pace_drift", score=0.35, triggered=True, evidence="35% drift"),
    ]
    intv = decide_intervention(student, roadmap, signals, target_problem_id="P-015", current_week=3, now=base_now)
    assert intv.kind == "HINT_LADDER"
    assert intv.roadmap_diff is not None


def test_priority_3_stuck_practice_without_drift_no_replan(base_now: datetime, make_student, make_roadmap):
    """Priority 3 without pace drift produces HINT_LADDER with roadmap_diff=None."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="stuck", score=0.8, triggered=True, evidence="Stuck on P-015"),
        SignalResult(name="pace_drift", score=0.10, triggered=False, evidence="Pace fine"),
    ]
    intv = decide_intervention(student, roadmap, signals, target_problem_id="P-015", current_week=3, now=base_now)
    assert intv.kind == "HINT_LADDER"
    assert intv.roadmap_diff is None


def test_priority_4_disengaged_produces_reengage(base_now: datetime, make_student, make_roadmap):
    """Priority 4: disengaged student receives REENGAGE with REPLAN_DOWN when drift triggered."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="6 days inactive"),
        SignalResult(name="pace_drift", score=0.35, triggered=True, evidence="35% drift"),
    ]
    intv = decide_intervention(student, roadmap, signals, current_week=2, now=base_now)
    assert intv.kind == "REENGAGE"
    assert intv.roadmap_diff is not None


def test_priority_4_disengaged_without_drift_no_replan(base_now: datetime, make_student, make_roadmap):
    """Priority 4 without pace drift produces REENGAGE with roadmap_diff=None."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="disengaged", score=1.0, triggered=True, evidence="Inactivity only"),
        SignalResult(name="pace_drift", score=0.15, triggered=False, evidence="Pace fine"),
    ]
    intv = decide_intervention(student, roadmap, signals, current_week=2, now=base_now)
    assert intv.kind == "REENGAGE"
    assert intv.roadmap_diff is None


def test_priority_5_pace_ahead_produces_replan_up(base_now: datetime, make_student, make_roadmap):
    """Priority 5: pace_ahead produces REPLAN_UP with attached re-plan."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="pace_ahead", score=0.30, triggered=True, evidence="30% ahead"),
    ]
    intv = decide_intervention(student, roadmap, signals, current_week=1, now=base_now)
    assert intv.kind == "REPLAN_UP"
    assert intv.roadmap_diff is not None


def test_priority_6_healthy_progress_produces_none(base_now: datetime, make_student, make_roadmap):
    """Priority 6: all signals healthy produces clean NONE with None message and diff."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="goal_change", score=0.0, triggered=False, evidence=""),
        SignalResult(name="stuck", score=0.1, triggered=False, evidence=""),
        SignalResult(name="disengaged", score=0.1, triggered=False, evidence=""),
        SignalResult(name="pace_ahead", score=0.1, triggered=False, evidence=""),
    ]
    intv = decide_intervention(student, roadmap, signals, current_week=1, now=base_now)
    assert intv.kind == "NONE"
    assert intv.message is None
    assert intv.cta is None
    assert intv.roadmap_diff is None
    assert "healthy" in intv.rationale.lower() or "no proactive intervention" in intv.rationale.lower()


# =============================================================================
# Target Problem Discovery & Concept Resolution Tests
# =============================================================================

def test_auto_discover_target_problem_from_in_progress(base_now: datetime, make_student, make_roadmap_item):
    """Policy auto-discovers the problem currently in_progress when target_problem_id is None."""
    student = make_student()
    roadmap = [
        make_roadmap_item(id="P-001", type="problem", status="done", graded=False),
        make_roadmap_item(id="P-002", type="problem", status="in_progress", graded=True, prereq_concept_ids=["async-await"]),
        make_roadmap_item(id="P-003", type="problem", status="todo", graded=False),
    ]
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stall"),
    ]
    intv = decide_intervention(student, roadmap, signals, now=base_now)
    assert intv.target_problem_id == "P-002"
    assert intv.kind == "CONCEPT_NUDGE"  # Graded in-progress problem


def test_auto_discover_target_problem_from_todo(base_now: datetime, make_student, make_roadmap_item):
    """Policy auto-discovers the first todo problem when none is in_progress."""
    student = make_student()
    roadmap = [
        make_roadmap_item(id="P-001", type="problem", status="done", graded=False),
        make_roadmap_item(id="P-002", type="problem", status="todo", graded=False),
    ]
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stall"),
    ]
    intv = decide_intervention(student, roadmap, signals, now=base_now)
    assert intv.target_problem_id == "P-002"
    assert intv.kind == "HINT_LADDER"  # Practice problem


# =============================================================================
# Aliases & Polymorphic Interface Tests
# =============================================================================

def test_policy_aliases_and_polymorphic_signatures(base_now: datetime, make_student, make_roadmap):
    """Verify evaluate_policy and decide aliases with both standard and polymorphic arguments."""
    student = make_student()
    roadmap = make_roadmap()
    signals = [
        SignalResult(name="pace_ahead", score=0.30, triggered=True, evidence="30% ahead"),
    ]

    # Test alias decide
    res1 = decide(student, roadmap, signals, current_week=1, now=base_now)
    assert res1.kind == "REPLAN_UP"

    # Test alias evaluate_policy with standard signature
    res2 = evaluate_policy(student, roadmap, signals, current_week=1, now=base_now)
    assert res2.kind == "REPLAN_UP"

    # Test evaluate_policy with polymorphic signature (student, signals, target_problem)
    target_problem = roadmap[4]  # P-014
    res3 = evaluate_policy(student, signals, target_problem, current_week=1, now=base_now)
    assert res3.kind == "REPLAN_UP"


def test_prereq_concept_matching_slug_and_bidirectional_title(base_now: datetime, make_student):
    """Prerequisite concept selection correctly matches by exact slug and bidirectional title.
    
    Ensures that when a concept in the roadmap is done (e.g. 'Promises & Error Handling'),
    even if the curated title is longer ('Promises & Error Handling Patterns'), it is correctly
    recognized as done and the policy proceeds to recommend uncompleted prerequisites.
    """
    student = make_student(id="s1", name="Student S")
    roadmap = [
        RoadmapItem(id="C-103", week=1, title="Promises & Error Handling", type="concept", est_minutes=30, status="done"),
        RoadmapItem(id="C-102", week=1, title="Async/Await Fundamentals", type="concept", est_minutes=30, status="in_progress"),
        RoadmapItem(id="P-014", week=2, title="Data Pipeline", type="problem", est_minutes=120, status="in_progress", graded=True, prereq_concept_ids=["promises-error-handling", "async-await"]),
    ]
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Repeated test failure"),
    ]
    intv = decide_intervention(student, roadmap, signals, now=base_now)
    assert intv.kind == "CONCEPT_NUDGE"
    assert intv.concept_id == "async-await"

