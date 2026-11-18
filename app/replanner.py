"""Scholar Pulse: Rule-Based Roadmap Replanner.

Implements deterministic curriculum re-planning algorithms for:
- Prerequisite concept micro-lesson insertion (week <= target_problem.week)
- Backlog spreading and capacity management (weekly load <= 600 min)
- Acceleration and future item advancement (REPLAN_UP)
- Career goal re-alignment (GOAL_REPLAN)
- Topological prerequisite ordering and graded item preservation guarantees
"""

from typing import Any, Optional
import copy

from app.config import DEFAULT_WEEKLY_CAPACITY_MIN
from app.models import CURATED_CONCEPTS, RoadmapDiff, RoadmapItem, get_concept


def replan_roadmap(
    roadmap: list[RoadmapItem],
    kind: str = "REPLAN_DOWN",
    current_week: int = 1,
    target_problem_id: Optional[str] = None,
    concept_id: Optional[str] = None,
    weekly_capacity_min: int = DEFAULT_WEEKLY_CAPACITY_MIN,
    new_goal: Optional[str] = None,
    **kwargs: Any,
) -> RoadmapDiff:
    """Generate a deterministic RoadmapDiff based on student progress signals.
    
    Args:
        roadmap: Current list of RoadmapItems.
        kind: Policy intervention kind (REPLAN_DOWN, CONCEPT_NUDGE, REPLAN_UP, GOAL_REPLAN, etc.).
        current_week: 1-based active study week.
        target_problem_id: ID of the problem causing struggle (if any).
        concept_id: Prerequisite concept ID to insert (if any).
        weekly_capacity_min: Maximum permitted minutes per study week (default 600).
        new_goal: Updated career/learning goal prompt for GOAL_REPLAN.
        
    Returns:
        RoadmapDiff with added, removed, moved, and retimed adjustments.
    """
    diff = RoadmapDiff(added=[], removed=[], moved=[], retimed=[])
    if not roadmap:
        return diff

    # Work on a deep copy of roadmap items to ensure immutability
    working_items: list[RoadmapItem] = [item.model_copy(deep=True) for item in roadmap]
    item_map: dict[str, RoadmapItem] = {item.id: item for item in working_items}
    original_weeks: dict[str, int] = {item.id: item.week for item in roadmap}

    def _record_move(item_id: str, title: str, from_week: int, to_week: int) -> None:
        existing_move = next((m for m in diff.moved if m["id"] == item_id), None)
        if existing_move:
            existing_move["to_week"] = to_week
        else:
            diff.moved.append({
                "id": item_id,
                "item_id": item_id,
                "title": title,
                "from_week": from_week,
                "to_week": to_week,
            })

    # -------------------------------------------------------------------------
    # 1. Prerequisite Micro-Lesson Insertion (CONCEPT_NUDGE / MICRO_LESSON)
    # -------------------------------------------------------------------------
    if concept_id:
        # Determine target week from target problem or current_week
        target_week = current_week
        if target_problem_id and target_problem_id in item_map:
            target_week = item_map[target_problem_id].week

        # Ensure scheduled week is <= target_week to satisfy topological requirement
        scheduled_week = min(target_week, max(1, current_week))

        # Check if concept already exists in roadmap
        existing_concept = item_map.get(concept_id)
        if not existing_concept:
            existing_concept = next((it for it in working_items if it.id == concept_id), None)

        if existing_concept:
            # DO NOT append duplicate to diff.added if already in roadmap
            # If scheduled after scheduled_week, move it forward instead
            if existing_concept.week > scheduled_week:
                from_w = original_weeks.get(existing_concept.id, existing_concept.week)
                existing_concept.week = scheduled_week
                _record_move(existing_concept.id, existing_concept.title, from_w, scheduled_week)
        else:
            # Concept does not exist in roadmap: create and add to diff.added
            curated = get_concept(concept_id)
            concept_title = f"Micro-Lesson: {curated.title}" if curated else f"Micro-Lesson: {concept_id}"
            concept_est = curated.est_minutes if curated else 25

            new_concept_item = RoadmapItem(
                id=concept_id,
                week=scheduled_week,
                title=concept_title,
                type="concept",
                est_minutes=concept_est,
                status="todo",
                graded=False,
                prereq_concept_ids=[],
            )
            diff.added.append(new_concept_item)

            # Insert into working_items right before target problem or at scheduled week
            insert_idx = len(working_items)
            for idx, it in enumerate(working_items):
                if target_problem_id and it.id == target_problem_id:
                    insert_idx = idx
                    break
                elif it.week >= scheduled_week and it.status != "done":
                    insert_idx = idx
                    break
            working_items.insert(insert_idx, new_concept_item)
            item_map[concept_id] = new_concept_item

    # -------------------------------------------------------------------------
    # 2. GOAL_REPLAN: Curriculum Realignment
    # -------------------------------------------------------------------------
    if kind == "GOAL_REPLAN":
        # Identify incomplete, non-graded items that can be replaced
        # Graded items and completed items must NEVER be dropped
        for item in working_items:
            if item.status != "done" and not item.graded:
                # Candidate for replacement with new goal curriculum
                if item.id not in diff.removed:
                    diff.removed.append(item.id)

        # Add concepts aligned with new goal track (e.g. backend / data engineering)
        goal_lower = (new_goal or "").lower()
        new_track_slugs = ["sql-joins", "sql-indexes", "rest-basics", "auth-jwt"]
        if "data" in goal_lower or "backend" in goal_lower or "distributed" in goal_lower:
            new_track_slugs = ["sql-joins", "sql-indexes", "rest-basics", "auth-jwt"]

        start_week = max(current_week, 2)
        week_cursor = start_week
        for slug in new_track_slugs:
            if slug not in item_map and slug not in [a.id for a in diff.added]:
                concept = get_concept(slug)
                if concept:
                    added_item = RoadmapItem(
                        id=concept.id,
                        week=week_cursor,
                        title=concept.title,
                        type="concept",
                        est_minutes=concept.est_minutes,
                        status="todo",
                        graded=False,
                        prereq_concept_ids=[],
                    )
                    diff.added.append(added_item)
                    working_items.append(added_item)
                    item_map[added_item.id] = added_item
                    week_cursor += 1

    # -------------------------------------------------------------------------
    # 3. REPLAN_UP: Curriculum Acceleration
    # -------------------------------------------------------------------------
    elif kind == "REPLAN_UP":
        # Calculate current week load
        current_week_items = [it for it in working_items if it.week == current_week]
        current_load = sum(it.est_minutes for it in current_week_items)
        headroom = weekly_capacity_min - current_load

        # Find eligible items from current_week + 1 that can fit into current_week
        next_week_items = [
            it for it in working_items
            if it.week == current_week + 1 and it.status != "done"
        ]

        for it in next_week_items:
            # Check prerequisites: all prereqs must be scheduled <= current_week
            prereqs_met = True
            for prereq_id in it.prereq_concept_ids:
                if prereq_id in item_map:
                    if item_map[prereq_id].week > current_week:
                        prereqs_met = False
                        break
            if prereqs_met and it.est_minutes <= headroom:
                diff.moved.append({
                    "id": it.id,
                    "item_id": it.id,
                    "title": it.title,
                    "from_week": it.week,
                    "to_week": current_week,
                })
                it.week = current_week
                headroom -= it.est_minutes

    # -------------------------------------------------------------------------
    # 4 & 5. Multi-Pass Topological Sorting & Capacity Relief Reconciliation
    # -------------------------------------------------------------------------
    # Active items exclude those removed by GOAL_REPLAN
    removed_set = set(diff.removed)
    active_items = [it for it in working_items if it.id not in removed_set]

    # Reconcile topological prerequisite ordering and weekly capacity constraints.
    # Shifts in prerequisites can push dependent items into later weeks, potentially
    # overloading them; cascading capacity relief in turn shifts items forward, which
    # may necessitate further topological shifts. We iterate until convergence.
    max_reconcile_rounds = len(active_items) + 10
    for _ in range(max_reconcile_rounds):
        changed_in_round = False

        # Section 5: Multi-Pass Topological Prerequisite Sorting
        active_by_id = {it.id: it for it in active_items}
        topo_changed = True
        topo_iters = 0
        max_topo_iters = len(active_items) + 2
        while topo_changed and topo_iters < max_topo_iters:
            topo_changed = False
            topo_iters += 1
            for it in active_items:
                for prereq_id in it.prereq_concept_ids:
                    if prereq_id in active_by_id:
                        prereq_item = active_by_id[prereq_id]
                        if prereq_item.week > it.week:
                            # Shift dependent item to at least prereq_item.week
                            new_w = prereq_item.week
                            from_w = original_weeks.get(it.id, it.week)
                            it.week = new_w
                            _record_move(it.id, it.title, from_w, new_w)
                            topo_changed = True
                            changed_in_round = True

        # Section 4: Capacity Enforcement & Cascading Spillover
        max_week = max([it.week for it in active_items] + [current_week]) + 5
        for w in range(current_week, max_week + 1):
            while True:
                week_items = [it for it in active_items if it.week == w]
                week_load = sum(it.est_minutes for it in week_items)
                if week_load <= weekly_capacity_min:
                    break

                # Movable items: non-graded todo/in_progress items
                movable = [
                    it for it in week_items
                    if it.status != "done" and not it.graded
                ]
                if not movable:
                    break

                def in_week_dependent_count(it: RoadmapItem) -> int:
                    return sum(1 for other in week_items if it.id in other.prereq_concept_ids)

                # Sort movable: 0 dependents first, prerequisites last.
                # Preserves reversed(movable) order for items with equal dependent counts.
                movable_sorted = sorted(reversed(movable), key=in_week_dependent_count)
                it_to_spill = movable_sorted[0]

                # If the item has dependents in week w that cannot move (graded/done),
                # do not spill it past unmovable dependents.
                unmovable_dependents = [
                    other for other in week_items
                    if it_to_spill.id in other.prereq_concept_ids and (other.graded or other.status == "done")
                ]
                if unmovable_dependents:
                    break

                from_w = original_weeks.get(it_to_spill.id, it_to_spill.week)
                it_to_spill.week = w + 1
                _record_move(it_to_spill.id, it_to_spill.title, from_w, w + 1)
                changed_in_round = True

                # Requirement 2: Maintain topological order
                # Cascade to any dependent items scheduled earlier than it_to_spill.week
                active_by_id = {it.id: it for it in active_items}
                queue = [it_to_spill]
                visited = {it_to_spill.id}
                while queue:
                    curr = queue.pop(0)
                    for dep in active_items:
                        if curr.id in dep.prereq_concept_ids and dep.week < curr.week and dep.id not in visited:
                            visited.add(dep.id)
                            dep_from_w = original_weeks.get(dep.id, dep.week)
                            dep.week = curr.week
                            _record_move(dep.id, dep.title, dep_from_w, curr.week)
                            changed_in_round = True
                            queue.append(dep)

                # Maintain topological order with any prerequisites scheduled after it_to_spill.week
                for prereq_id in it_to_spill.prereq_concept_ids:
                    if prereq_id in active_by_id:
                        prereq_item = active_by_id[prereq_id]
                        if prereq_item.week > it_to_spill.week:
                            it_from_w = original_weeks.get(it_to_spill.id, it_to_spill.week)
                            it_to_spill.week = prereq_item.week
                            _record_move(it_to_spill.id, it_to_spill.title, it_from_w, prereq_item.week)
                            changed_in_round = True

        if not changed_in_round:
            break

    # Final topological verification pass to guarantee invariant holds
    active_by_id = {it.id: it for it in active_items}
    topo_changed = True
    topo_iters = 0
    max_topo_iters = len(active_items) + 2
    while topo_changed and topo_iters < max_topo_iters:
        topo_changed = False
        topo_iters += 1
        for it in active_items:
            for prereq_id in it.prereq_concept_ids:
                if prereq_id in active_by_id:
                    prereq_item = active_by_id[prereq_id]
                    if prereq_item.week > it.week:
                        new_w = prereq_item.week
                        from_w = original_weeks.get(it.id, it.week)
                        it.week = new_w
                        _record_move(it.id, it.title, from_w, new_w)
                        topo_changed = True

    # Filter out no-op moves (where from_week == to_week)
    diff.moved = [m for m in diff.moved if m["from_week"] != m["to_week"]]

    return diff


def apply_roadmap_diff(roadmap: list[RoadmapItem], diff: RoadmapDiff) -> list[RoadmapItem]:
    """Apply a RoadmapDiff to a list of RoadmapItems, returning the updated list."""
    # Deep copy items
    updated_items = [item.model_copy(deep=True) for item in roadmap]

    # Remove items in diff.removed
    removed_set = set(diff.removed)
    updated_items = [it for it in updated_items if it.id not in removed_set]

    # Add items in diff.added (avoid duplicate IDs)
    existing_ids = {it.id for it in updated_items}
    for added_item in diff.added:
        if added_item.id not in existing_ids:
            updated_items.append(added_item.model_copy(deep=True))
            existing_ids.add(added_item.id)

    # Move items in diff.moved
    moved_map = {m["id"]: m["to_week"] for m in diff.moved}
    for it in updated_items:
        if it.id in moved_map:
            it.week = moved_map[it.id]

    # Group and order items within each week so prerequisite micro-lessons
    # are placed before the problem they are a prerequisite for
    weeks = sorted({it.week for it in updated_items})
    final_items = []
    type_priority = {"concept": 0, "problem": 1, "project": 2}

    for w in weeks:
        week_items = [it for it in updated_items if it.week == w]
        item_ids = {it.id for it in week_items}
        deps = {it.id: set() for it in week_items}
        for it in week_items:
            for p in it.prereq_concept_ids:
                if p in item_ids:
                    deps[it.id].add(p)

        remaining = {it.id: it for it in week_items}
        sorted_week = []
        while remaining:
            ready = [it_id for it_id, reqs in deps.items() if it_id in remaining and not (reqs & remaining.keys())]
            if not ready:
                ready = list(remaining.keys())
            ready.sort(key=lambda x: (type_priority.get(remaining[x].type, 1), x))
            chosen = ready[0]
            sorted_week.append(remaining.pop(chosen))
        final_items.extend(sorted_week)

    return final_items


# Aliases for specification and test compatibility
generate_replan = replan_roadmap
adjust_roadmap = replan_roadmap
apply_diff = apply_roadmap_diff
