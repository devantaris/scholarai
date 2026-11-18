"""Scholar Pulse: Deterministic Signal Detection Engine.

Implements pure detector functions for the 6 core signals and 2 composite signals:
- repeated_failure: Detects high failure counts on a single problem within 72h.
- stall_time: Ratio of active duration on an unpassed problem to estimated time.
- inactivity: Measures elapsed days since the last study session vs median gap.
- pace_drift: Divergence between completed minutes and planned schedule.
- pace_ahead: Extent to which completed work exceeds scheduled minutes.
- goal_change: Detects if the student's recent goal prompt differs from roadmap.
- stuck: Composite max of repeated_failure and stall_time.
- disengaged: Composite max of inactivity and weighted pace_drift.
"""

from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from app.config import (
    DISENGAGED_DRIFT_WEIGHT,
    DISENGAGED_THRESHOLD,
    INACTIVITY_ABSOLUTE_DAYS_TRIGGER,
    INACTIVITY_GAP_MULTIPLIER,
    INACTIVITY_MIN_DENOMINATOR_DAYS,
    INACTIVITY_THRESHOLD,
    PACE_AHEAD_THRESHOLD,
    PACE_DRIFT_THRESHOLD,
    REPEATED_FAILURE_BASELINE,
    REPEATED_FAILURE_DIVISOR,
    REPEATED_FAILURE_ERROR_BONUS,
    REPEATED_FAILURE_THRESHOLD,
    REPEATED_FAILURE_WINDOW_HOURS,
    STALL_TIME_MULTIPLIER,
    STALL_TIME_THRESHOLD,
)
from app.models import Event, RoadmapItem, SignalResult, Student


def clamp(val: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp a floating-point value to [low, high]."""
    return max(low, min(high, float(val)))


def compute_repeated_failure(
    events: list[Event],
    target_problem_id: str,
    now: datetime,
) -> SignalResult:
    """Detect repeated failed attempts on target_problem_id within 72 hours.

    Score is zero if the problem has any pass. Otherwise:
    raw_score = (fails - 2) / 4 + (0.2 if >=3 share same error signature else 0.0)
    Clamped to [0.0, 1.0]. Trigger: score >= 0.5.
    """
    if not target_problem_id:
        return SignalResult(
            name="repeated_failure",
            score=0.0,
            triggered=False,
            evidence="No target problem specified",
        )

    # If target problem has ANY attempt_pass up to `now`, repeated failure is cleared.
    has_pass = any(
        e.problem_id == target_problem_id and e.type == "attempt_pass" and e.ts <= now
        for e in events
    )
    if has_pass:
        return SignalResult(
            name="repeated_failure",
            score=0.0,
            triggered=False,
            evidence=f"Problem {target_problem_id} passed; repeated failure cleared",
        )

    # Filter attempts within 72h window
    window_start = now - timedelta(hours=REPEATED_FAILURE_WINDOW_HOURS)
    fails_in_window = [
        e for e in events
        if e.problem_id == target_problem_id
        and e.type == "attempt_fail"
        and window_start <= e.ts <= now
    ]

    fail_count = len(fails_in_window)
    if fail_count == 0:
        return SignalResult(
            name="repeated_failure",
            score=0.0,
            triggered=False,
            evidence=f"0 failed attempts on {target_problem_id} in the last 72h",
        )

    # Check for identical error signature bonus (>=3 with identical signature)
    sig_counter = Counter(
        e.error_signature for e in fails_in_window if e.error_signature
    )
    has_bonus = False
    max_sig_count = 0
    top_sig = None
    if sig_counter:
        top_sig, max_sig_count = sig_counter.most_common(1)[0]
        if max_sig_count >= 3:
            has_bonus = True

    bonus = REPEATED_FAILURE_ERROR_BONUS if has_bonus else 0.0
    raw_score = (fail_count - REPEATED_FAILURE_BASELINE) / REPEATED_FAILURE_DIVISOR + bonus
    score = clamp(raw_score, 0.0, 1.0)
    triggered = score >= REPEATED_FAILURE_THRESHOLD

    if top_sig:
        evidence = (
            f"{fail_count} failed attempts on {target_problem_id} in the last 72h, "
            f"{max_sig_count} with the same error {top_sig}"
        )
    else:
        evidence = f"{fail_count} failed attempts on {target_problem_id} in the last 72h"

    return SignalResult(
        name="repeated_failure",
        score=score,
        triggered=triggered,
        evidence=evidence,
    )


def compute_stall_time(
    events: list[Event],
    target_problem_id: str,
    est_minutes: int,
    now: Optional[datetime] = None,
) -> SignalResult:
    """Compute stall time ratio for an unpassed problem.

    raw_score = minutes_on_problem / (est_minutes * 1.5)
    Clamped to [0.0, 1.0]. Trigger: score >= 0.6.
    """
    if not target_problem_id:
        return SignalResult(
            name="stall_time",
            score=0.0,
            triggered=False,
            evidence="No target problem specified",
        )

    # Pass check
    has_pass = any(
        e.problem_id == target_problem_id
        and e.type == "attempt_pass"
        and (now is None or e.ts <= now)
        for e in events
    )
    if has_pass:
        return SignalResult(
            name="stall_time",
            score=0.0,
            triggered=False,
            evidence=f"Problem {target_problem_id} passed; stall cleared",
        )

    if est_minutes <= 0:
        return SignalResult(
            name="stall_time",
            score=0.0,
            triggered=False,
            evidence=f"Invalid estimated duration ({est_minutes} min) for {target_problem_id}",
        )

    # Sum duration of attempts and sessions on problem
    matching_events = [
        e for e in events
        if e.problem_id == target_problem_id
        and (now is None or e.ts <= now)
    ]
    minutes_on_problem = sum(e.duration_min or 0 for e in matching_events)

    threshold_minutes = est_minutes * STALL_TIME_MULTIPLIER
    raw_score = minutes_on_problem / threshold_minutes
    score = clamp(raw_score, 0.0, 1.0)
    triggered = score >= STALL_TIME_THRESHOLD

    evidence = (
        f"Spent {minutes_on_problem} minutes on {target_problem_id} "
        f"(estimated {est_minutes} min, 1.5x threshold {threshold_minutes:.0f} min) without passing"
    )

    return SignalResult(
        name="stall_time",
        score=score,
        triggered=triggered,
        evidence=evidence,
    )


def compute_inactivity(
    events: list[Event],
    student: Student,
    now: datetime,
) -> SignalResult:
    """Measure elapsed days since latest session against median gap.

    days_since = (now - max(session_ts)).total_seconds() / 86400.0
    denom = max(2.0, 2.0 * student.median_gap_days)
    raw_score = days_since / denom
    Clamped to [0.0, 1.0]. Trigger: score >= 0.6 or days_since >= 5.0.
    """
    session_events = [
        e for e in events
        if e.type == "session" and e.ts <= now
    ]

    median_gap = student.median_gap_days if student.median_gap_days > 0 else 1.0
    denom = max(
        INACTIVITY_MIN_DENOMINATOR_DAYS,
        INACTIVITY_GAP_MULTIPLIER * median_gap,
    )

    if not session_events:
        # Fallback to any past event if no explicit session event exists
        past_events = [e for e in events if e.ts <= now]
        if past_events:
            last_ts = max(e.ts for e in past_events)
            days_since = max(0.0, (now - last_ts).total_seconds() / 86400.0)
        else:
            days_since = 30.0  # Prolonged inactivity default
    else:
        last_ts = max(e.ts for e in session_events)
        days_since = max(0.0, (now - last_ts).total_seconds() / 86400.0)

    raw_score = days_since / denom
    score = clamp(raw_score, 0.0, 1.0)
    triggered = (score >= INACTIVITY_THRESHOLD) or (days_since >= INACTIVITY_ABSOLUTE_DAYS_TRIGGER)

    trigger_point = min(INACTIVITY_ABSOLUTE_DAYS_TRIGGER, INACTIVITY_THRESHOLD * denom)
    evidence = (
        f"{days_since:.1f} days since last session "
        f"(median gap {median_gap:.1f} days, trigger threshold {trigger_point:.1f} days)"
    )

    return SignalResult(
        name="inactivity",
        score=score,
        triggered=triggered,
        evidence=evidence,
    )


def compute_pace_drift(
    roadmap: list[RoadmapItem],
    current_week: int,
) -> SignalResult:
    """Measure divergence between planned curriculum minutes and actual done minutes.

    raw_score = 1.0 - (actual_min / planned_min)
    Clamped to [0.0, 1.0]. Trigger: score >= 0.30 (30% behind plan).
    """
    planned_min = sum(
        item.est_minutes for item in roadmap if item.week <= current_week
    )
    actual_min = sum(
        item.est_minutes for item in roadmap if item.status == "done"
    )

    if planned_min <= 0:
        return SignalResult(
            name="pace_drift",
            score=0.0,
            triggered=False,
            evidence="No planned curriculum minutes scheduled to date",
        )

    raw_score = 1.0 - (actual_min / planned_min)
    score = clamp(raw_score, 0.0, 1.0)
    triggered = score >= PACE_DRIFT_THRESHOLD
    drift_pct = score * 100.0

    evidence = (
        f"Completed {actual_min} of {planned_min} planned minutes to date "
        f"({drift_pct:.0f}% behind plan)"
    )

    return SignalResult(
        name="pace_drift",
        score=score,
        triggered=triggered,
        evidence=evidence,
    )


def compute_pace_ahead(
    roadmap: list[RoadmapItem],
    current_week: int,
) -> SignalResult:
    """Measure whether completed work exceeds planned schedule.

    raw_score = (actual_min / planned_min) - 1.0
    Clamped to [0.0, 1.0]. Trigger: score >= 0.25 (25% ahead of plan).
    """
    planned_min = sum(
        item.est_minutes for item in roadmap if item.week <= current_week
    )
    actual_min = sum(
        item.est_minutes for item in roadmap if item.status == "done"
    )

    if planned_min <= 0 or actual_min <= planned_min:
        return SignalResult(
            name="pace_ahead",
            score=0.0,
            triggered=False,
            evidence=f"Completed {actual_min} of {planned_min} planned minutes to date (on or behind schedule)",
        )

    raw_score = (actual_min / planned_min) - 1.0
    score = clamp(raw_score, 0.0, 1.0)
    triggered = score >= PACE_AHEAD_THRESHOLD
    ahead_pct = ((actual_min / planned_min) - 1.0) * 100.0

    evidence = (
        f"Completed {actual_min} of {planned_min} planned minutes to date "
        f"({ahead_pct:.0f}% ahead of schedule)"
    )

    return SignalResult(
        name="pace_ahead",
        score=score,
        triggered=triggered,
        evidence=evidence,
    )


def compute_goal_change(
    events: list[Event],
    student: Student,
) -> SignalResult:
    """Detect if the student's latest goal prompt differs from their current roadmap goal.

    Score is 1.0 if different (trigger == True), else 0.0 (trigger == False).
    """
    goal_events = [
        e for e in events
        if e.type == "goal_prompt" and e.goal_text
    ]

    if not goal_events:
        return SignalResult(
            name="goal_change",
            score=0.0,
            triggered=False,
            evidence=f"Current roadmap matches student goal '{student.roadmap_goal}'",
        )

    latest_prompt = max(goal_events, key=lambda e: e.ts)
    goal_text = (latest_prompt.goal_text or "").strip()

    if goal_text.lower() != student.roadmap_goal.strip().lower():
        return SignalResult(
            name="goal_change",
            score=1.0,
            triggered=True,
            evidence=f"Goal prompt '{goal_text}' differs from current roadmap goal '{student.roadmap_goal}'",
        )

    return SignalResult(
        name="goal_change",
        score=0.0,
        triggered=False,
        evidence=f"Current roadmap matches student goal '{student.roadmap_goal}'",
    )


def compute_stuck(
    repeated_failure: SignalResult,
    stall_time: SignalResult,
) -> SignalResult:
    """Composite detector: max(repeated_failure, stall_time).

    Trigger is OR of both component triggers.
    """
    score = clamp(max(repeated_failure.score, stall_time.score), 0.0, 1.0)
    triggered = repeated_failure.triggered or stall_time.triggered

    if triggered:
        if repeated_failure.triggered and stall_time.triggered:
            evidence = f"Stuck: {repeated_failure.evidence}; {stall_time.evidence}"
        elif repeated_failure.triggered:
            evidence = f"Stuck (repeated failures): {repeated_failure.evidence}"
        else:
            evidence = f"Stuck (stall time): {stall_time.evidence}"
    else:
        evidence = "Student is not stuck on current tasks"

    return SignalResult(
        name="stuck",
        score=score,
        triggered=triggered,
        evidence=evidence,
    )


def compute_disengaged(
    inactivity: SignalResult,
    pace_drift: SignalResult,
) -> SignalResult:
    """Composite detector: max(inactivity, 0.8 * pace_drift).

    Trigger: inactivity.triggered or ((0.8 * pace_drift.score) >= 0.6).
    """
    weighted_drift = DISENGAGED_DRIFT_WEIGHT * pace_drift.score
    score = clamp(max(inactivity.score, weighted_drift), 0.0, 1.0)
    triggered = inactivity.triggered or (weighted_drift >= DISENGAGED_THRESHOLD)

    if triggered:
        evidence = (
            f"Disengaged: {inactivity.evidence} "
            f"(weighted drift score: {weighted_drift:.2f})"
        )
    else:
        evidence = "Student engagement and activity levels are healthy"

    return SignalResult(
        name="disengaged",
        score=score,
        triggered=triggered,
        evidence=evidence,
    )


def detect_all_signals(
    student: Student,
    roadmap: list[RoadmapItem],
    events: list[Event],
    current_week: int,
    target_problem_id: Optional[str],
    now: datetime,
) -> list[SignalResult]:
    """Execute all 8 signal detectors and return ordered list of results.

    Returns:
    [
        repeated_failure,
        stall_time,
        inactivity,
        pace_drift,
        pace_ahead,
        goal_change,
        stuck,
        disengaged
    ]
    """
    # If no target problem was explicitly given, try finding the active in-progress or first unpassed problem
    est_minutes = 30
    if not target_problem_id:
        # Check roadmap for in_progress problem
        in_prog = [item for item in roadmap if item.status == "in_progress" and item.type == "problem"]
        if in_prog:
            target_problem_id = in_prog[0].id
            est_minutes = in_prog[0].est_minutes
        else:
            # Check for first todo problem
            todo_probs = [item for item in roadmap if item.status == "todo" and item.type == "problem"]
            if todo_probs:
                target_problem_id = todo_probs[0].id
                est_minutes = todo_probs[0].est_minutes
    else:
        # Find item in roadmap to get estimated minutes
        matching = [item for item in roadmap if item.id == target_problem_id]
        if matching:
            est_minutes = matching[0].est_minutes

    rf = compute_repeated_failure(events, target_problem_id or "", now)
    st = compute_stall_time(events, target_problem_id or "", est_minutes, now)
    inact = compute_inactivity(events, student, now)
    drift = compute_pace_drift(roadmap, current_week)
    ahead = compute_pace_ahead(roadmap, current_week)
    goal = compute_goal_change(events, student)
    stuck = compute_stuck(rf, st)
    disengaged = compute_disengaged(inact, drift)

    return [rf, st, inact, drift, ahead, goal, stuck, disengaged]
