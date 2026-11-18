"""Unit Test Suite for Scholar Pulse Guardrails Engine (app/guardrails.py).

Verifies:
- Pre-generation guardrails:
  1. Consent verification (opt-out suppresses message/CTA, preserves diff)
  2. Quiet hours active study window (09:00 - 22:00 UTC) with minute-level boundary precision
  3. Weekly cap (rolling 7 days max 3 delivered proactive messages)
  4. 24h Cooldown on identical intervention kinds (23h59m vs 24h01m)
  5. Graded work rule (hint ladders blocked on graded assessments)
- Post-generation validators:
  1. Word count boundary (<= 60 allowed, 61 rejected)
  2. Code fence and backtick prohibitions on graded work
  3. Concept index membership (CURATED_CONCEPTS)
  4. Roadmap diff integrity (graded item preservation, topological ordering, capacity)
- Aliases: check_guardrails, filter_guardrails, run_pre_guardrails, validate_post_generation
"""

from datetime import datetime, timedelta, timezone
import pytest

from app.guardrails import (
    apply_guardrails,
    check_guardrails,
    filter_guardrails,
    run_post_guardrails,
    run_pre_guardrails,
    validate_nudge,
    validate_output,
    validate_post_generation,
    validate_roadmap_diff,
)
from app.models import Intervention, RoadmapDiff, RoadmapItem, Student


# =============================================================================
# Pre-Generation Guardrail 1: Consent Verification
# =============================================================================

def test_consent_disabled_suppresses_message_and_cta(base_now: datetime, make_student):
    """When consent_nudges is False, message and cta are None while roadmap_diff is preserved."""
    student = make_student(consent_nudges=False)
    diff = RoadmapDiff(added=[RoadmapItem(id="C-1", week=2, title="T", type="concept", est_minutes=25)])
    intv = Intervention(
        id="i1", student_id="priya", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="Stuck", message="Review promises", cta="Start Review",
        roadmap_diff=diff,
    )
    passed, modified = apply_guardrails(intv, student, history=[], now=base_now)
    assert passed is False
    assert modified.message is None
    assert modified.cta is None
    assert modified.roadmap_diff is not None
    assert len(modified.roadmap_diff.added) == 1
    assert any("consent" in log.lower() for log in modified.guardrail_log)


def test_consent_enabled_allows_message(base_now: datetime, make_student):
    """When consent_nudges is True, message and cta remain intact."""
    student = make_student(consent_nudges=True)
    intv = Intervention(
        id="i1", student_id="aarav", ts=base_now, kind="CONCEPT_NUDGE",
        signals=[], rationale="Stuck", message="Review async", cta="Start",
    )
    passed, modified = apply_guardrails(intv, student, history=[], now=base_now)
    assert passed is True
    assert modified.message == "Review async"
    assert modified.cta == "Start"


# =============================================================================
# Pre-Generation Guardrail 2: Quiet Hours Window (09:00 - 22:00 UTC)
# =============================================================================

@pytest.mark.parametrize("hour,minute,second,expected_deferred", [
    (8, 59, 59, True),   # 08:59:59 -> outside window (deferred)
    (9, 0, 0, False),    # 09:00:00 -> inside window (allowed)
    (21, 59, 59, False), # 21:59:59 -> inside window (allowed)
    (22, 0, 0, True),    # 22:00:00 -> outside window (deferred)
    (23, 30, 0, True),   # 23:30:00 -> outside window (deferred)
    (3, 0, 0, True),     # 03:00:00 -> outside window (deferred)
])
def test_quiet_hours_exact_boundaries(make_student, hour, minute, second, expected_deferred):
    """Quiet hours strictly enforces [09:00 - 22:00) UTC study window and defers without dropping message."""
    student = make_student(active_hours=(9, 22))
    check_time = datetime(2026, 11, 18, hour, minute, second, tzinfo=timezone.utc)
    intv = Intervention(
        id="i1", student_id="s1", ts=check_time, kind="CONCEPT_NUDGE",
        signals=[], rationale="Stuck", message="Study async", cta="Start",
    )
    passed, modified = apply_guardrails(intv, student, history=[], now=check_time)
    # Message must never be dropped/cleared during quiet hours deferral
    assert modified.message == "Study async"
    is_deferred_logged = any("deferred" in log.lower() for log in modified.guardrail_log)
    assert is_deferred_logged is expected_deferred


# =============================================================================
# Pre-Generation Guardrail 3: Weekly Cap (Max 3 proactive messages)
# =============================================================================

def test_weekly_cap_third_allowed_fourth_blocked(base_now: datetime, make_student):
    """Weekly cap allows 3rd proactive message, but strictly blocks 4th within rolling 7 days."""
    student = make_student()
    # 2 prior delivered nudges in rolling 7 days
    history_2 = [
        Intervention(id="i1", student_id="s1", ts=base_now - timedelta(days=5), kind="CONCEPT_NUDGE", signals=[], rationale="r", message="m1"),
        Intervention(id="i2", student_id="s1", ts=base_now - timedelta(days=3), kind="REENGAGE", signals=[], rationale="r", message="m2"),
    ]
    intv_3 = Intervention(id="i3", student_id="s1", ts=base_now, kind="REPLAN_UP", signals=[], rationale="r", message="m3", cta="c3")
    passed_3, mod_3 = apply_guardrails(intv_3, student, history=history_2, now=base_now)
    assert passed_3 is True
    assert mod_3.message is not None

    # 3 prior delivered nudges -> 4th must be blocked
    history_3 = history_2 + [intv_3]
    intv_4 = Intervention(id="i4", student_id="s1", ts=base_now, kind="MICRO_LESSON", signals=[], rationale="r", message="m4", cta="c4")
    passed_4, mod_4 = apply_guardrails(intv_4, student, history=history_3, now=base_now)
    assert passed_4 is False
    assert mod_4.message is None
    assert any("weekly cap" in log.lower() for log in mod_4.guardrail_log)


def test_weekly_cap_ignores_nudges_older_than_7_days(base_now: datetime, make_student):
    """Nudges older than 7 days do not count towards the rolling weekly cap."""
    student = make_student()
    history_old = [
        Intervention(id="i1", student_id="s1", ts=base_now - timedelta(days=10), kind="CONCEPT_NUDGE", signals=[], rationale="r", message="m1"),
        Intervention(id="i2", student_id="s1", ts=base_now - timedelta(days=8), kind="REENGAGE", signals=[], rationale="r", message="m2"),
        Intervention(id="i3", student_id="s1", ts=base_now - timedelta(days=2), kind="REPLAN_UP", signals=[], rationale="r", message="m3"),
    ]
    # Only 1 within rolling 7 days
    intv_new = Intervention(id="i4", student_id="s1", ts=base_now, kind="CONCEPT_NUDGE", signals=[], rationale="r", message="m4", cta="c4")
    passed, mod = apply_guardrails(intv_new, student, history=history_old, now=base_now)
    assert passed is True
    assert mod.message is not None


# =============================================================================
# Pre-Generation Guardrail 4: 24h Cooldown
# =============================================================================

def test_cooldown_blocks_same_kind_within_24h(base_now: datetime, make_student):
    """Duplicate intervention kind within 24 hours is blocked."""
    student = make_student()
    prior = [
        Intervention(id="i1", student_id="s1", ts=base_now - timedelta(hours=23, minutes=59),
                     kind="CONCEPT_NUDGE", signals=[], rationale="r", message="prior")
    ]
    curr = Intervention(id="i2", student_id="s1", ts=base_now, kind="CONCEPT_NUDGE",
                        signals=[], rationale="r", message="new", cta="cta")
    passed, mod = apply_guardrails(curr, student, history=prior, now=base_now)
    assert passed is False
    assert mod.message is None
    assert any("cooldown" in log.lower() for log in mod.guardrail_log)


def test_cooldown_allows_same_kind_after_24h(base_now: datetime, make_student):
    """Intervention kind identical to prior sent 24h 01m ago is allowed."""
    student = make_student()
    prior = [
        Intervention(id="i1", student_id="s1", ts=base_now - timedelta(hours=24, minutes=1),
                     kind="CONCEPT_NUDGE", signals=[], rationale="r", message="prior")
    ]
    curr = Intervention(id="i2", student_id="s1", ts=base_now, kind="CONCEPT_NUDGE",
                        signals=[], rationale="r", message="new", cta="cta")
    passed, mod = apply_guardrails(curr, student, history=prior, now=base_now)
    assert passed is True
    assert mod.message == "new"


def test_cooldown_allows_different_kind_within_24h(base_now: datetime, make_student):
    """Different intervention kind is not blocked by cooldown."""
    student = make_student()
    prior = [
        Intervention(id="i1", student_id="s1", ts=base_now - timedelta(hours=6),
                     kind="CONCEPT_NUDGE", signals=[], rationale="r", message="prior")
    ]
    curr = Intervention(id="i2", student_id="s1", ts=base_now, kind="REENGAGE",
                        signals=[], rationale="r", message="new", cta="cta")
    passed, mod = apply_guardrails(curr, student, history=prior, now=base_now)
    assert passed is True
    assert mod.message == "new"


# =============================================================================
# Pre-Generation Guardrail 5: Graded Work Rule
# =============================================================================

def test_graded_work_blocks_hint_ladder(base_now: datetime, make_student, make_roadmap_item):
    """Hint ladders on graded problems are strictly blocked."""
    student = make_student()
    problem = make_roadmap_item(id="P-014", graded=True)
    intv = Intervention(
        id="i1", student_id="s1", ts=base_now, kind="HINT_LADDER",
        target_problem_id="P-014", signals=[], rationale="Hint", message="Try this loop", cta="Hint",
    )
    passed, mod = apply_guardrails(intv, student, history=[], now=base_now, problem=problem)
    assert passed is False
    assert mod.message is None
    assert any("graded" in log.lower() for log in mod.guardrail_log)


def test_graded_work_allows_concept_nudge(base_now: datetime, make_student, make_roadmap_item):
    """Conceptual review on graded problems is permitted."""
    student = make_student()
    problem = make_roadmap_item(id="P-014", graded=True)
    intv = Intervention(
        id="i1", student_id="s1", ts=base_now, kind="CONCEPT_NUDGE",
        target_problem_id="P-014", signals=[], rationale="Review", message="Review async", cta="Go",
    )
    passed, mod = apply_guardrails(intv, student, history=[], now=base_now, problem=problem)
    assert passed is True
    assert mod.message == "Review async"


def test_ungraded_practice_allows_hint_ladder(base_now: datetime, make_student, make_roadmap_item):
    """Practice problems allow hint ladders."""
    student = make_student()
    problem = make_roadmap_item(id="P-015", graded=False)
    intv = Intervention(
        id="i1", student_id="s1", ts=base_now, kind="HINT_LADDER",
        target_problem_id="P-015", signals=[], rationale="Hint", message="Try this loop", cta="Hint",
    )
    passed, mod = apply_guardrails(intv, student, history=[], now=base_now, problem=problem)
    assert passed is True
    assert mod.message == "Try this loop"


# =============================================================================
# Post-Generation Validators: validate_nudge
# =============================================================================

def test_postgen_word_cap_exact_boundaries():
    """Exactly 60 words passes; 61 words fails."""
    valid_60, _ = validate_nudge("word " * 60)
    assert valid_60 is True

    valid_61, reason = validate_nudge("word " * 61)
    assert valid_61 is False
    assert "exceeds maximum" in reason


def test_postgen_code_fence_prohibition_on_graded_work():
    """Any backtick on graded work is rejected."""
    # Single backtick
    valid_single, reason_single = validate_nudge("Use `await fetch()` to solve it", is_graded=True)
    assert valid_single is False
    assert "backtick" in reason_single.lower()

    # Triple backtick
    valid_triple, reason_triple = validate_nudge("```js const x = 1; ```", is_graded=True)
    assert valid_triple is False

    # Clean message on graded work
    valid_clean, _ = validate_nudge("Review async error handling patterns thoroughly.", is_graded=True)
    assert valid_clean is True


def test_postgen_triple_backticks_prohibited_on_ungraded():
    """Triple backtick code fences prohibited on all problems."""
    valid, reason = validate_nudge("Here is code: ```js run(); ```", is_graded=False)
    assert valid is False
    assert "triple-backtick" in reason.lower()


def test_postgen_concept_membership_validation():
    """Curated concept slugs must match exact case-sensitive entries."""
    valid_slug, _ = validate_nudge("Review concept", is_graded=False, concept_id="promises-error-handling")
    assert valid_slug is True

    valid_bad, reason = validate_nudge("Review concept", is_graded=False, concept_id="nonexistent-xyz")
    assert valid_bad is False
    assert "not found" in reason.lower()


# =============================================================================
# Roadmap Diff Integrity Validator: validate_roadmap_diff
# =============================================================================

def test_validate_roadmap_diff_rejects_dropping_graded_problem(make_roadmap):
    """Validating diff rejects any removal of graded items."""
    roadmap = make_roadmap()
    diff = RoadmapDiff(removed=["P-014"])  # P-014 is graded!
    valid, errors = validate_roadmap_diff(roadmap, diff)
    assert valid is False
    assert any("graded" in e.lower() for e in errors)


def test_validate_roadmap_diff_rejects_topological_inversion(make_roadmap):
    """Validating diff rejects scheduling a prerequisite after a dependent item."""
    roadmap = make_roadmap()
    # P-014 (week 2) depends on promises-error-handling.
    # If promises-error-handling is added to week 3: inversion!
    added_item = RoadmapItem(
        id="promises-error-handling", week=3, title="Promises", type="concept", est_minutes=25, status="todo"
    )
    diff = RoadmapDiff(added=[added_item])
    valid, errors = validate_roadmap_diff(roadmap, diff)
    assert valid is False
    assert any("scheduled after dependent item" in e.lower() for e in errors)


def test_validate_roadmap_diff_rejects_capacity_exceeded(make_roadmap):
    """Validating diff rejects total load exceeding weekly_capacity_min."""
    roadmap = make_roadmap()
    # Add huge item in week 1 pushing load past 600
    huge_item = RoadmapItem(id="HUGE-1", week=1, title="Huge Project", type="project", est_minutes=500, status="todo")
    diff = RoadmapDiff(added=[huge_item])
    valid, errors = validate_roadmap_diff(roadmap, diff, weekly_capacity_min=600)
    assert valid is False
    assert any("exceeds maximum capacity" in e.lower() for e in errors)


# =============================================================================
# Aliases
# =============================================================================

def test_guardrails_aliases_functionality(base_now: datetime, make_student):
    """Verify all guardrails and validator aliases work interchangeably."""
    student = make_student()
    intv = Intervention(id="i1", student_id="s1", ts=base_now, kind="REENGAGE", rationale="r", message="Hi")

    p1, _ = check_guardrails(intv, student, history=[], now=base_now)
    assert p1 is True

    p2, _ = filter_guardrails(intv, student, history=[], now=base_now)
    assert p2 is True

    p3, _ = run_pre_guardrails(intv, student, history=[], now=base_now)
    assert p3 is True

    v1, _ = validate_post_generation("Hello student", is_graded=False)
    assert v1 is True

    v2, _ = validate_output("Hello student", is_graded=False)
    assert v2 is True


def test_run_pre_guardrails_polymorphic_signatures(base_now: datetime, make_student):
    """run_pre_guardrails supports both (student, intervention) and (intervention, student) orderings."""
    student = make_student()
    intv = Intervention(id="i1", student_id="s1", ts=base_now, kind="REENGAGE", rationale="r", message="Hi")

    # Order 1: (intervention, student)
    res1 = run_pre_guardrails(intv, student, history=[], now=base_now)
    assert res1.passed is True
    assert res1.intervention.message == "Hi"
    p1, m1 = res1  # tuple unpacking
    assert p1 is True

    # Order 2: (student, intervention)
    res2 = run_pre_guardrails(student, intv, previous_interventions=[], sim_now=base_now)
    assert res2.passed is True
    assert res2.intervention.message == "Hi"
    p2, m2 = res2  # tuple unpacking
    assert p2 is True


def test_run_post_guardrails_contract(make_roadmap):
    """run_post_guardrails validates message constraints and diff integrity."""
    # 1. Clean message and diff pass
    passed, errors = run_post_guardrails(message="Keep going, you can do this!", cta="Review Concept")
    assert passed is True
    assert len(errors) == 0

    # 2. Word cap violation
    passed_long, errors_long = run_post_guardrails(message="word " * 65)
    assert passed_long is False
    assert any("exceeds maximum" in e.lower() for e in errors_long)

    # 3. Graded problem backtick violation
    graded_item = RoadmapItem(id="P-014", week=2, title="Pipeline", type="problem", est_minutes=120, status="todo", graded=True)
    passed_code, errors_code = run_post_guardrails(
        message="Look at `let x = 1;` here.",
        target_problem=graded_item,
    )
    assert passed_code is False
    assert any("backtick" in e.lower() for e in errors_code)

    # 4. Roadmap diff removing graded problem
    roadmap = make_roadmap()
    diff = RoadmapDiff(removed=["P-014"])
    passed_diff, errors_diff = run_post_guardrails(
        message="Valid message",
        diff=diff,
        original_roadmap=roadmap,
    )
    assert passed_diff is False
    assert any("graded" in e.lower() for e in errors_diff)

