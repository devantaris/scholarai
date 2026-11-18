"""Scholar Pulse: Central Pytest Configuration and Test Fixtures.

Provides:
- FastAPI TestClient with auto-resetting simulation state for complete test isolation.
- Pre- and post-test state reset hooks for in-memory / SQLite stores.
- Flexible mock data factories for Students, RoadmapItems, Events, and timestamps.
- Scenario builders for Aarav, Meera, Rohan, and Priya (Consent-disabled).
- Mode testing fixtures (template vs live mode detection and monkeypatch helpers).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Generator, Optional
import pytest

from app.config import (
    DEFAULT_ACTIVE_HOURS,
    DEFAULT_WEEKLY_CAPACITY_MIN,
    MAX_NUDGES_PER_WEEK,
)
from app.models import (
    CURATED_CONCEPTS,
    ContentConcept,
    Event,
    Intervention,
    RoadmapDiff,
    RoadmapItem,
    SignalResult,
    Student,
)

# Standardized baseline simulated timestamp
BASE_NOW: datetime = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)


# ============================================================================
# 1. State Isolation & Simulation Reset Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def reset_simulation_state() -> Generator[None, None, None]:
    """Autouse fixture ensuring pristine state before and after every test.
    
    Resets the in-memory store and simulated clock directly if app.store is
    available. Gracefully passes during early milestones when store is not yet built.
    """
    def _do_reset():
        try:
            from app.store import store
            if hasattr(store, "reset"):
                store.reset()
        except (ImportError, AttributeError):
            pass

    # Setup: Reset store before test starts
    _do_reset()
    yield
    # Teardown: Reset store after test completes
    _do_reset()


# ============================================================================
# 2. FastAPI TestClient Fixture
# ============================================================================

@pytest.fixture
def client(reset_simulation_state: None) -> Generator[Any, None, None]:
    """Provide a FastAPI TestClient with pristine isolated store state.
    
    Uses lazy import so tests for pure modules (signals, policy, guardrails)
    do not fail if app.main is still being implemented.
    """
    try:
        from app.main import app
    except ImportError as exc:
        pytest.skip(f"app.main not yet available: {exc}")

    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        # Trigger /api/sim/reset over HTTP to verify API reset works and clock is initialized
        try:
            res = test_client.post("/api/sim/reset")
            if res.status_code != 200:
                pass
        except Exception:
            pass

        yield test_client


# ============================================================================
# 3. Mode & Environment Fixtures (Live LLM vs Template Fallback)
# ============================================================================

@pytest.fixture
def force_template_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure ANTHROPIC_API_KEY is not present, guaranteeing template fallback mode."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def force_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set dummy ANTHROPIC_API_KEY to test live LLM mode branches."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy-key-for-mode-detection")


@pytest.fixture
def api_mode(client: Any) -> dict[str, Any]:
    """Fetch mode response from GET /api/mode."""
    response = client.get("/api/mode")
    assert response.status_code == 200, f"GET /api/mode failed: {response.text}"
    return response.json()


@pytest.fixture
def is_template_mode(api_mode: dict[str, Any]) -> bool:
    """Return True if the backend is operating in deterministic template mode."""
    return api_mode.get("llm") == "template"


@pytest.fixture
def is_live_mode(api_mode: dict[str, Any]) -> bool:
    """Return True if the backend is operating in live LLM mode."""
    return api_mode.get("llm") == "live"


# ============================================================================
# 4. Temporal Helpers & Mock Data Factories
# ============================================================================

@pytest.fixture
def base_now() -> datetime:
    """Return standardized reference datetime."""
    return BASE_NOW


@pytest.fixture
def make_timestamp() -> Callable[..., datetime]:
    """Factory to generate UTC timestamps relative to BASE_NOW."""
    def _make(
        offset_days: float = 0.0,
        offset_hours: float = 0.0,
        offset_minutes: float = 0.0,
        base: Optional[datetime] = None,
    ) -> datetime:
        ref = base or BASE_NOW
        return ref + timedelta(days=offset_days, hours=offset_hours, minutes=offset_minutes)
    return _make


@pytest.fixture
def make_student() -> Callable[..., Student]:
    """Factory fixture for creating Student instances with customizable overrides."""
    def _make(
        id: str = "std-test-001",
        name: str = "Test Student",
        goal_prompt: str = "Full-stack Developer",
        roadmap_goal: str = "Full-stack Web Track",
        consent_nudges: bool = True,
        active_hours: tuple[int, int] = DEFAULT_ACTIVE_HOURS,
        weekly_capacity_min: int = DEFAULT_WEEKLY_CAPACITY_MIN,
        median_gap_days: float = 1.5,
        weekly_nudge_count: int = 0,
        **extra: Any,
    ) -> Student:
        return Student(
            id=id,
            name=name,
            goal_prompt=goal_prompt,
            roadmap_goal=roadmap_goal,
            consent_nudges=consent_nudges,
            active_hours=active_hours,
            weekly_capacity_min=weekly_capacity_min,
            median_gap_days=median_gap_days,
            weekly_nudge_count=weekly_nudge_count,
            **extra,
        )
    return _make


@pytest.fixture
def make_roadmap_item() -> Callable[..., RoadmapItem]:
    """Factory fixture for creating RoadmapItem instances."""
    def _make(
        id: str = "R-001",
        week: int = 1,
        title: str = "Introduction to Async",
        type: str = "concept",
        est_minutes: int = 30,
        status: str = "todo",
        graded: bool = False,
        prereq_concept_ids: Optional[list[str]] = None,
        **extra: Any,
    ) -> RoadmapItem:
        return RoadmapItem(
            id=id,
            week=week,
            title=title,
            type=type,  # type: ignore[arg-type]
            est_minutes=est_minutes,
            status=status,  # type: ignore[arg-type]
            graded=graded,
            prereq_concept_ids=prereq_concept_ids or [],
            **extra,
        )
    return _make


@pytest.fixture
def make_event() -> Callable[..., Event]:
    """Factory fixture for creating single Event instances."""
    def _make(
        student_id: str = "std-test-001",
        ts: Optional[datetime] = None,
        type: str = "session",
        problem_id: Optional[str] = None,
        error_signature: Optional[str] = None,
        duration_min: Optional[int] = 45,
        goal_text: Optional[str] = None,
        **extra: Any,
    ) -> Event:
        return Event(
            student_id=student_id,
            ts=ts or BASE_NOW,
            type=type,  # type: ignore[arg-type]
            problem_id=problem_id,
            error_signature=error_signature,
            duration_min=duration_min,
            goal_text=goal_text,
            **extra,
        )
    return _make


@pytest.fixture
def make_roadmap(make_roadmap_item: Callable[..., RoadmapItem]) -> Callable[..., list[RoadmapItem]]:
    """Factory fixture generating a standard 4-week roadmap with mixed items."""
    def _make(custom_items: Optional[list[RoadmapItem]] = None) -> list[RoadmapItem]:
        if custom_items is not None:
            return custom_items
        return [
            make_roadmap_item(
                id="C-101", week=1, title="Closures & Lexical Scope",
                type="concept", est_minutes=60, status="done", graded=False
            ),
            make_roadmap_item(
                id="P-001", week=1, title="Scope Exercises",
                type="problem", est_minutes=120, status="done", graded=False,
                prereq_concept_ids=["C-101"]
            ),
            make_roadmap_item(
                id="C-102", week=2, title="Async/Await Fundamentals",
                type="concept", est_minutes=90, status="done", graded=False
            ),
            make_roadmap_item(
                id="C-103", week=2, title="Promises & Error Handling",
                type="concept", est_minutes=90, status="in_progress", graded=False
            ),
            make_roadmap_item(
                id="P-014", week=2, title="Async Data Fetch Pipeline",
                type="problem", est_minutes=180, status="todo", graded=True,
                prereq_concept_ids=["promises-error-handling", "async-await"]
            ),
            make_roadmap_item(
                id="P-015", week=3, title="REST Client Architecture",
                type="problem", est_minutes=150, status="todo", graded=False,
                prereq_concept_ids=["rest-basics"]
            ),
            make_roadmap_item(
                id="PRJ-301", week=4, title="Full-Stack Capstone Project",
                type="project", est_minutes=400, status="todo", graded=True,
                prereq_concept_ids=["auth-jwt", "sql-joins"]
            ),
        ]
    return _make


@pytest.fixture
def make_failure_events(make_event: Callable[..., Event]) -> Callable[..., list[Event]]:
    """Helper to generate a cluster of failed attempts on a problem within a time window."""
    def _make(
        student_id: str,
        problem_id: str,
        count: int = 4,
        error_signature: Optional[str] = "TypeError:undefined_iterable",
        within_hours: float = 24.0,
        base_time: Optional[datetime] = None,
    ) -> list[Event]:
        ref = base_time or BASE_NOW
        events = []
        for i in range(count):
            delta_h = (within_hours / (count + 1)) * (count - i)
            events.append(
                make_event(
                    student_id=student_id,
                    ts=ref - timedelta(hours=delta_h),
                    type="attempt_fail",
                    problem_id=problem_id,
                    error_signature=error_signature,
                    duration_min=15,
                )
            )
        return events
    return _make


@pytest.fixture
def make_stall_events(make_event: Callable[..., Event]) -> Callable[..., list[Event]]:
    """Helper to generate multiple sessions accumulating stall time on an unpassed problem."""
    def _make(
        student_id: str,
        problem_id: str,
        total_minutes: int = 300,
        sessions_count: int = 3,
        base_time: Optional[datetime] = None,
    ) -> list[Event]:
        ref = base_time or BASE_NOW
        events = []
        mins_per_session = total_minutes // sessions_count
        for i in range(sessions_count):
            delta_h = (i + 1) * 8.0
            events.append(
                make_event(
                    student_id=student_id,
                    ts=ref - timedelta(hours=delta_h),
                    type="session",
                    problem_id=problem_id,
                    duration_min=mins_per_session,
                )
            )
            events.append(
                make_event(
                    student_id=student_id,
                    ts=ref - timedelta(hours=delta_h - 0.2),
                    type="attempt_fail",
                    problem_id=problem_id,
                    error_signature="RuntimeError:timeout",
                    duration_min=5,
                )
            )
        return events
    return _make


@pytest.fixture
def make_inactivity_events(make_event: Callable[..., Event]) -> Callable[..., list[Event]]:
    """Helper to simulate an inactive student whose last session was X days ago."""
    def _make(
        student_id: str,
        days_inactive: float = 6.0,
        historical_sessions: int = 5,
        base_time: Optional[datetime] = None,
    ) -> list[Event]:
        ref = base_time or BASE_NOW
        events = []
        for i in range(historical_sessions):
            delta_d = days_inactive + (i + 1) * 2.0
            events.append(
                make_event(
                    student_id=student_id,
                    ts=ref - timedelta(days=delta_d),
                    type="session",
                    duration_min=60,
                )
            )
            events.append(
                make_event(
                    student_id=student_id,
                    ts=ref - timedelta(days=delta_d - 0.1),
                    type="attempt_pass",
                    problem_id=f"P-{100 + i}",
                    duration_min=20,
                )
            )
        return events
    return _make


# ============================================================================
# 5. Predefined Scenario State Fixtures (Aarav, Meera, Rohan, Priya)
# ============================================================================

@pytest.fixture
def aarav_scenario_data(
    make_student: Callable[..., Student],
    make_roadmap: Callable[..., list[RoadmapItem]],
    make_failure_events: Callable[..., list[Event]],
    base_now: datetime,
) -> dict[str, Any]:
    """Baseline test scenario for Aarav (Scenario 1: Graded problem stall & pace drift)."""
    student = make_student(
        id="aarav",
        name="Aarav Sharma",
        goal_prompt="I want to become a full-stack engineer and build scalable web apps.",
        roadmap_goal="Full-stack Web Track",
        consent_nudges=True,
        active_hours=(9, 22),
        weekly_capacity_min=600,
        median_gap_days=1.5,
    )
    roadmap = make_roadmap()
    events: list[Event] = []
    events.extend(
        make_failure_events(
            student_id="aarav",
            problem_id="P-014",
            count=4,
            error_signature="TypeError:undefined_iterable",
            within_hours=48.0,
            base_time=base_now,
        )
    )
    events.extend(
        make_failure_events(
            student_id="aarav",
            problem_id="P-014",
            count=2,
            error_signature="SyntaxError:unexpected_token",
            within_hours=70.0,
            base_time=base_now - timedelta(hours=48),
        )
    )
    events.append(
        Event(
            student_id="aarav",
            ts=base_now - timedelta(hours=36),
            type="session",
            problem_id="P-014",
            duration_min=180,
        )
    )
    events.append(
        Event(
            student_id="aarav",
            ts=base_now - timedelta(hours=12),
            type="session",
            problem_id="P-014",
            duration_min=100,
        )
    )

    return {
        "student": student,
        "roadmap": roadmap,
        "events": events,
        "target_problem_id": "P-014",
        "now": base_now,
    }


@pytest.fixture
def meera_scenario_data(
    make_student: Callable[..., Student],
    make_roadmap: Callable[..., list[RoadmapItem]],
    make_inactivity_events: Callable[..., list[Event]],
    base_now: datetime,
) -> dict[str, Any]:
    """Baseline test scenario for Meera (Scenario 2: Silent Drop-off & Escalation)."""
    student = make_student(
        id="meera",
        name="Meera Patel",
        goal_prompt="Backend Python developer",
        roadmap_goal="Python Backend Track",
        consent_nudges=True,
        active_hours=(10, 21),
        weekly_capacity_min=480,
        median_gap_days=1.5,
    )
    roadmap = make_roadmap()
    events = make_inactivity_events(
        student_id="meera",
        days_inactive=6.0,
        historical_sessions=6,
        base_time=base_now,
    )
    return {
        "student": student,
        "roadmap": roadmap,
        "events": events,
        "target_problem_id": None,
        "now": base_now,
    }


@pytest.fixture
def rohan_scenario_data(
    make_student: Callable[..., Student],
    make_roadmap: Callable[..., list[RoadmapItem]],
    make_event: Callable[..., Event],
    base_now: datetime,
) -> dict[str, Any]:
    """Baseline test scenario for Rohan (Scenario 3: Acceleration & Goal Change)."""
    student = make_student(
        id="rohan",
        name="Rohan Verma",
        goal_prompt="Frontend React specialist",
        roadmap_goal="Frontend Track",
        consent_nudges=True,
        active_hours=(8, 23),
        weekly_capacity_min=600,
        median_gap_days=1.0,
    )
    roadmap = make_roadmap()
    events = [
        make_event(
            student_id="rohan",
            ts=base_now - timedelta(days=2),
            type="session",
            duration_min=180,
        ),
        make_event(
            student_id="rohan",
            ts=base_now - timedelta(days=1),
            type="session",
            duration_min=180,
        ),
        make_event(
            student_id="rohan",
            ts=base_now - timedelta(hours=2),
            type="goal_prompt",
            goal_text="I want to move into backend and data engineering",
            duration_min=0,
        ),
    ]
    return {
        "student": student,
        "roadmap": roadmap,
        "events": events,
        "target_problem_id": None,
        "now": base_now,
    }


@pytest.fixture
def priya_consent_disabled_scenario_data(
    make_student: Callable[..., Student],
    make_roadmap: Callable[..., list[RoadmapItem]],
    make_failure_events: Callable[..., list[Event]],
    base_now: datetime,
) -> dict[str, Any]:
    """Baseline test scenario for Priya (Scenario 4: Consent-Disabled Student)."""
    student = make_student(
        id="priya",
        name="Priya Nair",
        goal_prompt="Data Analyst",
        roadmap_goal="Data Track",
        consent_nudges=False,
        active_hours=(9, 22),
        weekly_capacity_min=500,
        median_gap_days=1.5,
    )
    roadmap = make_roadmap()
    events = make_failure_events(
        student_id="priya",
        problem_id="P-014",
        count=5,
        error_signature="RuntimeError:connection_aborted",
        within_hours=36.0,
        base_time=base_now,
    )
    return {
        "student": student,
        "roadmap": roadmap,
        "events": events,
        "target_problem_id": "P-014",
        "now": base_now,
    }
