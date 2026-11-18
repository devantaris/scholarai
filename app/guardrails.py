"""Scholar Pulse: Pre-Generation Guardrails and Post-Generation Validators.

Enforces academic integrity, student communication preferences, study hours,
temporal cooldowns, word caps, and curriculum re-planning invariants.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from app.config import (
    COOLDOWN_HOURS,
    DEFAULT_ACTIVE_HOURS,
    DEFAULT_WEEKLY_CAPACITY_MIN,
    MAX_NUDGES_PER_WEEK,
    MAX_NUDGE_WORDS,
    WEEKLY_CAP_WINDOW_DAYS,
)
from app.models import (
    CURATED_CONCEPTS,
    Intervention,
    RoadmapDiff,
    RoadmapItem,
    Student,
    get_concept,
)
from app.replanner import apply_roadmap_diff


class GuardrailCheckResult(tuple):
    """Result tuple supporting both (passed, intervention) unpacking and attribute access."""

    def __new__(cls, passed: bool, intervention: Intervention):
        return super().__new__(cls, (passed, intervention))

    @property
    def passed(self) -> bool:
        return self[0]

    @property
    def intervention(self) -> Intervention:
        return self[1]

    @property
    def modified_intervention(self) -> Intervention:
        return self[1]


def apply_guardrails(
    intervention: Intervention,
    student: Student,
    history: list[Intervention] = [],
    now: Optional[datetime] = None,
    problem: Optional[RoadmapItem] = None,
    **kwargs: Any,
) -> GuardrailCheckResult:
    """Execute pre-generation guardrail checks in strict priority order.
    
    Order:
      1. Consent verification (student.consent_nudges)
      2. Quiet hours check (student.active_hours or DEFAULT_ACTIVE_HOURS)
      3. Weekly cap check (rolling 7 days max 3 delivered nudges)
      4. 24h Cooldown check (duplicate intervention kind)
      5. Graded work rule (hint ladders strictly forbidden on graded work)
      
    Args:
        intervention: The candidate Intervention produced by the Policy Engine.
        student: Target student record.
        history: Previous interventions received by this student.
        now: Current simulated UTC time.
        problem: Target problem item (if applicable).
        
    Returns:
        GuardrailCheckResult(passed, modified_intervention) where passed indicates delivery.
    """
    modified = intervention.model_copy(deep=True)
    if modified.guardrail_log is None:
        modified.guardrail_log = []

    current_time = now or modified.ts or datetime.now(timezone.utc)

    # -------------------------------------------------------------------------
    # 1. Consent Verification
    # -------------------------------------------------------------------------
    if not student.consent_nudges:
        modified.message = None
        modified.cta = None
        modified.status = "blocked"
        modified.followup_due = None
        modified.guardrail_log.append(
            "Blocked: Student opted out of proactive nudges (consent_nudges=False); "
            "proactive message suppressed, in-app roadmap diff preserved."
        )
        return GuardrailCheckResult(False, modified)

    modified.guardrail_log.append(
        "Consent check: PASSED. Student opted in to proactive nudges."
    )

    # -------------------------------------------------------------------------
    # 2. Quiet Hours Window Check
    # -------------------------------------------------------------------------
    start_hour, end_hour = student.active_hours or DEFAULT_ACTIVE_HOURS
    is_quiet_hours = current_time.hour < start_hour or current_time.hour >= end_hour
    if is_quiet_hours:
        # Message is DEFERRED to next window (NOT dropped/cleared)
        modified.status = "deferred"
        modified.followup_due = None
        modified.guardrail_log.append(
            f"Quiet hours check: DEFERRED. Evaluation time {current_time.strftime('%H:%M:%S')} UTC "
            f"is outside active study window [{start_hour:02d}:00 - {end_hour:02d}:00 UTC]; "
            f"message deferred to next morning window."
        )
    else:
        modified.guardrail_log.append(
            f"Quiet hours check: PASSED. Evaluation time {current_time.strftime('%H:%M:%S')} UTC "
            f"is within active study window [{start_hour:02d}:00 - {end_hour:02d}:00 UTC]."
        )

    # -------------------------------------------------------------------------
    # 3. Weekly Cap Check (Max 3 proactive messages in rolling 7 days)
    # -------------------------------------------------------------------------
    cap_window_start = current_time - timedelta(days=WEEKLY_CAP_WINDOW_DAYS)
    rolling_delivered = [
        intv for intv in history
        if intv.message is not None
        and intv.status not in ["blocked", "deferred"]
        and cap_window_start <= intv.ts <= current_time
    ]
    if len(rolling_delivered) >= MAX_NUDGES_PER_WEEK:
        modified.message = None
        modified.cta = None
        modified.status = "blocked"
        modified.followup_due = None
        modified.guardrail_log.append(
            f"Weekly cap check: BLOCKED. Student has received {len(rolling_delivered)}/{MAX_NUDGES_PER_WEEK} "
            f"proactive nudges in rolling 7-day window; 4th nudge suppressed."
        )
        return GuardrailCheckResult(False, modified)

    modified.guardrail_log.append(
        f"Weekly cap check: PASSED. Student has received {len(rolling_delivered)}/{MAX_NUDGES_PER_WEEK} "
        f"proactive nudges in rolling 7-day window."
    )

    # -------------------------------------------------------------------------
    # 4. 24h Cooldown Check (Duplicate intervention kind within 24 hours)
    # -------------------------------------------------------------------------
    cooldown_cutoff = current_time - timedelta(hours=COOLDOWN_HOURS)
    duplicate_recent = any(
        intv.kind == modified.kind
        and intv.message is not None
        and intv.status not in ["blocked", "deferred"]
        and cooldown_cutoff <= intv.ts <= current_time
        for intv in history
    )
    if duplicate_recent:
        modified.message = None
        modified.cta = None
        modified.status = "blocked"
        modified.followup_due = None
        modified.guardrail_log.append(
            f"Cooldown check: BLOCKED. 24h cooldown active for intervention kind '{modified.kind}'."
        )
        return GuardrailCheckResult(False, modified)

    modified.guardrail_log.append(
        f"Cooldown check: PASSED. No prior '{modified.kind}' sent within the last 24h."
    )

    # -------------------------------------------------------------------------
    # 5. Graded-Work Rule (No hints or solution code on graded problems)
    # -------------------------------------------------------------------------
    is_graded = False
    if problem is not None:
        is_graded = problem.graded

    if is_graded and modified.kind == "HINT_LADDER":
        modified.message = None
        modified.cta = None
        modified.status = "blocked"
        modified.followup_due = None
        modified.guardrail_log.append(
            f"Graded-work rule: BLOCKED. Problem '{problem.id if problem else 'target'}' "
            f"is a graded assessment. Hint ladders and solution guidance are strictly forbidden."
        )
        return GuardrailCheckResult(False, modified)

    if is_graded:
        modified.guardrail_log.append(
            "Graded-work rule: PASSED. Intervention uses conceptual review without code or hints."
        )
    else:
        modified.guardrail_log.append(
            "Graded-work rule: PASSED. Non-graded practice work permits hint ladders."
        )

    if is_quiet_hours:
        modified.status = "deferred"
        modified.followup_due = None
        return GuardrailCheckResult(False, modified)

    return GuardrailCheckResult(modified.message is not None, modified)


def run_pre_guardrails(
    arg1: Any,
    arg2: Any,
    history: Optional[list[Intervention]] = None,
    now: Optional[datetime] = None,
    problem: Optional[RoadmapItem] = None,
    previous_interventions: Optional[list[Intervention]] = None,
    sim_now: Optional[datetime] = None,
    **kwargs: Any,
) -> GuardrailCheckResult:
    """Execute pre-generation guardrail checks with polymorphic parameter ordering.
    
    Supports:
      - (intervention, student, history, now, problem)
      - (student, intervention, previous_interventions, sim_now, problem)
    """
    if isinstance(arg1, Student) or hasattr(arg1, "consent_nudges"):
        student = arg1
        intervention = arg2
    else:
        intervention = arg1
        student = arg2

    effective_history = history if history is not None else (previous_interventions or [])
    effective_now = now or sim_now

    return apply_guardrails(
        intervention=intervention,
        student=student,
        history=effective_history,
        now=effective_now,
        problem=problem,
        **kwargs,
    )


# Aliases for pre-generation guardrails
check_guardrails = run_pre_guardrails
filter_guardrails = run_pre_guardrails


# =============================================================================
# Post-Generation Validators
# =============================================================================

def validate_nudge(
    message: str,
    is_graded: bool = False,
    concept_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """Validate a generated nudge message against length, syntax, and index constraints.
    
    Rules:
      1. Word cap: strictly <= 60 words (len(message.split()) <= 60).
      2. Code fence prohibition:
         - On graded problems: any backticks (`` ` `` or `` ``` ``) are strictly forbidden.
         - On all problems: markdown code blocks (`` ``` ``) are forbidden.
      3. Curated concept membership: if concept_id is given, must exist in CURATED_CONCEPTS.
      
    Args:
        message: Generated nudge text.
        is_graded: Whether target problem is a graded assessment.
        concept_id: Concept identifier referenced by the intervention.
        
    Returns:
        (valid, validated_message_or_reason)
    """
    if not message or not message.strip():
        return False, "Message is empty or whitespace only"

    words = message.strip().split()
    if len(words) > MAX_NUDGE_WORDS:
        return False, f"Word count ({len(words)}) exceeds maximum of {MAX_NUDGE_WORDS} words"

    # Code fence prohibitions
    if is_graded:
        if "`" in message:
            return False, "Message contains backtick code on a graded assessment"
    else:
        if "```" in message:
            return False, "Message contains triple-backtick code fences (```)"

    # Concept membership check
    if concept_id:
        if get_concept(concept_id) is None:
            return False, f"Concept '{concept_id}' not found in CURATED_CONCEPTS index"

    return True, message


# Aliases for post-generation validation
validate_post_generation = validate_nudge
validate_output = validate_nudge


def validate_roadmap_diff(
    original_roadmap: list[RoadmapItem],
    diff: RoadmapDiff,
    weekly_capacity_min: int = DEFAULT_WEEKLY_CAPACITY_MIN,
) -> Tuple[bool, list[str]]:
    """Validate a proposed RoadmapDiff for structural and academic integrity.
    
    Invariants:
      1. Graded Preservation: Never drops a graded item (diff.removed cannot contain graded items).
      2. Topological Order: Prerequisites must precede or share the week of dependent items.
      3. Capacity Constraint: Total load per week must not exceed weekly_capacity_min.
      
    Args:
        original_roadmap: Baseline list of RoadmapItems.
        diff: Proposed RoadmapDiff to validate.
        weekly_capacity_min: Maximum permitted minutes per week.
        
    Returns:
        (valid, list_of_errors)
    """
    errors: list[str] = []

    # 1. Graded item preservation
    graded_ids = {item.id for item in original_roadmap if item.graded}
    dropped_graded = [item_id for item_id in diff.removed if item_id in graded_ids]
    if dropped_graded:
        errors.append(f"Re-plan proposal illegally removes graded problem(s): {dropped_graded}")

    # Build updated roadmap using apply_roadmap_diff
    adjusted_roadmap = apply_roadmap_diff(original_roadmap, diff)
    item_map = {item.id: item for item in adjusted_roadmap}

    # 2. Topological prerequisite ordering
    for item in adjusted_roadmap:
        for prereq_id in item.prereq_concept_ids:
            if prereq_id in item_map:
                prereq_item = item_map[prereq_id]
                if prereq_item.week > item.week:
                    errors.append(
                        f"Prerequisite '{prereq_id}' (scheduled week {prereq_item.week}) "
                        f"is scheduled after dependent item '{item.id}' (scheduled week {item.week})"
                    )

    # 3. Weekly capacity constraint
    weeks_present = {item.week for item in adjusted_roadmap}
    for w in sorted(weeks_present):
        week_load = sum(item.est_minutes for item in adjusted_roadmap if item.week == w)
        if week_load > weekly_capacity_min:
            errors.append(
                f"Week {w} load ({week_load} min) exceeds maximum capacity ({weekly_capacity_min} min)"
            )

    return len(errors) == 0, errors


def run_post_guardrails(
    message: Optional[str] = None,
    cta: Optional[str] = None,
    target_problem: Optional[RoadmapItem] = None,
    diff: Optional[RoadmapDiff] = None,
    weekly_capacity_min: int = DEFAULT_WEEKLY_CAPACITY_MIN,
    original_roadmap: Optional[list[RoadmapItem]] = None,
    concept_id: Optional[str] = None,
    is_graded: Optional[bool] = None,
    **kwargs: Any,
) -> Tuple[bool, list[str]]:
    """Validate intervention output artifacts after generation against academic and structural constraints.
    
    Invariants:
      1. Word cap (strictly <= 60 words) and non-empty message.
      2. Code fence prohibition: no backticks on graded problems; no triple-backticks anywhere.
      3. Concept index membership in CURATED_CONCEPTS (if concept_id provided).
      4. Roadmap diff integrity: graded items preserved, topological order satisfied,
         and weekly capacity <= weekly_capacity_min (if diff provided).
         
    Returns:
        (valid, list_of_errors)
    """
    errors: list[str] = []

    # 1. Message validation
    if message is not None:
        graded = is_graded if is_graded is not None else (target_problem.graded if target_problem else False)
        valid_msg, msg_reason = validate_nudge(
            message=message,
            is_graded=graded,
            concept_id=concept_id,
        )
        if not valid_msg:
            errors.append(msg_reason)

    # 2. Roadmap diff validation
    if diff is not None:
        if original_roadmap is not None:
            valid_diff, diff_errors = validate_roadmap_diff(
                original_roadmap=original_roadmap,
                diff=diff,
                weekly_capacity_min=weekly_capacity_min,
            )
            if not valid_diff:
                errors.extend(diff_errors)
        else:
            if target_problem and target_problem.graded and target_problem.id in diff.removed:
                errors.append(f"Diff removes graded problem '{target_problem.id}'")

    return len(errors) == 0, errors

