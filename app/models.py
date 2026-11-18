"""Scholar Pulse: Domain Data Models and API Schemas.

Pydantic v2 schemas for Students, Roadmap Items, Events, Signals, Interventions,
Roadmap Diffs, and Curated Concepts.
"""

from datetime import datetime
from typing import Any, Literal, Optional, Tuple
from pydantic import BaseModel, Field

from app.config import DEFAULT_ACTIVE_HOURS, DEFAULT_WEEKLY_CAPACITY_MIN


# ============================================================================
# Core Domain Models
# ============================================================================

class Student(BaseModel):
    """Represents an enrolled student in Scholar Pulse."""
    id: str = Field(..., description="Unique student ID (e.g. std-001, aarav)")
    name: str = Field(..., description="Student full name")
    goal_prompt: str = Field(..., description="User-entered career or learning goal")
    roadmap_goal: str = Field(..., description="Current curriculum track target")
    consent_nudges: bool = Field(default=True, description="Opt-in flag for proactive nudges")
    active_hours: Tuple[int, int] = Field(
        default=DEFAULT_ACTIVE_HOURS,
        description="Daily active study window (start_hour, end_hour) in 24h format"
    )
    weekly_capacity_min: int = Field(
        default=DEFAULT_WEEKLY_CAPACITY_MIN,
        description="Target weekly study capacity in minutes"
    )
    median_gap_days: float = Field(
        default=1.5,
        description="Historical median gap between study sessions in days"
    )
    weekly_nudge_count: int = Field(
        default=0,
        description="Derived count of nudges received in rolling 7 days"
    )


class RoadmapItem(BaseModel):
    """Represents a scheduled item in a student's learning roadmap."""
    id: str = Field(..., description="Unique item ID (e.g. R-001, P-014)")
    week: int = Field(..., ge=1, description="Scheduled 1-based week number")
    title: str = Field(..., description="Human-readable title")
    type: Literal["concept", "problem", "project"] = Field(
        ..., description="Curriculum item classification"
    )
    est_minutes: int = Field(..., ge=0, description="Estimated completion duration in minutes")
    status: Literal["todo", "in_progress", "done"] = Field(
        default="todo", description="Current completion status"
    )
    graded: bool = Field(default=False, description="Whether item is a graded assessment")
    prereq_concept_ids: list[str] = Field(
        default_factory=list, description="IDs of prerequisite concepts required before this item"
    )


class Event(BaseModel):
    """Immutable learning interaction event from student activity stream."""
    student_id: str = Field(..., description="Foreign key to Student")
    ts: datetime = Field(..., description="Timestamp in simulated UTC time")
    type: Literal["session", "attempt_fail", "attempt_pass", "hint_request", "goal_prompt"] = Field(
        ..., description="Event type classification"
    )
    problem_id: Optional[str] = Field(default=None, description="Associated problem ID if applicable")
    error_signature: Optional[str] = Field(
        default=None, description="Normalized error message or exception class"
    )
    duration_min: Optional[int] = Field(
        default=None, ge=0, description="Session or attempt duration in minutes"
    )
    goal_text: Optional[str] = Field(
        default=None, description="Payload text for goal_prompt events"
    )


class ContentConcept(BaseModel):
    """Curated curriculum concept used for targeted recommendations and RAG indexing."""
    id: str = Field(..., description="Unique concept slug")
    title: str = Field(..., description="Concept title")
    summary: str = Field(..., description="2-sentence curriculum summary")
    est_minutes: int = Field(..., ge=0, description="Typical review duration in minutes")


class SignalResult(BaseModel):
    """Normalized output from a deterministic signal detector."""
    name: str = Field(..., description="Signal identifier")
    score: float = Field(..., ge=0.0, le=1.0, description="Normalized score clamped to [0.0, 1.0]")
    triggered: bool = Field(..., description="Whether signal exceeded trigger threshold")
    evidence: str = Field(..., description="Human-readable explanation shown in the UI")


class RoadmapDiff(BaseModel):
    """Structural difference between previous and adjusted roadmaps."""
    added: list[RoadmapItem] = Field(default_factory=list, description="Newly inserted items")
    removed: list[str] = Field(default_factory=list, description="IDs of removed items")
    moved: list[dict[str, Any]] = Field(default_factory=list, description="Items moved between weeks")
    retimed: list[dict[str, Any]] = Field(default_factory=list, description="Items with updated durations")


class Intervention(BaseModel):
    """Complete record of a proactive intervention decision and action."""
    id: str = Field(..., description="Unique intervention identifier")
    student_id: str = Field(..., description="Foreign key to Student")
    ts: datetime = Field(..., description="Creation timestamp in simulated UTC")
    kind: Literal[
        "CONCEPT_NUDGE",
        "HINT_LADDER",
        "MICRO_LESSON",
        "REPLAN_DOWN",
        "REPLAN_UP",
        "REENGAGE",
        "GOAL_REPLAN",
        "ESCALATE_MENTOR",
        "NONE",
    ] = Field(..., description="Intervention policy decision kind")
    signals: list[SignalResult] = Field(
        default_factory=list, description="Snapshot of evaluated signals at decision time"
    )
    rationale: str = Field(..., description="Plain-English explanation of why this policy fired")
    message: Optional[str] = Field(default=None, description="Nudge message body (None if blocked or NONE)")
    cta: Optional[str] = Field(default=None, description="Call-to-action button label")
    target_problem_id: Optional[str] = Field(default=None, description="Target problem if stuck")
    concept_id: Optional[str] = Field(default=None, description="Recommended concept ID")
    roadmap_diff: Optional[RoadmapDiff] = Field(default=None, description="Roadmap adjustment diff")
    guardrail_log: list[str] = Field(
        default_factory=list, description="Audit log of pre- and post-gen guardrail checks"
    )
    status: Literal[
        "pending_followup", "recovered", "partial", "no_response", "escalated", "blocked", "deferred"
    ] = Field(default="pending_followup", description="Outcome tracking state")
    followup_due: Optional[datetime] = Field(
        default=None, description="Simulated timestamp when 48h outcome must be evaluated"
    )
    outcome_ts: Optional[datetime] = Field(
        default=None, description="Timestamp when follow-up was completed"
    )
    outcome_details: Optional[str] = Field(
        default=None, description="Summary details of follow-up evaluation"
    )


# ============================================================================
# API Response & Request Schemas
# ============================================================================

class StudentSummary(BaseModel):
    """Summary overview of student risk status for listing views."""
    id: str
    name: str
    goal_prompt: str
    roadmap_goal: str
    consent_nudges: bool
    stuck_score: float = Field(ge=0.0, le=1.0)
    disengaged_score: float = Field(ge=0.0, le=1.0)
    risk_level: Literal["high", "medium", "low", "healthy"]
    active_interventions_count: int = 0


class SimAdvanceRequest(BaseModel):
    """Request payload to advance the simulation clock."""
    days: int = Field(default=1, ge=0, le=36500, description="Number of days to advance (max 100 years)")


class SimAdvanceResponse(BaseModel):
    """Response returned when simulation clock advances."""
    sim_now: str
    days_advanced: int
    events_generated: int


class EvaluateResponse(BaseModel):
    """Response returned from full proactive evaluation loop."""
    intervention: Intervention
    signals: list[SignalResult]
    roadmap: list[RoadmapItem]
    followup_updates: list[dict[str, Any]] = Field(default_factory=list)


class ModeResponse(BaseModel):
    """Response indicating current LLM execution mode and clock time."""
    llm: Literal["live", "template"]
    sim_now: str


class CohortStudentItem(BaseModel):
    """Student record in the cohort / mentor view."""
    id: str
    name: str
    primary_signal: str
    suggested_action: str
    days_at_risk: float
    status: str
    consent_nudges: bool = True


class CohortResponse(BaseModel):
    """Response returning all at-risk students for cohort view."""
    at_risk_students: list[CohortStudentItem]


class MetricsResponse(BaseModel):
    """Aggregate proactive intervention metrics."""
    recovery_rate: float = Field(ge=0.0, le=1.0, description="Percentage of interventions reaching recovered state")
    avg_hours_to_recover: float = Field(ge=0.0, description="Average simulated hours to student recovery")
    nudges_sent: int = Field(ge=0, description="Total proactive nudges delivered to students")
    nudges_blocked: int = Field(ge=0, description="Total nudges blocked by guardrails")
    escalations: int = Field(ge=0, description="Total interventions escalated to mentor / placement cell")


# ============================================================================
# Curated Concept Index (12 Concepts per Build Plan §10)
# ============================================================================

CURATED_CONCEPTS: dict[str, ContentConcept] = {
    "js-closures": ContentConcept(
        id="js-closures",
        title="Closures & Lexical Scope",
        summary="Understanding how functions retain access to their outer scope variables even after execution. Essential for function factories, private state, and event callbacks.",
        est_minutes=20,
    ),
    "async-await": ContentConcept(
        id="async-await",
        title="Asynchronous JavaScript with Async/Await",
        summary="Writing clean asynchronous code using async functions and await expressions. Simplifies promise chaining and makes error handling more intuitive with try/catch blocks.",
        est_minutes=25,
    ),
    "promises-error-handling": ContentConcept(
        id="promises-error-handling",
        title="Promises & Error Handling Patterns",
        summary="Managing asynchronous operations and rejected states with Promise.catch, Promise.allSettled, and error propagation. Crucial for resilient network requests and stream pipelines.",
        est_minutes=25,
    ),
    "rest-basics": ContentConcept(
        id="rest-basics",
        title="RESTful API Design Fundamentals",
        summary="Designing stateless, resource-oriented HTTP APIs adhering to REST principles. Covers standard HTTP verbs, resource naming conventions, and idempotent operations.",
        est_minutes=20,
    ),
    "http-status-codes": ContentConcept(
        id="http-status-codes",
        title="HTTP Status Codes and Headers",
        summary="Proper utilization of HTTP status codes (2xx, 3xx, 4xx, 5xx) and essential headers for caching, content negotiation, and CORS policies.",
        est_minutes=15,
    ),
    "sql-joins": ContentConcept(
        id="sql-joins",
        title="SQL Joins and Relational Queries",
        summary="Querying data across relational tables using INNER, LEFT, RIGHT, and FULL OUTER joins. Mastering multi-table filtering, aggregation, and null handling.",
        est_minutes=30,
    ),
    "sql-indexes": ContentConcept(
        id="sql-indexes",
        title="Database Indexing and Performance",
        summary="Accelerating database queries with B-tree and hash indexes. Understanding query execution plans, index cardinality, and avoiding table scans.",
        est_minutes=30,
    ),
    "react-state": ContentConcept(
        id="react-state",
        title="React State Management and Hooks",
        summary="Managing local and lifted component state using useState and useReducer hooks. Best practices for immutability, state batching, and derived values.",
        est_minutes=25,
    ),
    "react-effects": ContentConcept(
        id="react-effects",
        title="React useEffect Lifecycle and Cleanup",
        summary="Handling side effects, subscriptions, and DOM interactions in functional components. Properly managing dependency arrays and cleanup functions to avoid memory leaks.",
        est_minutes=25,
    ),
    "node-modules": ContentConcept(
        id="node-modules",
        title="Node.js Module Architecture and npm",
        summary="Structuring server-side JavaScript applications using CommonJS and ES Modules. Managing dependency trees, semantic versioning, and package publishing.",
        est_minutes=20,
    ),
    "git-branching": ContentConcept(
        id="git-branching",
        title="Git Branching, Rebasing, and Merging",
        summary="Collaborative version control workflows including feature branches, rebasing onto main, resolving merge conflicts, and atomic commits.",
        est_minutes=20,
    ),
    "auth-jwt": ContentConcept(
        id="auth-jwt",
        title="JSON Web Tokens (JWT) and Stateless Authentication",
        summary="Implementing secure stateless user authentication with signed JWT tokens. Covers token verification, payload claims, expiration, and refresh token rotation.",
        est_minutes=30,
    ),
}


def get_concept(concept_id: str) -> Optional[ContentConcept]:
    """Retrieve a curated concept by its identifier, or None if not found."""
    return CURATED_CONCEPTS.get(concept_id)
