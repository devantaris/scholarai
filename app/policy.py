"""Scholar Pulse: Decision Policy Engine.

Implements the deterministic priority hierarchy for proactive interventions:
  Priority 1: GOAL_REPLAN (goal_change triggered)
  Priority 2: CONCEPT_NUDGE (stuck triggered on graded problem)
  Priority 3: HINT_LADDER (stuck triggered on practice problem)
  Priority 4: REENGAGE (disengaged triggered)
  Priority 5: REPLAN_UP (pace_ahead triggered)
  Priority 6: NONE (healthy progress)

Also handles:
- Target problem auto-discovery (in_progress problem -> todo problem)
- Prerequisite concept resolution from curated concept index
- Re-plan diff attachment conditions (e.g. pace_drift >= 0.30)
- Deterministic plain-English audit rationale generation
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union
import uuid

from app.config import FOLLOWUP_WINDOW_HOURS
from app.generator import generate_intervention_content, generate_template_nudge
from app.models import (
    CURATED_CONCEPTS,
    Intervention,
    RoadmapDiff,
    RoadmapItem,
    SignalResult,
    Student,
    get_concept,
)
from app.replanner import replan_roadmap


def decide_intervention(
    student: Student,
    roadmap: Union[list[RoadmapItem], list[SignalResult]],
    signals: Optional[Union[list[SignalResult], RoadmapItem, list[RoadmapItem]]] = None,
    current_week: int = 1,
    target_problem_id: Optional[str] = None,
    now: Optional[datetime] = None,
    previous_interventions: Optional[list[Intervention]] = None,
    events: Optional[list[Event]] = None,
    **kwargs: Any,
) -> Intervention:
    """Evaluate detected signals against the decision hierarchy table.
    
    Supports both standard call signature:
      decide_intervention(student, roadmap, signals, current_week=..., target_problem_id=..., now=...)
    and polymorphic SCOPE.md signature:
      evaluate_policy(student, signals, target_problem, previous_interventions=...)
      
    Returns:
        Complete Intervention domain object.
    """
    # -------------------------------------------------------------------------
    # 0. Signature Normalization
    # -------------------------------------------------------------------------
    actual_roadmap: list[RoadmapItem] = []
    actual_signals: list[SignalResult] = []

    # Check if 2nd arg is signals (polymorphic evaluate_policy call)
    if isinstance(roadmap, list) and len(roadmap) > 0 and isinstance(roadmap[0], SignalResult):
        actual_signals = roadmap  # type: ignore[assignment]
        if isinstance(signals, list) and len(signals) > 0 and isinstance(signals[0], RoadmapItem):
            actual_roadmap = signals  # type: ignore[assignment]
        elif isinstance(signals, RoadmapItem):
            actual_roadmap = [signals]
            target_problem_id = target_problem_id or signals.id
    else:
        actual_roadmap = roadmap if isinstance(roadmap, list) else []  # type: ignore[assignment]
        if isinstance(signals, list):
            actual_signals = signals  # type: ignore[assignment]

    # Map signals by name for fast lookup
    sig_map: dict[str, SignalResult] = {s.name: s for s in actual_signals}

    decision_time = now or datetime.now(timezone.utc)
    item_map: dict[str, RoadmapItem] = {item.id: item for item in actual_roadmap}

    # -------------------------------------------------------------------------
    # 1. Target Problem Auto-Discovery
    # -------------------------------------------------------------------------
    target_item: Optional[RoadmapItem] = None
    if target_problem_id and target_problem_id in item_map:
        target_item = item_map[target_problem_id]
    elif not target_problem_id:
        # Check in_progress problems
        for it in actual_roadmap:
            if it.type == "problem" and it.status == "in_progress":
                target_item = it
                target_problem_id = it.id
                break
        # If none in progress, check todo problems
        if not target_item:
            for it in actual_roadmap:
                if it.type == "problem" and it.status == "todo":
                    target_item = it
                    target_problem_id = it.id
                    break

    is_graded = target_item.graded if target_item else False
    if target_item and not target_problem_id:
        target_problem_id = target_item.id

    # -------------------------------------------------------------------------
    # 2. Prerequisite Concept Selection
    # -------------------------------------------------------------------------
    resolved_concept_id: Optional[str] = None
    if target_item and target_item.prereq_concept_ids:
        # Check if any prerequisite concept in roadmap is uncompleted
        # E.g., for Aarav: async-await is done, promises-error-handling is in_progress/todo
        candidate_slugs = [cid for cid in target_item.prereq_concept_ids if cid in CURATED_CONCEPTS]
        uncompleted_in_roadmap: list[str] = []
        for slug in candidate_slugs:
            # Check if there is an item in the roadmap for this concept
            # 1. Exact slug match by ID
            matching_item = item_map.get(slug)
            if not matching_item:
                matching_item = next((it for it in actual_roadmap if it.id == slug), None)
            # 2. Bidirectional title match
            if not matching_item:
                curated = get_concept(slug)
                if curated:
                    curated_lower = curated.title.lower()
                    matching_item = next(
                        (
                            it for it in actual_roadmap
                            if (curated_lower in it.title.lower() or it.title.lower() in curated_lower)
                        ),
                        None,
                    )
            if matching_item:
                if matching_item.status != "done":
                    uncompleted_in_roadmap.append(slug)
            else:
                uncompleted_in_roadmap.append(slug)

        if uncompleted_in_roadmap:
            resolved_concept_id = uncompleted_in_roadmap[0]
        elif candidate_slugs:
            resolved_concept_id = candidate_slugs[0]
        else:
            resolved_concept_id = target_item.prereq_concept_ids[0]

    # -------------------------------------------------------------------------
    # 3. Decision Hierarchy Table Evaluation (Priority 1 to 6)
    # -------------------------------------------------------------------------
    kind: str = "NONE"
    rationale: str = ""
    roadmap_diff: Optional[RoadmapDiff] = None
    concept_id: Optional[str] = None

    goal_change_sig = sig_map.get("goal_change")
    stuck_sig = sig_map.get("stuck")
    disengaged_sig = sig_map.get("disengaged")
    pace_drift_sig = sig_map.get("pace_drift")
    pace_ahead_sig = sig_map.get("pace_ahead")

    pace_drift_triggered = bool(pace_drift_sig and pace_drift_sig.triggered)

    # Priority 1: GOAL_REPLAN
    if goal_change_sig and goal_change_sig.triggered:
        kind = "GOAL_REPLAN"
        evidence_snippet = goal_change_sig.evidence or "New goal prompt differs from current track"
        rationale = (
            f"Goal prompt change detected: latest goal differs from roadmap goal "
            f"'{student.roadmap_goal}'; {evidence_snippet}. Initiating full curriculum re-plan."
        )
        roadmap_diff = replan_roadmap(
            actual_roadmap,
            kind="GOAL_REPLAN",
            current_week=current_week,
            new_goal=student.goal_prompt,
        )

    # Priority 2: CONCEPT_NUDGE (Stuck on Graded Problem)
    elif stuck_sig and stuck_sig.triggered and is_graded:
        kind = "CONCEPT_NUDGE"
        concept_id = resolved_concept_id
        target_name = target_problem_id or "graded assessment"
        evidence_snippet = stuck_sig.evidence or "repeated failed attempts"
        rationale = (
            f"Student is stuck on graded problem {target_name}: {evidence_snippet}. "
            f"Graded assessment rules strictly forbid hints; directing to prerequisite concept "
            f"'{concept_id or 'foundational material'}' without solution code."
        )
        # Re-plan attached IF AND ONLY IF pace_drift is triggered
        if pace_drift_triggered:
            roadmap_diff = replan_roadmap(
                actual_roadmap,
                kind="CONCEPT_NUDGE",
                target_problem_id=target_problem_id,
                concept_id=concept_id,
                current_week=current_week,
            )
        else:
            roadmap_diff = None

    # Priority 3: HINT_LADDER (Stuck on Practice Problem)
    elif stuck_sig and stuck_sig.triggered and not is_graded:
        kind = "HINT_LADDER"
        target_name = target_problem_id or "practice exercise"
        evidence_snippet = stuck_sig.evidence or "stall detected"
        rationale = (
            f"Student is stuck on practice problem {target_name}: {evidence_snippet}. "
            f"Offering progressive hint ladder guidance to unblock practical implementation."
        )
        if pace_drift_triggered:
            roadmap_diff = replan_roadmap(
                actual_roadmap,
                kind="REPLAN_DOWN",
                target_problem_id=target_problem_id,
                current_week=current_week,
            )
        else:
            roadmap_diff = None

    # Priority 4: REENGAGE (Disengaged Student)
    elif disengaged_sig and disengaged_sig.triggered:
        # Recent-success suppression: do not fire REENGAGE or disengaged interventions within 72h
        # after an attempt_pass or a recovered outcome, or if active within the last 3 days.
        cutoff_72h = decision_time - timedelta(hours=72)
        actual_events = events if events is not None else kwargs.get("events", [])
        has_recent_pass = any(
            e.type == "attempt_pass" and cutoff_72h <= e.ts <= decision_time
            for e in actual_events
        )
        has_recent_recovery = any(
            i.status == "recovered"
            and (
                (i.outcome_ts and cutoff_72h <= i.outcome_ts <= decision_time)
                or (cutoff_72h <= i.ts <= decision_time)
            )
            for i in (previous_interventions or [])
        )
        recent_session_events = [e for e in actual_events if e.type == "session" and e.ts <= decision_time]
        too_recent_session = False
        if recent_session_events:
            last_sess_ts = max(e.ts for e in recent_session_events)
            days_inactive = (decision_time - last_sess_ts).total_seconds() / 86400.0
            if days_inactive < 3.0:
                too_recent_session = True

        if has_recent_pass or has_recent_recovery or too_recent_session:
            # Recent success / recent activity suppression active: do not fire REENGAGE!
            if pace_ahead_sig and pace_ahead_sig.triggered:
                kind = "REPLAN_UP"
                evidence_snippet = pace_ahead_sig.evidence or "ahead of schedule"
                rationale = (
                    f"Student is ahead of pace: {evidence_snippet}. "
                    f"Pulling forward upcoming coursework to maintain momentum and encourage acceleration."
                )
                roadmap_diff = replan_roadmap(
                    actual_roadmap,
                    kind="REPLAN_UP",
                    current_week=current_week,
                )
            else:
                kind = "NONE"
                if has_recent_pass:
                    rationale = "Recent problem pass recorded within 72h; suppressing re-engagement intervention."
                elif has_recent_recovery:
                    rationale = "Recent recovery outcome recorded within 72h; suppressing re-engagement intervention."
                else:
                    rationale = "Student was active within the last 3 days; suppressing re-engagement intervention."
                roadmap_diff = None
        else:
            kind = "REENGAGE"
            evidence_snippet = disengaged_sig.evidence or "inactivity threshold exceeded"
            rationale = (
                f"Student disengagement detected: {evidence_snippet}. "
                f"Recommending a low-friction 10-minute warm-up task to restore study cadence."
            )
            if pace_drift_triggered:
                roadmap_diff = replan_roadmap(
                    actual_roadmap,
                    kind="REPLAN_DOWN",
                    current_week=current_week,
                )
            else:
                roadmap_diff = None

    # Priority 5: REPLAN_UP (Ahead of Schedule)
    elif pace_ahead_sig and pace_ahead_sig.triggered:
        kind = "REPLAN_UP"
        evidence_snippet = pace_ahead_sig.evidence or "ahead of schedule"
        rationale = (
            f"Student is ahead of pace: {evidence_snippet}. "
            f"Pulling forward upcoming coursework to maintain momentum and encourage acceleration."
        )
        roadmap_diff = replan_roadmap(
            actual_roadmap,
            kind="REPLAN_UP",
            current_week=current_week,
        )

    # Priority 6: NONE (Healthy Progress)
    else:
        kind = "NONE"
        rationale = "Student progress and activity levels are healthy; no proactive intervention required."
        roadmap_diff = None

    # -------------------------------------------------------------------------
    # 4. Build Candidate Intervention
    # -------------------------------------------------------------------------
    intervention_id = f"int-{uuid.uuid4().hex[:8]}"
    status = "pending_followup" if kind != "NONE" else "recovered"
    followup_due = decision_time + timedelta(hours=FOLLOWUP_WINDOW_HOURS) if kind != "NONE" else None

    intervention = Intervention(
        id=intervention_id,
        student_id=student.id,
        ts=decision_time,
        kind=kind,  # type: ignore[arg-type]
        signals=actual_signals,
        rationale=rationale,
        message=None,
        cta=None,
        target_problem_id=target_problem_id,
        concept_id=concept_id,
        roadmap_diff=roadmap_diff,
        guardrail_log=[],
        status=status,  # type: ignore[arg-type]
        followup_due=followup_due,
    )

    # -------------------------------------------------------------------------
    # 5. Message & CTA Generation via Dual-Mode Generator Pipeline
    # -------------------------------------------------------------------------
    if kind != "NONE":
        concept_obj = get_concept(concept_id) if concept_id else None
        intervention = generate_intervention_content(
            intervention=intervention,
            student=student,
            problem=target_item,
            concept=concept_obj,
        )

    return intervention


# Aliases for specification and test compatibility
evaluate_policy = decide_intervention
decide = decide_intervention
