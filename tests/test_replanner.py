"""Unit Test Suite for Scholar Pulse Roadmap Replanner (app/replanner.py).

Verifies:
- Empty roadmap handling returns clean RoadmapDiff
- Prerequisite concept micro-lesson insertion before target problem (week <= target_problem.week)
- Graded item preservation invariant (never in diff.removed)
- Backlog spreading and capacity management (weekly load <= 600 min)
- Cascading spillover across multiple weeks
- Topological prerequisite ordering preservation
- Acceleration curriculum pull-forward (REPLAN_UP)
- Career goal curriculum realignment (GOAL_REPLAN)
- apply_roadmap_diff correctness and deterministic sorting
- Replanner aliases (generate_replan, adjust_roadmap, apply_diff)
"""

import pytest

from app.models import CURATED_CONCEPTS, RoadmapDiff, RoadmapItem
from app.replanner import (
    adjust_roadmap,
    apply_diff,
    apply_roadmap_diff,
    generate_replan,
    replan_roadmap,
)


# =============================================================================
# Empty Roadmap & Boundary Cases
# =============================================================================

def test_replanner_empty_roadmap_returns_clean_diff():
    """Empty roadmap input safely produces empty RoadmapDiff without exceptions."""
    diff = replan_roadmap([], kind="REPLAN_DOWN", current_week=1)
    assert isinstance(diff, RoadmapDiff)
    assert len(diff.added) == 0
    assert len(diff.removed) == 0
    assert len(diff.moved) == 0
    assert len(diff.retimed) == 0


def test_replanner_single_item_roadmap_under_capacity(make_roadmap_item):
    """Single item under capacity produces no moves or removals."""
    roadmap = [make_roadmap_item(id="R-1", week=1, est_minutes=120)]
    diff = replan_roadmap(roadmap, kind="REPLAN_DOWN", current_week=1)
    assert len(diff.removed) == 0
    assert len(diff.moved) == 0


# =============================================================================
# Concept Micro-Lesson Insertion
# =============================================================================

def test_concept_nudge_inserts_micro_lesson_before_target_problem(make_roadmap):
    """Concept insertion schedules micro-lesson in week <= target_problem.week."""
    roadmap = make_roadmap()
    # P-014 is in week 2
    diff = replan_roadmap(
        roadmap,
        kind="CONCEPT_NUDGE",
        target_problem_id="P-014",
        concept_id="promises-error-handling",
        current_week=2,
    )
    assert len(diff.added) >= 1
    added_concept = next(a for a in diff.added if a.id == "promises-error-handling")
    assert added_concept.id == "promises-error-handling"
    assert added_concept.week <= 2  # Prerequisite topological constraint
    assert added_concept.type == "concept"
    assert added_concept.est_minutes == CURATED_CONCEPTS["promises-error-handling"].est_minutes
    assert added_concept.status == "todo"
    assert added_concept.graded is False


# =============================================================================
# Graded Problem Preservation Invariant
# =============================================================================

def test_replanner_never_drops_graded_items(make_roadmap):
    """Re-planner must never remove any graded item in any re-plan kind."""
    roadmap = make_roadmap()
    graded_ids = {it.id for it in roadmap if it.graded}
    assert len(graded_ids) > 0

    # Test under REPLAN_DOWN
    diff_down = replan_roadmap(roadmap, kind="REPLAN_DOWN", current_week=1)
    assert not any(r_id in graded_ids for r_id in diff_down.removed)

    # Test under GOAL_REPLAN
    diff_goal = replan_roadmap(roadmap, kind="GOAL_REPLAN", current_week=1, new_goal="Machine Learning")
    assert not any(r_id in graded_ids for r_id in diff_goal.removed)


# =============================================================================
# Backlog Spreading & Capacity Management
# =============================================================================

def test_backlog_spreading_cascades_excess_items(make_roadmap_item):
    """When a week exceeds 600 min, excess items are cascaded to subsequent weeks."""
    # Week 1 has 3 items of 250 min each = 750 min > 600 min
    roadmap = [
        make_roadmap_item(id="W1-1", week=1, est_minutes=250, status="todo", graded=False),
        make_roadmap_item(id="W1-2", week=1, est_minutes=250, status="todo", graded=False),
        make_roadmap_item(id="W1-3", week=1, est_minutes=250, status="todo", graded=False),
    ]
    diff = replan_roadmap(roadmap, kind="REPLAN_DOWN", current_week=1, weekly_capacity_min=600)
    assert len(diff.moved) >= 1

    updated = apply_roadmap_diff(roadmap, diff)
    week_1_load = sum(it.est_minutes for it in updated if it.week == 1)
    assert week_1_load <= 600
    assert any(it.id == "W1-3" and it.week == 2 for it in updated)


def test_cascading_spillover_across_multiple_weeks(make_roadmap_item):
    """Spillover cascades when subsequent weeks are also at or near capacity."""
    # Week 1 has 700 min (excess 100), Week 2 has 550 min (adding 100 pushes to 650 -> cascades to Week 3)
    roadmap = [
        make_roadmap_item(id="W1-1", week=1, est_minutes=350, status="todo", graded=False),
        make_roadmap_item(id="W1-2", week=1, est_minutes=350, status="todo", graded=False),
        make_roadmap_item(id="W2-1", week=2, est_minutes=300, status="todo", graded=False),
        make_roadmap_item(id="W2-2", week=2, est_minutes=250, status="todo", graded=False),
    ]
    diff = replan_roadmap(roadmap, kind="REPLAN_DOWN", current_week=1, weekly_capacity_min=600)
    updated = apply_roadmap_diff(roadmap, diff)

    for w in [1, 2, 3]:
        load = sum(it.est_minutes for it in updated if it.week == w)
        assert load <= 600


# =============================================================================
# REPLAN_UP: Curriculum Acceleration
# =============================================================================

def test_replan_up_pulls_future_items_when_capacity_permits(make_roadmap_item):
    """REPLAN_UP pulls forward items from week 2 into week 1 when week 1 has capacity."""
    roadmap = [
        make_roadmap_item(id="W1-1", week=1, est_minutes=200, status="done"),
        make_roadmap_item(id="W2-1", week=2, est_minutes=150, status="todo", graded=False, prereq_concept_ids=[]),
        make_roadmap_item(id="W2-2", week=2, est_minutes=200, status="todo", graded=False, prereq_concept_ids=[]),
    ]
    diff = replan_roadmap(roadmap, kind="REPLAN_UP", current_week=1, weekly_capacity_min=600)
    assert len(diff.moved) >= 1
    # Check that at least W2-1 was pulled to week 1
    pulled = next((m for m in diff.moved if m["id"] == "W2-1"), None)
    assert pulled is not None
    assert pulled["to_week"] == 1


# =============================================================================
# apply_roadmap_diff Correctness
# =============================================================================

def test_apply_roadmap_diff_adds_removes_and_moves(make_roadmap_item):
    """apply_roadmap_diff correctly executes additions, removals, and moves."""
    original = [
        make_roadmap_item(id="R-1", week=1, est_minutes=100),
        make_roadmap_item(id="R-2", week=1, est_minutes=100),
    ]
    diff = RoadmapDiff(
        added=[make_roadmap_item(id="R-3", week=1, est_minutes=50)],
        removed=["R-2"],
        moved=[{"id": "R-1", "item_id": "R-1", "title": "R-1", "from_week": 1, "to_week": 2}],
    )
    result = apply_roadmap_diff(original, diff)
    result_ids = {it.id for it in result}
    assert "R-3" in result_ids
    assert "R-2" not in result_ids
    assert "R-1" in result_ids
    r1 = next(it for it in result if it.id == "R-1")
    assert r1.week == 2


# =============================================================================
# Aliases
# =============================================================================

def test_replanner_aliases_functionality(make_roadmap):
    """Verify replanner aliases generate_replan, adjust_roadmap, and apply_diff work."""
    roadmap = make_roadmap()
    d1 = generate_replan(roadmap, kind="REPLAN_DOWN", current_week=1)
    assert isinstance(d1, RoadmapDiff)

    d2 = adjust_roadmap(roadmap, kind="REPLAN_DOWN", current_week=1)
    assert isinstance(d2, RoadmapDiff)

    res = apply_diff(roadmap, d1)
    assert isinstance(res, list)
    assert len(res) >= len(roadmap)


# =============================================================================
# Invariant Fix Tests: Duplicates, Multi-Pass Topological Sort & Capacity Relief
# =============================================================================

def test_replanner_prevents_duplicate_concept_id_and_moves_forward():
    """If concept_id already exists in roadmap, do not duplicate; move it forward if needed."""
    roadmap = [
        RoadmapItem(id="promises-error-handling", week=3, title="Promises & Error Handling", type="concept", est_minutes=30, status="todo"),
        RoadmapItem(id="P-014", week=2, title="Pipeline", type="problem", est_minutes=120, status="in_progress", graded=True),
    ]
    diff = replan_roadmap(
        roadmap,
        kind="CONCEPT_NUDGE",
        current_week=2,
        target_problem_id="P-014",
        concept_id="promises-error-handling",
    )
    # diff.added must NOT contain promises-error-handling
    assert not any(a.id == "promises-error-handling" for a in diff.added)
    # diff.moved must have shifted promises-error-handling from week 3 to week 2
    assert any(m["id"] == "promises-error-handling" and m["to_week"] == 2 for m in diff.moved)

    applied = apply_roadmap_diff(roadmap, diff)
    applied_ids = [it.id for it in applied]
    # No duplicate IDs
    assert len(applied_ids) == len(set(applied_ids))
    moved_item = next(it for it in applied if it.id == "promises-error-handling")
    assert moved_item.week == 2


def test_replanner_multi_pass_topological_sorting_reversed_order():
    """Multi-pass topological sort resolves multi-step prerequisite chains regardless of list ordering."""
    from app.guardrails import validate_roadmap_diff

    # Dependency chain: item-C (week 1) -> item-B (week 1) -> item-A (week 3)
    # Listed in reverse order: item-C, item-B, item-A
    items = [
        RoadmapItem(id="item-C", week=1, title="Problem C", type="problem", est_minutes=60, status="todo", graded=False, prereq_concept_ids=["item-B"]),
        RoadmapItem(id="item-B", week=1, title="Concept B", type="concept", est_minutes=60, status="todo", graded=False, prereq_concept_ids=["item-A"]),
        RoadmapItem(id="item-A", week=3, title="Concept A", type="concept", est_minutes=60, status="todo", graded=False, prereq_concept_ids=[]),
    ]

    diff = replan_roadmap(items, kind="REPLAN_DOWN", current_week=1)
    valid, errors = validate_roadmap_diff(items, diff)
    assert valid is True, f"Topological re-planning failed with errors: {errors}"
    assert len(errors) == 0

    applied = apply_roadmap_diff(items, diff)
    by_id = {it.id: it.week for it in applied}
    assert by_id["item-A"] <= by_id["item-B"] <= by_id["item-C"]


def test_replanner_capacity_relief_after_topological_shifts():
    """Moving dependent items forward topographically never leaves destination week overloaded."""
    from app.guardrails import validate_roadmap_diff

    # Week 2 is heavily loaded (500 min). Week 1 has a dependent item (300 min) depending on a week 2 prereq.
    # Moving week 1 item to week 2 creates an 800 min overload unless post-topo capacity relief fires.
    items = [
        RoadmapItem(id="w2-p1", week=2, title="Problem 2", type="problem", est_minutes=500, status="todo", graded=False),
        RoadmapItem(id="w1-dep", week=1, title="Dependent Problem", type="problem", est_minutes=300, status="todo", graded=False, prereq_concept_ids=["w2-prereq"]),
        RoadmapItem(id="w2-prereq", week=2, title="Prerequisite Concept", type="concept", est_minutes=50, status="todo", graded=False),
    ]

    diff = replan_roadmap(items, kind="REPLAN_DOWN", current_week=1, weekly_capacity_min=600)
    valid, errors = validate_roadmap_diff(items, diff, weekly_capacity_min=600)
    assert valid is True, f"Capacity reconciliation failed with errors: {errors}"
    assert len(errors) == 0

    applied = apply_roadmap_diff(items, diff)
    weeks = {it.week for it in applied}
    for w in weeks:
        load = sum(it.est_minutes for it in applied if it.week == w)
        assert load <= 600, f"Week {w} load ({load}) exceeds 600 min"


def test_replanner_reversed_order_in_week_dependency_spillover():
    """Defect 2.1: Reversed order capacity cascade within same overloaded week."""
    from app.guardrails import validate_roadmap_diff

    items = [
        RoadmapItem(id="item-B", week=1, title="Item B", type="problem", est_minutes=400, status="todo", graded=False, prereq_concept_ids=["item-A"]),
        RoadmapItem(id="item-A", week=1, title="Item A", type="concept", est_minutes=400, status="todo", graded=False, prereq_concept_ids=[]),
    ]
    diff = replan_roadmap(items, kind="REPLAN_DOWN", current_week=1, weekly_capacity_min=600)
    valid, errors = validate_roadmap_diff(items, diff, weekly_capacity_min=600)
    assert valid is True
    applied = apply_roadmap_diff(items, diff)
    weeks = {it.id: it.week for it in applied}
    assert weeks["item-A"] <= weeks["item-B"]
    assert weeks["item-A"] == 1
    assert weeks["item-B"] == 2


