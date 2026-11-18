"""Scholar Pulse: Deterministic Seed Scenarios & 28-Day Historical Generator.

Generates reproducible seed state for the 4 primary student personas:
1. Aarav Patel (Scenario 1: Graded problem stall & pace drift -> recovery)
2. Meera Sharma (Scenario 2: Inactivity & silent drop-off -> escalation ladder)
3. Rohan Gupta (Scenario 3: Pace acceleration -> goal shift re-plan)
4. Priya Nair (Scenario 4: Consent-disabled guardrail & cohort alert)

All history is deterministically constructed using random.Random(42) starting at
BASE_NOW = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc).
"""

from datetime import datetime, timedelta, timezone
import random
from typing import TYPE_CHECKING

from app.models import (
    CURATED_CONCEPTS,
    ContentConcept,
    Event,
    RoadmapItem,
    Student,
)

if TYPE_CHECKING:
    from app.store import Store

BASE_NOW = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)


def seed_store(store: "Store") -> None:
    """Populate store with concepts, students, roadmaps, and 28-day historical events."""
    rng = random.Random(42)

    # -------------------------------------------------------------------------
    # 1. Curated Concepts (12 Concepts per Build Plan §10)
    # -------------------------------------------------------------------------
    for concept in CURATED_CONCEPTS.values():
        store.save_concept(concept)

    # -------------------------------------------------------------------------
    # 2. Student Profiles
    # -------------------------------------------------------------------------
    aarav = Student(
        id="aarav",
        name="Aarav Sharma",
        goal_prompt="I want to become a full-stack engineer and build scalable web apps.",
        roadmap_goal="Full-stack Web Track",
        consent_nudges=True,
        active_hours=(9, 22),
        weekly_capacity_min=600,
        median_gap_days=1.5,
    )

    meera = Student(
        id="meera",
        name="Meera Patel",
        goal_prompt="Backend Python developer",
        roadmap_goal="Python Backend Track",
        consent_nudges=True,
        active_hours=(10, 21),
        weekly_capacity_min=480,
        median_gap_days=1.5,
    )

    rohan = Student(
        id="rohan",
        name="Rohan Verma",
        goal_prompt="Frontend React specialist",
        roadmap_goal="Frontend Track",
        consent_nudges=True,
        active_hours=(8, 23),
        weekly_capacity_min=600,
        median_gap_days=1.0,
    )

    priya = Student(
        id="priya",
        name="Priya Nair",
        goal_prompt="Data Analyst",
        roadmap_goal="Data Track",
        consent_nudges=False,
        active_hours=(9, 22),
        weekly_capacity_min=500,
        median_gap_days=1.5,
    )

    store.save_student(aarav)
    store.save_student(meera)
    store.save_student(rohan)
    store.save_student(priya)

    # -------------------------------------------------------------------------
    # 3. Curriculum Roadmaps
    # -------------------------------------------------------------------------
    aarav_roadmap = [
        RoadmapItem(
            id="C-101", week=1, title="Closures & Lexical Scope",
            type="concept", est_minutes=60, status="done", graded=False
        ),
        RoadmapItem(
            id="P-001", week=1, title="Scope Exercises",
            type="problem", est_minutes=120, status="done", graded=False,
            prereq_concept_ids=["C-101"]
        ),
        RoadmapItem(
            id="C-102", week=2, title="Async/Await Fundamentals",
            type="concept", est_minutes=90, status="done", graded=False
        ),
        RoadmapItem(
            id="C-103", week=2, title="Promises & Error Handling",
            type="concept", est_minutes=90, status="in_progress", graded=False
        ),
        RoadmapItem(
            id="P-014", week=2, title="Async Data Fetch Pipeline",
            type="problem", est_minutes=180, status="todo", graded=True,
            prereq_concept_ids=["promises-error-handling", "async-await"]
        ),
        RoadmapItem(
            id="P-015", week=3, title="REST Client Architecture",
            type="problem", est_minutes=150, status="todo", graded=False,
            prereq_concept_ids=["rest-basics"]
        ),
        RoadmapItem(
            id="PRJ-301", week=4, title="Full-Stack Capstone Project",
            type="project", est_minutes=400, status="todo", graded=True,
            prereq_concept_ids=["auth-jwt", "sql-joins"]
        ),
    ]

    meera_roadmap = [
        RoadmapItem(
            id="C-101", week=1, title="Closures & Lexical Scope",
            type="concept", est_minutes=60, status="done", graded=False
        ),
        RoadmapItem(
            id="P-001", week=1, title="Scope Exercises",
            type="problem", est_minutes=120, status="in_progress", graded=False,
            prereq_concept_ids=["C-101"]
        ),
        RoadmapItem(
            id="C-102", week=2, title="Async/Await Fundamentals",
            type="concept", est_minutes=90, status="todo", graded=False
        ),
        RoadmapItem(
            id="C-103", week=2, title="Promises & Error Handling",
            type="concept", est_minutes=90, status="todo", graded=False
        ),
        RoadmapItem(
            id="P-014", week=2, title="Async Data Fetch Pipeline",
            type="problem", est_minutes=180, status="todo", graded=True,
            prereq_concept_ids=["promises-error-handling", "async-await"]
        ),
        RoadmapItem(
            id="P-015", week=3, title="REST Client Architecture",
            type="problem", est_minutes=150, status="todo", graded=False,
            prereq_concept_ids=["rest-basics"]
        ),
        RoadmapItem(
            id="PRJ-301", week=4, title="Full-Stack Capstone Project",
            type="project", est_minutes=400, status="todo", graded=True,
            prereq_concept_ids=["auth-jwt", "sql-joins"]
        ),
    ]

    # Rohan: 33% ahead of schedule at current_week = 2
    # Planned week <= 2: 60 + 120 + 90 + 90 + 180 = 540 min.
    # Done: 60 + 120 + 90 + 180 + 150 + 60 + 60 = 720 min.
    rohan_roadmap = [
        RoadmapItem(
            id="C-101", week=1, title="Closures & Lexical Scope",
            type="concept", est_minutes=60, status="done", graded=False
        ),
        RoadmapItem(
            id="P-001", week=1, title="Scope Exercises",
            type="problem", est_minutes=120, status="done", graded=False,
            prereq_concept_ids=["C-101"]
        ),
        RoadmapItem(
            id="C-102", week=2, title="Async/Await Fundamentals",
            type="concept", est_minutes=90, status="done", graded=False
        ),
        RoadmapItem(
            id="C-103", week=2, title="Promises & Error Handling",
            type="concept", est_minutes=90, status="in_progress", graded=False
        ),
        RoadmapItem(
            id="P-014", week=2, title="Async Data Fetch Pipeline",
            type="problem", est_minutes=180, status="done", graded=True,
            prereq_concept_ids=["promises-error-handling", "async-await"]
        ),
        RoadmapItem(
            id="P-015", week=3, title="REST Client Architecture",
            type="problem", est_minutes=150, status="done", graded=False,
            prereq_concept_ids=["rest-basics"]
        ),
        RoadmapItem(
            id="C-104", week=3, title="HTTP Status Codes and Headers",
            type="concept", est_minutes=60, status="done", graded=False
        ),
        RoadmapItem(
            id="C-105", week=3, title="SQL Joins & Relational Queries",
            type="concept", est_minutes=60, status="done", graded=False
        ),
        RoadmapItem(
            id="PRJ-301", week=4, title="Full-Stack Capstone Project",
            type="project", est_minutes=400, status="todo", graded=True,
            prereq_concept_ids=["auth-jwt", "sql-joins"]
        ),
    ]

    priya_roadmap = [
        RoadmapItem(
            id="C-101", week=1, title="Closures & Lexical Scope",
            type="concept", est_minutes=60, status="done", graded=False
        ),
        RoadmapItem(
            id="P-001", week=1, title="Scope Exercises",
            type="problem", est_minutes=120, status="done", graded=False,
            prereq_concept_ids=["C-101"]
        ),
        RoadmapItem(
            id="C-102", week=2, title="Async/Await Fundamentals",
            type="concept", est_minutes=90, status="done", graded=False
        ),
        RoadmapItem(
            id="C-103", week=2, title="Promises & Error Handling",
            type="concept", est_minutes=90, status="in_progress", graded=False
        ),
        RoadmapItem(
            id="P-014", week=2, title="Async Data Fetch Pipeline",
            type="problem", est_minutes=180, status="todo", graded=True,
            prereq_concept_ids=["promises-error-handling", "async-await"]
        ),
        RoadmapItem(
            id="P-015", week=3, title="REST Client Architecture",
            type="problem", est_minutes=150, status="todo", graded=False,
            prereq_concept_ids=["rest-basics"]
        ),
        RoadmapItem(
            id="PRJ-301", week=4, title="Full-Stack Capstone Project",
            type="project", est_minutes=400, status="todo", graded=True,
            prereq_concept_ids=["auth-jwt", "sql-joins"]
        ),
    ]

    store.update_roadmap("aarav", aarav_roadmap)
    store.update_roadmap("meera", meera_roadmap)
    store.update_roadmap("rohan", rohan_roadmap)
    store.update_roadmap("priya", priya_roadmap)

    # -------------------------------------------------------------------------
    # 4. 28-Day Historical Events Generator
    # -------------------------------------------------------------------------
    # 4.1 Aarav History:
    # 28 days of background activity + critical 72h stall
    aarav_events: list[Event] = []
    # Background study sessions (days -28 to -4)
    for day_offset in range(28, 4, 2):
        study_date = BASE_NOW - timedelta(days=day_offset, hours=rng.randint(2, 6))
        aarav_events.append(Event(
            student_id="aarav",
            ts=study_date,
            type="session",
            duration_min=rng.randint(45, 90),
        ))
    # Completed item passes
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(days=21), type="attempt_pass", problem_id="C-101", duration_min=45))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(days=15), type="attempt_pass", problem_id="P-001", duration_min=60))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(days=7), type="attempt_pass", problem_id="C-102", duration_min=45))

    # Critical 72h window on P-014: 6 fails + 2 long sessions (total 280m on problem)
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(hours=60), type="attempt_fail", problem_id="P-014", error_signature="SyntaxError:unexpected_token", duration_min=15))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(hours=48), type="attempt_fail", problem_id="P-014", error_signature="SyntaxError:unexpected_token", duration_min=15))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(hours=36), type="session", problem_id="P-014", duration_min=180))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(hours=36), type="attempt_fail", problem_id="P-014", error_signature="TypeError:undefined_iterable", duration_min=15))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(hours=24), type="attempt_fail", problem_id="P-014", error_signature="TypeError:undefined_iterable", duration_min=15))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(hours=12), type="session", problem_id="P-014", duration_min=100))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(hours=12), type="attempt_fail", problem_id="P-014", error_signature="TypeError:undefined_iterable", duration_min=15))
    aarav_events.append(Event(student_id="aarav", ts=BASE_NOW - timedelta(hours=4), type="attempt_fail", problem_id="P-014", error_signature="TypeError:undefined_iterable", duration_min=15))

    # 4.2 Meera History:
    # 6 historical study sessions between day -28 and day -6. Last session at -6d 1h. Zero events in past 6 days.
    meera_events: list[Event] = [
        Event(student_id="meera", ts=BASE_NOW - timedelta(days=26), type="session", duration_min=45),
        Event(student_id="meera", ts=BASE_NOW - timedelta(days=22), type="session", duration_min=60),
        Event(student_id="meera", ts=BASE_NOW - timedelta(days=18), type="session", duration_min=45),
        Event(student_id="meera", ts=BASE_NOW - timedelta(days=14), type="session", duration_min=50),
        Event(student_id="meera", ts=BASE_NOW - timedelta(days=10), type="session", duration_min=45),
        Event(student_id="meera", ts=BASE_NOW - timedelta(days=6, hours=1), type="session", duration_min=45),
    ]

    # 4.3 Rohan History:
    # Highly active, sessions every 1-2 days, high completion rate, no goal prompt yet
    rohan_events: list[Event] = []
    for d in range(25, 2, 2):
        rohan_events.append(Event(
            student_id="rohan",
            ts=BASE_NOW - timedelta(days=d, hours=2),
            type="session",
            duration_min=rng.randint(60, 120),
        ))
    rohan_events.append(Event(student_id="rohan", ts=BASE_NOW - timedelta(days=2), type="session", duration_min=180))
    rohan_events.append(Event(student_id="rohan", ts=BASE_NOW - timedelta(days=1), type="session", duration_min=180))

    # 4.4 Priya History:
    # 5 failed attempts in past 36h with RuntimeError:connection_aborted
    priya_events: list[Event] = [
        Event(student_id="priya", ts=BASE_NOW - timedelta(days=12), type="session", duration_min=60),
        Event(student_id="priya", ts=BASE_NOW - timedelta(days=8), type="session", duration_min=45),
        Event(student_id="priya", ts=BASE_NOW - timedelta(hours=34), type="attempt_fail", problem_id="P-014", error_signature="RuntimeError:connection_aborted", duration_min=15),
        Event(student_id="priya", ts=BASE_NOW - timedelta(hours=28), type="attempt_fail", problem_id="P-014", error_signature="RuntimeError:connection_aborted", duration_min=15),
        Event(student_id="priya", ts=BASE_NOW - timedelta(hours=20), type="session", problem_id="P-014", duration_min=120),
        Event(student_id="priya", ts=BASE_NOW - timedelta(hours=20), type="attempt_fail", problem_id="P-014", error_signature="RuntimeError:connection_aborted", duration_min=15),
        Event(student_id="priya", ts=BASE_NOW - timedelta(hours=10), type="attempt_fail", problem_id="P-014", error_signature="RuntimeError:connection_aborted", duration_min=15),
        Event(student_id="priya", ts=BASE_NOW - timedelta(hours=3), type="attempt_fail", problem_id="P-014", error_signature="RuntimeError:connection_aborted", duration_min=15),
    ]

    store.add_events(aarav_events)
    store.add_events(meera_events)
    store.add_events(rohan_events)
    store.add_events(priya_events)
