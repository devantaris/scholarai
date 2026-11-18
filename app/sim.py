"""Scholar Pulse: Scripted Simulation Engine.

Advances the simulated clock, injects scripted learning behaviors for the 4 student
personas, and automatically evaluates pending 48-hour follow-up outcomes.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.followup import evaluate_followup
from app.models import Event, RoadmapItem
from app.store import Store, store


def advance_clock(store: Store, days: int) -> dict[str, Any]:
    """Advance the simulation clock by the specified number of days.
    
    In addition to updating sim_now, this function:
    1. Injects scripted student behaviors based on scenario progression.
    2. Evaluates any pending follow-up evaluations whose followup_due has arrived.
    
    Args:
        store: Target Store instance.
        days: Number of days to advance (must be >= 0).
        
    Returns:
        Dict with keys: sim_now (ISO str), days_advanced (int), events_generated (int).
    """
    if days < 0:
        raise ValueError("Days must be non-negative")

    sim_now = store.get_sim_now()
    if days == 0:
        return {
            "sim_now": sim_now.isoformat(),
            "days_advanced": 0,
            "events_generated": 0,
        }

    new_sim_now = sim_now + timedelta(days=days)
    events_generated = 0

    # -------------------------------------------------------------------------
    # 1. Scripted Student Behaviors
    # -------------------------------------------------------------------------
    # Scenario 1: Aarav Patel recovery after nudge
    aarav_interventions = store.get_interventions("aarav")
    if aarav_interventions:
        nudge_candidates = [i for i in aarav_interventions if i.kind in ["CONCEPT_NUDGE", "HINT_LADDER"]]
        if nudge_candidates:
            # Anchor to the earliest problem nudge
            nudge_candidates.sort(key=lambda x: x.ts)
            first_nudge = nudge_candidates[0]
            aarav_events = store.get_events("aarav")
            has_day1_session = any(
                e.type == "session" and e.problem_id == "P-014" and e.ts >= first_nudge.ts
                for e in aarav_events
            )
            has_day2_pass = any(
                e.type == "attempt_pass" and e.problem_id == "P-014" and e.ts >= first_nudge.ts
                for e in aarav_events
            )

            # Day +1 session (at nudge.ts + 24h)
            if new_sim_now >= first_nudge.ts + timedelta(days=1) and not has_day1_session:
                ev_session = Event(
                    student_id="aarav",
                    ts=first_nudge.ts + timedelta(hours=24),
                    type="session",
                    problem_id="P-014",
                    duration_min=45,
                )
                store.add_event(ev_session)
                events_generated += 1

            # Day +2 pass (at nudge.ts + 36h)
            if new_sim_now >= first_nudge.ts + timedelta(days=2) and not has_day2_pass:
                ev_pass = Event(
                    student_id="aarav",
                    ts=first_nudge.ts + timedelta(hours=36),
                    type="attempt_pass",
                    problem_id="P-014",
                    duration_min=30,
                )
                store.add_event(ev_pass)
                events_generated += 1

                # Mark P-014 done in Aarav's roadmap
                roadmap = store.get_roadmap("aarav")
                for item in roadmap:
                    if item.id == "P-014":
                        item.status = "done"
                store.update_roadmap("aarav", roadmap)

    # Scenario 3: Rohan Gupta goal change prompt
    rohan_interventions = store.get_interventions("rohan")
    rohan_events = store.get_events("rohan")
    has_goal_shift_prompt = any(
        e.type == "goal_prompt" and "backend" in (e.goal_text or "").lower()
        for e in rohan_events
    )
    if not has_goal_shift_prompt and (len(rohan_interventions) > 0 or days >= 1):
        ev_goal = Event(
            student_id="rohan",
            ts=new_sim_now - timedelta(hours=1),
            type="goal_prompt",
            goal_text="I want to move into backend and data engineering",
            duration_min=0,
        )
        store.add_event(ev_goal)
        events_generated += 1

    # -------------------------------------------------------------------------
    # 2. Process Pending Follow-ups Due
    # -------------------------------------------------------------------------
    all_students = store.get_students()
    for s in all_students:
        s_interventions = store.get_interventions(s.id)
        s_events = store.get_events(s.id)
        for intervention in s_interventions:
            if intervention.status == "pending_followup" and intervention.followup_due:
                if new_sim_now >= intervention.followup_due:
                    events_since = [e for e in s_events if e.ts >= intervention.ts and e.ts <= new_sim_now]
                    updated = evaluate_followup(
                        intervention,
                        events=events_since,
                        sim_now=new_sim_now,
                        history=s_interventions,
                    )
                    store.update_intervention(updated)

    # Commit updated simulated time
    store.set_sim_now(new_sim_now)

    return {
        "sim_now": new_sim_now.isoformat(),
        "days_advanced": days,
        "events_generated": events_generated,
    }


def reset_simulation(store: Store) -> None:
    """Reset store state and clock to pristine baseline seed state."""
    store.reset()
