"""Tests for Scholar Pulse Backend REST API & State Store.

Validates:
1. GET /api/mode (live vs template mode detection and sim_now)
2. GET /api/students (seeded student listing and risk summaries)
3. GET /api/students/{id} (detailed student profile, roadmap, events, signals, 404)
4. POST /api/students/{id}/evaluate (proactive loop execution, diff attachment, 24h cooldown)
5. GET /api/students/{id}/interventions (intervention history, 404)
6. POST /api/sim/advance (clock advancement, validation of 0 and negative days)
7. POST /api/sim/reset (state re-seeding and idempotence)
8. GET /api/cohort (at-risk student identification and action suggestions)
9. GET /api/metrics (recovery rate, avg hours, nudge counters, zero-state safety)
10. Store CRUD & concurrency mechanics
11. CORS headers and middleware
"""

from datetime import datetime, timedelta, timezone
from typing import Any
import pytest
from fastapi.testclient import TestClient

from app.models import Event, Intervention, RoadmapItem, Student
from app.store import Store, store


@pytest.fixture
def api_client(client: Any) -> Any:
    """Fixture providing clean client."""
    client.post("/api/sim/reset")
    return client


# ============================================================================
# 1. API Route Tests
# ============================================================================

def test_api_mode(api_client: Any):
    """GET /api/mode returns execution mode and simulated ISO datetime."""
    res = api_client.get("/api/mode")
    assert res.status_code == 200
    data = res.json()
    assert data["llm"] in ["live", "template"]
    assert "sim_now" in data
    # Parse ISO timestamp
    parsed = datetime.fromisoformat(data["sim_now"])
    assert parsed.year == 2026


def test_api_students_listing(api_client: Any):
    """GET /api/students returns 4 seeded students with risk levels."""
    res = api_client.get("/api/students")
    assert res.status_code == 200
    students = res.json()
    assert len(students) == 4
    student_map = {s["id"]: s for s in students}
    assert "aarav" in student_map
    assert "meera" in student_map
    assert "rohan" in student_map
    assert "priya" in student_map

    # Aarav risk level
    assert student_map["aarav"]["risk_level"] in ["high", "medium"]
    assert student_map["aarav"]["stuck_score"] >= 0.6

    # Meera risk level
    assert student_map["meera"]["risk_level"] in ["high", "medium"]
    assert student_map["meera"]["disengaged_score"] >= 0.6


def test_api_student_detail_found(api_client: Any):
    """GET /api/students/{id} returns full student detail payload."""
    res = api_client.get("/api/students/aarav")
    assert res.status_code == 200
    data = res.json()
    assert data["student"]["id"] == "aarav"
    assert len(data["roadmap"]) > 0
    assert len(data["events"]) > 0
    assert len(data["signals"]) > 0


def test_api_student_detail_not_found(api_client: Any):
    """GET /api/students/{id} returns 404 for unknown student ID."""
    res = api_client.get("/api/students/non_existent_student_999")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_api_evaluate_student_aarav(api_client: Any):
    """POST /api/students/{id}/evaluate executes proactive loop for Aarav."""
    res = api_client.post("/api/students/aarav/evaluate")
    assert res.status_code == 200
    data = res.json()
    intv = data["intervention"]
    assert intv["student_id"] == "aarav"
    assert intv["kind"] == "CONCEPT_NUDGE"
    assert intv["concept_id"] == "promises-error-handling"
    assert intv["target_problem_id"] == "P-014"
    assert intv["roadmap_diff"] is not None
    assert intv["message"] is not None
    assert intv["cta"] is not None
    assert intv["status"] == "pending_followup"


def test_api_evaluate_student_not_found(api_client: Any):
    """POST /api/students/{id}/evaluate returns 404 for unknown student."""
    res = api_client.post("/api/students/unknown_xyz/evaluate")
    assert res.status_code == 404


def test_api_evaluate_consecutive_cooldown(api_client: Any):
    """Consecutive evaluations trigger 24-hour temporal cooldown guardrail."""
    res1 = api_client.post("/api/students/aarav/evaluate")
    assert res1.status_code == 200

    res2 = api_client.post("/api/students/aarav/evaluate")
    assert res2.status_code == 200
    data2 = res2.json()
    # Cooldown suppresses message or notes block in guardrail_log
    assert data2["intervention"]["message"] is None or any("cooldown" in l.lower() for l in data2["intervention"]["guardrail_log"])


def test_api_interventions_history(api_client: Any):
    """GET /api/students/{id}/interventions returns chronological history."""
    api_client.post("/api/students/aarav/evaluate")
    res = api_client.get("/api/students/aarav/interventions")
    assert res.status_code == 200
    history = res.json()
    assert isinstance(history, list)
    assert len(history) >= 1
    assert history[0]["student_id"] == "aarav"


def test_api_interventions_history_not_found(api_client: Any):
    """GET /api/students/{id}/interventions returns 404 for unknown student."""
    res = api_client.get("/api/students/unknown_abc/interventions")
    assert res.status_code == 404


def test_api_sim_advance_basic(api_client: Any):
    """POST /api/sim/advance increments clock and returns advance statistics."""
    mode_before = api_client.get("/api/mode").json()["sim_now"]
    res = api_client.post("/api/sim/advance", json={"days": 2})
    assert res.status_code == 200
    data = res.json()
    assert data["days_advanced"] == 2
    assert data["sim_now"] > mode_before


def test_api_sim_advance_zero_days(api_client: Any):
    """POST /api/sim/advance with days=0 succeeds without moving clock."""
    mode_before = api_client.get("/api/mode").json()["sim_now"]
    res = api_client.post("/api/sim/advance", json={"days": 0})
    assert res.status_code == 200
    assert res.json()["days_advanced"] == 0
    assert res.json()["sim_now"] == mode_before


def test_api_sim_advance_negative_days(api_client: Any):
    """POST /api/sim/advance with days < 0 returns HTTP 422 validation error."""
    res = api_client.post("/api/sim/advance", json={"days": -3})
    assert res.status_code in [400, 422]


def test_api_sim_reset(api_client: Any):
    """POST /api/sim/reset restores seed state and initial clock."""
    api_client.post("/api/sim/advance", json={"days": 10})
    res_reset = api_client.post("/api/sim/reset")
    assert res_reset.status_code == 200
    assert res_reset.json()["status"] == "ok"
    mode = api_client.get("/api/mode").json()
    assert "2026-11-18T10:00:00" in mode["sim_now"]


def test_api_cohort_view(api_client: Any):
    """GET /api/cohort surfaces at-risk students with suggested actions."""
    res = api_client.get("/api/cohort")
    assert res.status_code == 200
    cohort = res.json()
    assert "at_risk_students" in cohort
    at_risk = cohort["at_risk_students"]
    ids = [s["id"] for s in at_risk]
    assert "aarav" in ids
    assert "meera" in ids
    assert "priya" in ids


def test_api_metrics_view(api_client: Any):
    """GET /api/metrics calculates aggregate proactive system performance."""
    # Day 0: reset
    api_client.post("/api/sim/reset")
    res_zero = api_client.get("/api/metrics")
    assert res_zero.status_code == 200
    assert res_zero.json()["recovery_rate"] == 0.0

    # Evaluate all 4 students
    api_client.post("/api/students/aarav/evaluate")
    api_client.post("/api/students/meera/evaluate")
    api_client.post("/api/students/rohan/evaluate")
    api_client.post("/api/students/priya/evaluate")

    res_eval = api_client.get("/api/metrics")
    assert res_eval.status_code == 200
    data = res_eval.json()
    assert data["nudges_sent"] >= 1
    assert data["nudges_blocked"] >= 1
    assert 0.0 <= data["recovery_rate"] <= 1.0


def test_api_cors_headers(api_client: Any):
    """API responds with standard CORS headers."""
    res = api_client.get("/api/mode", headers={"Origin": "http://localhost:3000"})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "*"


# ============================================================================
# 2. Store Unit & Mechanics Tests
# ============================================================================

def test_store_crud_and_isolation():
    """Store CRUD operations maintain defensive copies and isolation."""
    custom_store = Store(auto_seed=False)
    now = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    custom_store.set_sim_now(now)
    assert custom_store.get_sim_now() == now

    student = Student(
        id="s1", name="Test Student", goal_prompt="Goal",
        roadmap_goal="Track", consent_nudges=True,
    )
    custom_store.save_student(student)
    retrieved = custom_store.get_student("s1")
    assert retrieved is not None
    assert retrieved.name == "Test Student"

    # Modify retrieved copy; store state must remain unchanged
    retrieved.name = "Mutated Name"
    assert custom_store.get_student("s1").name == "Test Student"


def test_store_roadmap_crud():
    """Store roadmap operations correctly update and retrieve items."""
    custom_store = Store(auto_seed=False)
    item1 = RoadmapItem(id="r1", week=1, title="Item 1", type="concept", est_minutes=30)
    custom_store.update_roadmap("s1", [item1])
    assert len(custom_store.get_roadmap("s1")) == 1

    item2 = RoadmapItem(id="r2", week=2, title="Item 2", type="problem", est_minutes=60)
    custom_store.update_roadmap_item("s1", item2)
    assert len(custom_store.get_roadmap("s1")) == 2

    # Update item 1 in-place
    item1_upd = RoadmapItem(id="r1", week=1, title="Item 1 Updated", type="concept", est_minutes=45, status="done")
    custom_store.update_roadmap_item("s1", item1_upd)
    items = custom_store.get_roadmap("s1")
    assert len(items) == 2
    assert items[0].title == "Item 1 Updated"
    assert items[0].status == "done"


def test_store_event_filtering():
    """Store get_events applies since, until, and limit_days filters accurately."""
    custom_store = Store(auto_seed=False)
    base_t = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    custom_store.set_sim_now(base_t)

    ev1 = Event(student_id="s1", ts=base_t - timedelta(days=10), type="session", duration_min=30)
    ev2 = Event(student_id="s1", ts=base_t - timedelta(days=3), type="session", duration_min=45)
    ev3 = Event(student_id="s1", ts=base_t - timedelta(hours=5), type="session", duration_min=60)
    custom_store.add_events([ev1, ev2, ev3])

    # All events
    assert len(custom_store.get_events("s1")) == 3

    # Limit to past 5 days
    assert len(custom_store.get_events("s1", limit_days=5)) == 2

    # Filter with since
    assert len(custom_store.get_events("s1", since=base_t - timedelta(days=2))) == 1

    # Filter with until
    assert len(custom_store.get_events("s1", until=base_t - timedelta(days=2))) == 2
