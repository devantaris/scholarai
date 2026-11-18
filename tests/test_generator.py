"""Scholar Pulse: Unit Test Suite for Generator (app/generator.py).

Comprehensive unit tests verifying dual-mode generation (Live Anthropic LLM and
deterministic template fallback):
1. Successful live LLM JSON response -> validates message and CTA returned cleanly.
2. Malformed JSON string response from LLM -> verifies graceful fallback to deterministic template and audit log entry.
3. Message > 60 words response from LLM -> verifies post-gen validation rejection and fallback to deterministic template.
4. Code fence / backtick response from LLM on graded problem -> verifies post-gen rejection and fallback to template.
5. Anthropic API exception (e.g. rate limit, connection error) -> verifies fallback to deterministic template.
6. Deterministic templates for all intervention kinds verified under template mode.
7. Master generate_intervention entrypoint with polymorphic call signatures.
"""

from datetime import datetime, timezone
import json
from unittest.mock import MagicMock, patch
import pytest

from app.config import ANTHROPIC_API_KEY_ENV, MAX_NUDGE_WORDS
from app.generator import (
    generate_intervention,
    generate_intervention_content,
    generate_replan_proposal,
    generate_template_nudge,
    get_current_mode,
    invoke_llm_nudge,
    is_live_llm_mode,
)
from app.models import (
    CURATED_CONCEPTS,
    ContentConcept,
    Intervention,
    RoadmapDiff,
    RoadmapItem,
    SignalResult,
    Student,
    get_concept,
)


# =============================================================================
# Test Fixtures & Helpers
# =============================================================================

@pytest.fixture
def mock_anthropic():
    """Mock anthropic package in sys.modules and configure client mock."""
    mock_module = MagicMock()
    mock_client = MagicMock()
    mock_module.Anthropic.return_value = mock_client
    with patch.dict("sys.modules", {"anthropic": mock_module}):
        yield mock_client


@pytest.fixture
def sample_student():
    return Student(
        id="std-aarav-001",
        name="Aarav Sharma",
        goal_prompt="Full-Stack Developer",
        roadmap_goal="Full-Stack Track",
        consent_nudges=True,
    )


@pytest.fixture
def graded_problem():
    return RoadmapItem(
        id="P-014",
        week=2,
        title="Async Data Fetch Pipeline",
        type="problem",
        est_minutes=120,
        status="in_progress",
        graded=True,
        prereq_concept_ids=["promises-error-handling", "async-await"],
    )


@pytest.fixture
def practice_problem():
    return RoadmapItem(
        id="P-015",
        week=2,
        title="Basic Array Processing",
        type="problem",
        est_minutes=60,
        status="in_progress",
        graded=False,
        prereq_concept_ids=[],
    )


@pytest.fixture
def sample_concept():
    return get_concept("promises-error-handling")


# =============================================================================
# 1. Mode Detection Tests
# =============================================================================

def test_mode_detection_without_api_key(monkeypatch):
    """When ANTHROPIC_API_KEY is not set, generator is in template mode."""
    monkeypatch.delenv(ANTHROPIC_API_KEY_ENV, raising=False)
    assert is_live_llm_mode() is False
    assert get_current_mode() == "template"


def test_mode_detection_with_api_key(monkeypatch):
    """When ANTHROPIC_API_KEY is set, generator is in live mode."""
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "sk-ant-test-key-12345")
    assert is_live_llm_mode() is True
    assert get_current_mode() == "live"


# =============================================================================
# 2. Case a: Successful Live LLM JSON Response
# =============================================================================

def test_live_llm_successful_json_response(monkeypatch, mock_anthropic, sample_student, graded_problem, sample_concept):
    """Successful live LLM call returns clean message and CTA, and logs SUCCESS in guardrail log."""
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "sk-ant-test-live-key")

    mock_text = MagicMock()
    mock_text.text = json.dumps({
        "message": "Hi Aarav, review promise chaining and error handling to unblock your pipeline.",
        "cta": "Review Promises & Error Handling",
    })
    mock_response = MagicMock()
    mock_response.content = [mock_text]
    mock_anthropic.messages.create.return_value = mock_response

    # Test invoke_llm_nudge directly
    msg, cta, err = invoke_llm_nudge(
        student=sample_student,
        kind="CONCEPT_NUDGE",
        problem=graded_problem,
        concept=sample_concept,
        evidence="Repeated test failures on Promise rejection",
    )
    assert err is None
    assert msg == "Hi Aarav, review promise chaining and error handling to unblock your pipeline."
    assert cta == "Review Promises & Error Handling"

    # Test generate_intervention_content orchestration
    intv = Intervention(
        id="int-test-1",
        student_id=sample_student.id,
        ts=datetime.now(timezone.utc),
        kind="CONCEPT_NUDGE",
        signals=[SignalResult(name="stuck", score=1.0, triggered=True, evidence="Repeated failure")],
        rationale="Stuck on graded problem",
        guardrail_log=[],
    )
    result = generate_intervention_content(
        intervention=intv,
        student=sample_student,
        problem=graded_problem,
        concept=sample_concept,
    )
    assert result.message == "Hi Aarav, review promise chaining and error handling to unblock your pipeline."
    assert result.cta == "Review Promises & Error Handling"
    assert any("LLM generation: SUCCESS" in log for log in result.guardrail_log)


def test_live_llm_code_fenced_json_response(monkeypatch, mock_anthropic, sample_student, practice_problem):
    """When LLM returns JSON wrapped in markdown code fences, parser unwraps it correctly."""
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "sk-ant-test-live-key")

    mock_text = MagicMock()
    mock_text.text = "```json\n" + json.dumps({
        "message": "Hi Aarav, check your loop counter to fix the index out of bounds error.",
        "cta": "View Loop Hint",
    }) + "\n```"
    mock_response = MagicMock()
    mock_response.content = [mock_text]
    mock_anthropic.messages.create.return_value = mock_response

    msg, cta, err = invoke_llm_nudge(
        student=sample_student,
        kind="HINT_LADDER",
        problem=practice_problem,
    )
    assert err is None
    assert msg == "Hi Aarav, check your loop counter to fix the index out of bounds error."
    assert cta == "View Loop Hint"


# =============================================================================
# 3. Case b: Malformed JSON String Response from LLM
# =============================================================================

def test_live_llm_malformed_json_fallback_to_template(monkeypatch, mock_anthropic, sample_student, graded_problem, sample_concept):
    """Malformed JSON string triggers graceful fallback to deterministic template and logs error."""
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "sk-ant-test-live-key")

    mock_text = MagicMock()
    mock_text.text = "Here is the advice: {message: 'missing quotes', cta:"  # Malformed
    mock_response = MagicMock()
    mock_response.content = [mock_text]
    mock_anthropic.messages.create.return_value = mock_response

    msg, cta, err = invoke_llm_nudge(
        student=sample_student,
        kind="CONCEPT_NUDGE",
        problem=graded_problem,
        concept=sample_concept,
    )
    assert msg is None
    assert cta is None
    assert err is not None
    assert "LLM invocation failed" in err

    # Orchestrator fallback check
    intv = Intervention(
        id="int-test-2",
        student_id=sample_student.id,
        ts=datetime.now(timezone.utc),
        kind="CONCEPT_NUDGE",
        signals=[],
        rationale="Stuck",
        guardrail_log=[],
    )
    result = generate_intervention_content(
        intervention=intv,
        student=sample_student,
        problem=graded_problem,
        concept=sample_concept,
    )
    # Template fallback populated
    assert result.message is not None
    assert "Aarav" in result.message
    assert "Promises & Error Handling" in result.message
    assert result.cta == "Review Promises & Error Handling Patterns"
    assert any("LLM generation failed" in log and "reverted to deterministic template" in log for log in result.guardrail_log)


# =============================================================================
# 4. Case c: Message > 60 Words Response from LLM
# =============================================================================

def test_live_llm_word_cap_exceeded_fallback_to_template(monkeypatch, mock_anthropic, sample_student, graded_problem, sample_concept):
    """LLM response exceeding 60 words is rejected by post-gen validation and falls back to template."""
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "sk-ant-test-live-key")

    long_message = "word " * 65  # 65 words
    mock_text = MagicMock()
    mock_text.text = json.dumps({
        "message": long_message,
        "cta": "Read Guide",
    })
    mock_response = MagicMock()
    mock_response.content = [mock_text]
    mock_anthropic.messages.create.return_value = mock_response

    msg, cta, err = invoke_llm_nudge(
        student=sample_student,
        kind="CONCEPT_NUDGE",
        problem=graded_problem,
        concept=sample_concept,
    )
    assert msg is None
    assert cta is None
    assert err is not None
    assert "Post-gen validation failed" in err
    assert "exceeds maximum of 60 words" in err

    # Verify generate_intervention_content falls back to template
    intv = Intervention(
        id="int-test-3",
        student_id=sample_student.id,
        ts=datetime.now(timezone.utc),
        kind="CONCEPT_NUDGE",
        signals=[],
        rationale="Stuck",
        guardrail_log=[],
    )
    result = generate_intervention_content(
        intervention=intv,
        student=sample_student,
        problem=graded_problem,
        concept=sample_concept,
    )
    assert result.message is not None
    assert len(result.message.split()) <= MAX_NUDGE_WORDS
    assert any("reverted to deterministic template" in log for log in result.guardrail_log)


# =============================================================================
# 5. Case d: Code Fence / Backtick Response on Graded Problem
# =============================================================================

def test_live_llm_code_fence_on_graded_problem_fallback_to_template(monkeypatch, mock_anthropic, sample_student, graded_problem, sample_concept):
    """LLM output containing backticks on a graded problem is rejected and falls back to template."""
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "sk-ant-test-live-key")

    code_message = "Review this snippet: `const res = await fetch(url);` to see how promises work."
    mock_text = MagicMock()
    mock_text.text = json.dumps({
        "message": code_message,
        "cta": "Check Code",
    })
    mock_response = MagicMock()
    mock_response.content = [mock_text]
    mock_anthropic.messages.create.return_value = mock_response

    msg, cta, err = invoke_llm_nudge(
        student=sample_student,
        kind="CONCEPT_NUDGE",
        problem=graded_problem,  # graded=True
        concept=sample_concept,
    )
    assert msg is None
    assert cta is None
    assert err is not None
    assert "Post-gen validation failed" in err
    assert "backtick code on a graded assessment" in err

    # Verify generate_intervention_content falls back to template without backticks
    intv = Intervention(
        id="int-test-4",
        student_id=sample_student.id,
        ts=datetime.now(timezone.utc),
        kind="CONCEPT_NUDGE",
        signals=[],
        rationale="Stuck",
        guardrail_log=[],
    )
    result = generate_intervention_content(
        intervention=intv,
        student=sample_student,
        problem=graded_problem,
        concept=sample_concept,
    )
    assert result.message is not None
    assert "`" not in result.message
    assert any("reverted to deterministic template" in log for log in result.guardrail_log)


# =============================================================================
# 6. Case e: Anthropic API Exception (Rate Limit, Network Error)
# =============================================================================

def test_live_llm_api_exception_fallback_to_template(monkeypatch, mock_anthropic, sample_student, graded_problem, sample_concept):
    """Anthropic API exception (e.g. rate limit, connection failure) is caught and falls back to template."""
    monkeypatch.setenv(ANTHROPIC_API_KEY_ENV, "sk-ant-test-live-key")

    # Simulate Anthropic API error
    mock_anthropic.messages.create.side_effect = Exception("Rate limit exceeded: 429 Too Many Requests")

    msg, cta, err = invoke_llm_nudge(
        student=sample_student,
        kind="CONCEPT_NUDGE",
        problem=graded_problem,
        concept=sample_concept,
    )
    assert msg is None
    assert cta is None
    assert err is not None
    assert "Rate limit exceeded" in err

    intv = Intervention(
        id="int-test-5",
        student_id=sample_student.id,
        ts=datetime.now(timezone.utc),
        kind="CONCEPT_NUDGE",
        signals=[],
        rationale="Stuck",
        guardrail_log=[],
    )
    result = generate_intervention_content(
        intervention=intv,
        student=sample_student,
        problem=graded_problem,
        concept=sample_concept,
    )
    assert result.message is not None
    assert result.cta is not None
    assert any("Rate limit exceeded" in log and "reverted to deterministic template" in log for log in result.guardrail_log)


# =============================================================================
# 7. Case f: Deterministic Fallback Templates for All Intervention Kinds
# =============================================================================

@pytest.mark.parametrize("kind,expected_cta,should_have_content", [
    ("CONCEPT_NUDGE", "Review Promises & Error Handling Patterns", True),
    ("HINT_LADDER", "View Hint", True),
    ("MICRO_LESSON", "Start Micro-Lesson", True),
    ("REENGAGE", "Start 10-Min Task", True),
    ("REPLAN_UP", "Explore Next Modules", True),
    ("GOAL_REPLAN", "View Updated Roadmap", True),
    ("REPLAN_DOWN", "View Adjusted Schedule", True),
    ("ESCALATE_MENTOR", None, False),
    ("NONE", None, False),
])
def test_deterministic_templates_for_all_intervention_kinds(
    kind, expected_cta, should_have_content, sample_student, graded_problem, sample_concept, monkeypatch
):
    """Under template mode, all intervention kinds produce valid templates complying with length and format rules."""
    monkeypatch.delenv(ANTHROPIC_API_KEY_ENV, raising=False)

    msg, cta = generate_template_nudge(
        kind=kind,
        student=sample_student,
        problem=graded_problem,
        concept=sample_concept,
        goal_prompt="Backend Systems Track",
    )

    if should_have_content:
        assert msg is not None, f"Expected non-empty message for kind '{kind}'"
        assert cta is not None, f"Expected non-empty cta for kind '{kind}'"
        assert cta == expected_cta

        # Word count strictly <= 60 (templates are designed to be < 35 words)
        words = msg.strip().split()
        assert len(words) <= MAX_NUDGE_WORDS
        assert len(words) < 35

        # Zero backticks or code fences
        assert "`" not in msg

        # Student personalization
        if kind in ["CONCEPT_NUDGE", "HINT_LADDER", "MICRO_LESSON", "REENGAGE", "REPLAN_DOWN", "GOAL_REPLAN"]:
            assert "Aarav" in msg
    else:
        assert msg is None
        assert cta is None


# =============================================================================
# 8. Master generate_intervention Entrypoint & Polymorphic Signatures
# =============================================================================

def test_generate_intervention_orchestrates_from_signals(monkeypatch, sample_student, graded_problem):
    """generate_intervention creates intervention from signals, invokes generator, and returns result."""
    monkeypatch.delenv(ANTHROPIC_API_KEY_ENV, raising=False)

    roadmap = [
        RoadmapItem(id="C-101", week=1, title="Closures", type="concept", est_minutes=30, status="done"),
        graded_problem,
    ]
    signals = [
        SignalResult(name="stuck", score=1.0, triggered=True, evidence="Stuck on P-014"),
        SignalResult(name="pace_drift", score=0.40, triggered=True, evidence="40% drift"),
    ]

    # Call with (student, roadmap=..., signals=...)
    intv = generate_intervention(
        student=sample_student,
        roadmap=roadmap,
        signals=signals,
        target_problem=graded_problem,
        current_week=2,
    )
    assert isinstance(intv, Intervention)
    assert intv.kind == "CONCEPT_NUDGE"
    assert intv.message is not None
    assert intv.cta is not None
    assert intv.roadmap_diff is not None


def test_generate_intervention_polymorphic_signatures(monkeypatch, sample_student):
    """generate_intervention accepts polymorphic argument order (intervention, student) and (student, intervention)."""
    monkeypatch.delenv(ANTHROPIC_API_KEY_ENV, raising=False)

    candidate = Intervention(
        id="cand-1",
        student_id=sample_student.id,
        ts=datetime.now(timezone.utc),
        kind="REENGAGE",
        signals=[],
        rationale="Inactive",
        guardrail_log=[],
    )

    # Order 1: (intervention, student)
    res1 = generate_intervention(candidate, sample_student)
    assert res1.message is not None
    assert "Aarav" in res1.message
    assert res1.cta == "Start 10-Min Task"

    # Order 2: (student, intervention)
    res2 = generate_intervention(sample_student, candidate)
    assert res2.message is not None
    assert "Aarav" in res2.message
    assert res2.cta == "Start 10-Min Task"


def test_generate_replan_proposal_call_b():
    """generate_replan_proposal returns a valid RoadmapDiff using replanner."""
    roadmap = [
        RoadmapItem(id="item-1", week=1, title="Problem 1", type="problem", est_minutes=60, status="todo"),
    ]
    diff = generate_replan_proposal(roadmap, kind="REPLAN_DOWN", current_week=1)
    assert isinstance(diff, RoadmapDiff)
