"""Unit tests for Scholar Pulse Deterministic Signal Detection Engine.

Covers all 8 signals, boundary conditions, zero-division edge cases,
clamping bounds [0.0, 1.0], evidence strings, pass overrides, and composite logic.
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.config import (
    DISENGAGED_DRIFT_WEIGHT,
    DISENGAGED_THRESHOLD,
    INACTIVITY_ABSOLUTE_DAYS_TRIGGER,
    INACTIVITY_THRESHOLD,
    PACE_AHEAD_THRESHOLD,
    PACE_DRIFT_THRESHOLD,
    REPEATED_FAILURE_THRESHOLD,
    STALL_TIME_THRESHOLD,
)
from app.models import Event, RoadmapItem, SignalResult, Student
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


@pytest.fixture
def sim_now() -> datetime:
    return datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_student() -> Student:
    return Student(
        id="std-001",
        name="Aarav Patel",
        goal_prompt="I want to become a full-stack developer",
        roadmap_goal="Full-Stack Web Development",
        consent_nudges=True,
        active_hours=(9, 22),
        weekly_capacity_min=600,
        median_gap_days=1.5,
    )


# ============================================================================
# Clamp Function Tests
# ============================================================================

def test_clamp_within_bounds():
    assert clamp(0.5) == 0.5
    assert clamp(0.0) == 0.0
    assert clamp(1.0) == 1.0


def test_clamp_exceeding_bounds():
    assert clamp(-0.5) == 0.0
    assert clamp(1.5) == 1.0
    assert clamp(100.0) == 1.0
    assert clamp(-100.0) == 0.0


# ============================================================================
# 1. repeated_failure Tests
# ============================================================================

def test_repeated_failure_zero_attempts(sim_now):
    result = compute_repeated_failure([], "P-014", sim_now)
    assert result.name == "repeated_failure"
    assert result.score == 0.0
    assert not result.triggered
    assert "0 failed attempts" in result.evidence


def test_repeated_failure_cleared_by_pass(sim_now):
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=10), type="attempt_fail", problem_id="P-014"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=5), type="attempt_fail", problem_id="P-014"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=1), type="attempt_pass", problem_id="P-014"),
    ]
    result = compute_repeated_failure(events, "P-014", sim_now)
    assert result.score == 0.0
    assert not result.triggered
    assert "passed" in result.evidence.lower()


def test_repeated_failure_below_threshold(sim_now):
    # 3 fails without matching errors: (3 - 2) / 4 = 0.25 (< 0.5)
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=i), type="attempt_fail",
              problem_id="P-014", error_signature=f"Err_{i}")
        for i in range(1, 4)
    ]
    result = compute_repeated_failure(events, "P-014", sim_now)
    assert result.score == 0.25
    assert not result.triggered


def test_repeated_failure_at_threshold(sim_now):
    # 4 fails without matching errors: (4 - 2) / 4 = 0.5 (threshold is 0.5)
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=i), type="attempt_fail",
              problem_id="P-014", error_signature=f"Err_{i}")
        for i in range(1, 5)
    ]
    result = compute_repeated_failure(events, "P-014", sim_now)
    assert result.score == 0.5
    assert result.triggered


def test_repeated_failure_with_error_bonus(sim_now):
    # 3 fails with identical error: (3 - 2) / 4 + 0.2 = 0.25 + 0.20 = 0.45
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=i), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable")
        for i in range(1, 4)
    ]
    result = compute_repeated_failure(events, "P-014", sim_now)
    assert pytest.approx(result.score, 0.001) == 0.45
    assert not result.triggered

    # 4 fails with identical error: (4 - 2) / 4 + 0.2 = 0.5 + 0.2 = 0.70
    events.append(
        Event(student_id="std-001", ts=sim_now - timedelta(hours=5), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable")
    )
    result2 = compute_repeated_failure(events, "P-014", sim_now)
    assert pytest.approx(result2.score, 0.001) == 0.70
    assert result2.triggered
    assert "TypeError:undefined_iterable" in result2.evidence


def test_repeated_failure_aarav_scenario(sim_now):
    # Aarav: 6 fails, 4 with identical error -> (6 - 2)/4 + 0.2 = 1.0 + 0.2 = 1.2 -> clamped to 1.0
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=10), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=9), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=8), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=7), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=6), type="attempt_fail",
              problem_id="P-014", error_signature="ReferenceError:data"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=5), type="attempt_fail",
              problem_id="P-014", error_signature="NetworkTimeout"),
    ]
    result = compute_repeated_failure(events, "P-014", sim_now)
    assert result.score == 1.0
    assert result.triggered
    assert "6 failed attempts" in result.evidence
    assert "TypeError:undefined_iterable" in result.evidence


def test_repeated_failure_ignores_events_outside_72h(sim_now):
    # 5 fails 80 hours ago should be ignored
    old_events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=80 + i), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError")
        for i in range(5)
    ]
    result = compute_repeated_failure(old_events, "P-014", sim_now)
    assert result.score == 0.0
    assert not result.triggered


# ============================================================================
# 2. stall_time Tests
# ============================================================================

def test_stall_time_zero_or_negative_est_minutes(sim_now):
    result = compute_stall_time([], "P-014", 0, sim_now)
    assert result.score == 0.0
    assert not result.triggered

    result_neg = compute_stall_time([], "P-014", -10, sim_now)
    assert result_neg.score == 0.0
    assert not result_neg.triggered


def test_stall_time_cleared_by_pass(sim_now):
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=2), type="session",
              problem_id="P-014", duration_min=120),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=1), type="attempt_pass",
              problem_id="P-014"),
    ]
    result = compute_stall_time(events, "P-014", 60, sim_now)
    assert result.score == 0.0
    assert not result.triggered
    assert "passed" in result.evidence.lower()


def test_stall_time_below_threshold(sim_now):
    # est = 60m. 1.5x threshold = 90m. Spent 45m -> 45 / 90 = 0.50 (< 0.6)
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=2), type="session",
              problem_id="P-014", duration_min=45),
    ]
    result = compute_stall_time(events, "P-014", 60, sim_now)
    assert pytest.approx(result.score, 0.001) == 0.50
    assert not result.triggered


def test_stall_time_at_and_above_threshold(sim_now):
    # est = 60m, threshold = 90m. Spent 54m -> 54 / 90 = 0.60 (trigger >= 0.6)
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=2), type="session",
              problem_id="P-014", duration_min=54),
    ]
    result = compute_stall_time(events, "P-014", 60, sim_now)
    assert pytest.approx(result.score, 0.001) == 0.60
    assert result.triggered

    # Aarav scenario: 72 min on 60 min problem -> 72 / 90 = 0.80
    events_aarav = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=2), type="session",
              problem_id="P-014", duration_min=72),
    ]
    result_aarav = compute_stall_time(events_aarav, "P-014", 60, sim_now)
    assert pytest.approx(result_aarav.score, 0.001) == 0.80
    assert result_aarav.triggered
    assert "Spent 72 minutes" in result_aarav.evidence


def test_stall_time_clamping_excess(sim_now):
    # Spent 200m on 60m problem -> 200 / 90 = 2.22 -> clamped to 1.0
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=2), type="session",
              problem_id="P-014", duration_min=200),
    ]
    result = compute_stall_time(events, "P-014", 60, sim_now)
    assert result.score == 1.0
    assert result.triggered


# ============================================================================
# 3. inactivity Tests
# ============================================================================

def test_inactivity_healthy_student(sim_now, sample_student):
    # Active 12 hours ago (0.5 days). median_gap = 1.5, denom = max(2, 2*1.5) = 3.0.
    # Score = 0.5 / 3.0 = 0.1666... (< 0.6)
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=12), type="session", duration_min=60),
    ]
    result = compute_inactivity(events, sample_student, sim_now)
    assert pytest.approx(result.score, abs=1e-3) == (0.5 / 3.0)
    assert not result.triggered


def test_inactivity_trigger_by_ratio(sim_now, sample_student):
    # 2.4 days inactive. denom = 3.0. Score = 2.4 / 3.0 = 0.80 (>= 0.6)
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(days=2.4), type="session", duration_min=60),
    ]
    result = compute_inactivity(events, sample_student, sim_now)
    assert pytest.approx(result.score, 0.01) == 0.80
    assert result.triggered


def test_inactivity_absolute_trigger_meera(sim_now, sample_student):
    # Inactive 6 days (Meera scenario). denom = 3.0. Score = 6 / 3 = 2.0 -> clamped to 1.0.
    # Absolute trigger >= 5.0 days also fires.
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(days=6.0), type="session", duration_min=60),
    ]
    result = compute_inactivity(events, sample_student, sim_now)
    assert result.score == 1.0
    assert result.triggered
    assert "6.0 days since last session" in result.evidence


def test_inactivity_absolute_trigger_high_median(sim_now):
    # Edge case: high median gap (5 days) -> denom = max(2, 2*5) = 10 days.
    # At 5.1 days inactive, ratio = 5.1 / 10 = 0.51 (< 0.6), but days >= 5.0 triggers it!
    lazy_student = Student(
        id="std-002",
        name="Slow Student",
        goal_prompt="Goal",
        roadmap_goal="Track",
        median_gap_days=5.0,
    )
    events = [
        Event(student_id="std-002", ts=sim_now - timedelta(days=5.1), type="session", duration_min=60),
    ]
    result = compute_inactivity(events, lazy_student, sim_now)
    assert pytest.approx(result.score, 0.01) == 0.51
    assert result.triggered  # Fired due to days_since >= 5.0


def test_inactivity_no_sessions_defaults_to_inactive(sim_now, sample_student):
    result = compute_inactivity([], sample_student, sim_now)
    assert result.score == 1.0
    assert result.triggered


# ============================================================================
# 4. pace_drift Tests
# ============================================================================

def test_pace_drift_zero_planned_minutes():
    roadmap = [
        RoadmapItem(id="R-1", week=2, title="Item 1", type="concept", est_minutes=60, status="todo"),
    ]
    # Current week is 1, so 0 planned minutes scheduled to date
    result = compute_pace_drift(roadmap, current_week=1)
    assert result.score == 0.0
    assert not result.triggered
    assert "No planned curriculum minutes" in result.evidence


def test_pace_drift_on_or_ahead_of_plan():
    roadmap = [
        RoadmapItem(id="R-1", week=1, title="Item 1", type="concept", est_minutes=100, status="done"),
        RoadmapItem(id="R-2", week=1, title="Item 2", type="problem", est_minutes=100, status="done"),
    ]
    result = compute_pace_drift(roadmap, current_week=1)
    assert result.score == 0.0
    assert not result.triggered


def test_pace_drift_below_threshold():
    # Planned: 600m, Done: 480m -> 1 - (480/600) = 0.20 (< 0.30)
    roadmap = [
        RoadmapItem(id="R-1", week=1, title="Item 1", type="concept", est_minutes=480, status="done"),
        RoadmapItem(id="R-2", week=1, title="Item 2", type="problem", est_minutes=120, status="todo"),
    ]
    result = compute_pace_drift(roadmap, current_week=1)
    assert pytest.approx(result.score, 0.001) == 0.20
    assert not result.triggered


def test_pace_drift_aarav_scenario():
    # Aarav: 360 done of 600 planned -> 1 - (360/600) = 0.40 (>= 0.30)
    roadmap = [
        RoadmapItem(id="R-1", week=1, title="Item 1", type="concept", est_minutes=360, status="done"),
        RoadmapItem(id="R-2", week=1, title="Item 2", type="problem", est_minutes=240, status="todo"),
    ]
    result = compute_pace_drift(roadmap, current_week=1)
    assert pytest.approx(result.score, 0.001) == 0.40
    assert result.triggered
    assert "40% behind plan" in result.evidence


def test_pace_drift_total_stall():
    # Planned: 600m, Done: 0m -> 1.0
    roadmap = [
        RoadmapItem(id="R-1", week=1, title="Item 1", type="concept", est_minutes=600, status="todo"),
    ]
    result = compute_pace_drift(roadmap, current_week=1)
    assert result.score == 1.0
    assert result.triggered
    assert "100% behind plan" in result.evidence


# ============================================================================
# 5. pace_ahead Tests
# ============================================================================

def test_pace_ahead_behind_or_equal():
    roadmap = [
        RoadmapItem(id="R-1", week=1, title="Item 1", type="concept", est_minutes=600, status="done"),
    ]
    result = compute_pace_ahead(roadmap, current_week=1)
    assert result.score == 0.0
    assert not result.triggered


def test_pace_ahead_below_threshold():
    # Planned: 600m, Done: 660m -> (660/600) - 1 = 0.10 (< 0.25)
    roadmap = [
        RoadmapItem(id="R-1", week=1, title="Item 1", type="concept", est_minutes=600, status="done"),
        RoadmapItem(id="R-2", week=2, title="Item 2", type="concept", est_minutes=60, status="done"),
    ]
    result = compute_pace_ahead(roadmap, current_week=1)
    assert pytest.approx(result.score, 0.001) == 0.10
    assert not result.triggered


def test_pace_ahead_rohan_scenario():
    # Rohan: 780 done of 600 planned -> (780/600) - 1 = 0.30 (>= 0.25)
    roadmap = [
        RoadmapItem(id="R-1", week=1, title="Item 1", type="concept", est_minutes=600, status="done"),
        RoadmapItem(id="R-2", week=2, title="Item 2", type="concept", est_minutes=180, status="done"),
    ]
    result = compute_pace_ahead(roadmap, current_week=1)
    assert pytest.approx(result.score, 0.001) == 0.30
    assert result.triggered
    assert "30% ahead of schedule" in result.evidence


def test_pace_ahead_clamped_at_one():
    # 1500 done of 600 planned -> 1.5 -> clamped to 1.0
    roadmap = [
        RoadmapItem(id="R-1", week=1, title="Item 1", type="concept", est_minutes=600, status="done"),
        RoadmapItem(id="R-2", week=2, title="Item 2", type="concept", est_minutes=900, status="done"),
    ]
    result = compute_pace_ahead(roadmap, current_week=1)
    assert result.score == 1.0
    assert result.triggered


# ============================================================================
# 6. goal_change Tests
# ============================================================================

def test_goal_change_no_events(sample_student):
    result = compute_goal_change([], sample_student)
    assert result.score == 0.0
    assert not result.triggered


def test_goal_change_matching_prompt(sample_student, sim_now):
    events = [
        Event(
            student_id="std-001",
            ts=sim_now - timedelta(hours=1),
            type="goal_prompt",
            goal_text="  full-stack web development  ",
        )
    ]
    result = compute_goal_change(events, sample_student)
    assert result.score == 0.0
    assert not result.triggered


def test_goal_change_different_prompt_rohan(sample_student, sim_now):
    events = [
        Event(
            student_id="std-001",
            ts=sim_now - timedelta(hours=2),
            type="goal_prompt",
            goal_text="I want to move into backend and data engineering",
        )
    ]
    result = compute_goal_change(events, sample_student)
    assert result.score == 1.0
    assert result.triggered
    assert "differs from current roadmap goal" in result.evidence


def test_goal_change_picks_latest_prompt(sample_student, sim_now):
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(days=2), type="goal_prompt", goal_text="DevOps"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=1), type="goal_prompt",
              goal_text="Full-Stack Web Development"),
    ]
    result = compute_goal_change(events, sample_student)
    assert result.score == 0.0
    assert not result.triggered


# ============================================================================
# 7. stuck Composite Tests
# ============================================================================

def test_stuck_composite_neither():
    rf = SignalResult(name="repeated_failure", score=0.2, triggered=False, evidence="rf ev")
    st = SignalResult(name="stall_time", score=0.4, triggered=False, evidence="st ev")
    result = compute_stuck(rf, st)
    assert result.score == 0.4
    assert not result.triggered


def test_stuck_composite_one_triggered():
    rf = SignalResult(name="repeated_failure", score=0.7, triggered=True, evidence="6 fails")
    st = SignalResult(name="stall_time", score=0.4, triggered=False, evidence="st ev")
    result = compute_stuck(rf, st)
    assert result.score == 0.7
    assert result.triggered
    assert "Stuck (repeated failures)" in result.evidence


def test_stuck_composite_both_triggered():
    rf = SignalResult(name="repeated_failure", score=1.0, triggered=True, evidence="6 fails on P-014")
    st = SignalResult(name="stall_time", score=0.8, triggered=True, evidence="72 min on P-014")
    result = compute_stuck(rf, st)
    assert result.score == 1.0
    assert result.triggered
    assert "6 fails" in result.evidence and "72 min" in result.evidence


# ============================================================================
# 8. disengaged Composite Tests
# ============================================================================

def test_disengaged_composite_neither():
    inact = SignalResult(name="inactivity", score=0.2, triggered=False, evidence="inact ev")
    drift = SignalResult(name="pace_drift", score=0.2, triggered=False, evidence="drift ev")
    result = compute_disengaged(inact, drift)
    assert result.score == 0.2
    assert not result.triggered


def test_disengaged_composite_inactivity_triggered():
    inact = SignalResult(name="inactivity", score=1.0, triggered=True, evidence="6 days inactive")
    drift = SignalResult(name="pace_drift", score=0.4, triggered=True, evidence="drift 40%")
    result = compute_disengaged(inact, drift)
    assert result.score == 1.0
    assert result.triggered
    assert "6 days inactive" in result.evidence


def test_disengaged_composite_drift_alone_triggers():
    # Inactivity not triggered (score 0.3), but drift is 0.80 -> 0.8 * 0.80 = 0.64 (>= 0.6)
    inact = SignalResult(name="inactivity", score=0.3, triggered=False, evidence="1 day inactive")
    drift = SignalResult(name="pace_drift", score=0.8, triggered=True, evidence="drift 80%")
    result = compute_disengaged(inact, drift)
    assert pytest.approx(result.score, 0.001) == 0.64
    assert result.triggered


# ============================================================================
# 9. detect_all_signals End-to-End Tests
# ============================================================================

def test_detect_all_signals_aarav_profile(sim_now, sample_student):
    roadmap = [
        RoadmapItem(id="R-001", week=1, title="Basics", type="concept", est_minutes=360, status="done"),
        RoadmapItem(id="P-014", week=1, title="Async Data Fetch Pipeline", type="problem",
                    est_minutes=60, status="in_progress", graded=True),
        RoadmapItem(id="R-002", week=1, title="Other", type="problem", est_minutes=180, status="todo"),
    ]
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=10), type="session", duration_min=72, problem_id="P-014"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=9), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=8), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=7), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=6), type="attempt_fail",
              problem_id="P-014", error_signature="TypeError:undefined_iterable"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=5), type="attempt_fail",
              problem_id="P-014", error_signature="OtherError"),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=4), type="attempt_fail",
              problem_id="P-014", error_signature="OtherError2"),
    ]

    signals = detect_all_signals(
        student=sample_student,
        roadmap=roadmap,
        events=events,
        current_week=1,
        target_problem_id="P-014",
        now=sim_now,
    )

    assert len(signals) == 8
    sig_map = {s.name: s for s in signals}

    assert sig_map["repeated_failure"].score == 1.0
    assert sig_map["repeated_failure"].triggered

    assert pytest.approx(sig_map["stall_time"].score, 0.01) == 0.80
    assert sig_map["stall_time"].triggered

    assert sig_map["stuck"].score == 1.0
    assert sig_map["stuck"].triggered

    assert pytest.approx(sig_map["pace_drift"].score, 0.01) == 0.40
    assert sig_map["pace_drift"].triggered

    assert not sig_map["pace_ahead"].triggered
    assert not sig_map["goal_change"].triggered


def test_detect_all_signals_auto_discovers_target_problem(sim_now, sample_student):
    roadmap = [
        RoadmapItem(id="P-099", week=1, title="Unpassed Problem", type="problem",
                    est_minutes=45, status="in_progress"),
    ]
    signals = detect_all_signals(
        student=sample_student,
        roadmap=roadmap,
        events=[],
        current_week=1,
        target_problem_id=None,  # Should auto-select P-099
        now=sim_now,
    )
    assert len(signals) == 8
    assert all(0.0 <= s.score <= 1.0 for s in signals)


# ============================================================================
# 10. Additional Edge Cases & Model Integrity Tests
# ============================================================================

def test_repeated_failure_empty_target_id(sim_now):
    result = compute_repeated_failure([], "", sim_now)
    assert result.score == 0.0
    assert not result.triggered


def test_stall_time_empty_target_id(sim_now):
    result = compute_stall_time([], "", 60, sim_now)
    assert result.score == 0.0
    assert not result.triggered


def test_stall_time_handles_none_duration(sim_now):
    events = [
        Event(student_id="std-001", ts=sim_now - timedelta(hours=1), type="session",
              problem_id="P-014", duration_min=None),
        Event(student_id="std-001", ts=sim_now - timedelta(hours=2), type="attempt_fail",
              problem_id="P-014", duration_min=54),
    ]
    result = compute_stall_time(events, "P-014", 60, sim_now)
    assert pytest.approx(result.score, 0.001) == 0.60
    assert result.triggered


def test_curated_concepts_membership():
    from app.models import CURATED_CONCEPTS, get_concept

    expected_slugs = [
        "js-closures", "async-await", "promises-error-handling", "rest-basics",
        "http-status-codes", "sql-joins", "sql-indexes", "react-state",
        "react-effects", "node-modules", "git-branching", "auth-jwt"
    ]
    assert len(CURATED_CONCEPTS) == 12
    for slug in expected_slugs:
        concept = get_concept(slug)
        assert concept is not None
        assert concept.id == slug
        assert len(concept.title) > 0
        assert len(concept.summary) > 0
        assert concept.est_minutes > 0

    assert get_concept("non-existent-concept") is None

