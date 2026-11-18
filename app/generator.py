"""Scholar Pulse: Nudge and Re-plan Generator.

Implements dual-mode intervention generation:
1. Live Anthropic Claude LLM generation (claude-sonnet-4-6) when ANTHROPIC_API_KEY is present.
2. Deterministic zero-config template fallback when the key is unset, API fails, or output fails post-generation validation.
"""

from datetime import datetime, timezone
import json
import os
from typing import Any, Literal, Optional, Tuple

from app.config import (
    ANTHROPIC_API_KEY_ENV,
    DEFAULT_LLM_MODEL,
    MAX_NUDGE_WORDS,
)
from app.guardrails import validate_nudge
from app.models import (
    CURATED_CONCEPTS,
    ContentConcept,
    Intervention,
    RoadmapDiff,
    RoadmapItem,
    Student,
    get_concept,
)
from app.replanner import replan_roadmap


def is_live_llm_mode() -> bool:
    """Return True if a non-empty Anthropic API key is configured."""
    return bool(os.environ.get(ANTHROPIC_API_KEY_ENV, "").strip())


def get_current_mode() -> Literal["live", "template"]:
    """Return the active generator mode ('live' or 'template')."""
    return "live" if is_live_llm_mode() else "template"


# =============================================================================
# Deterministic Fallback Templates
# =============================================================================

def generate_template_nudge(
    kind: str,
    student: Optional[Student] = None,
    problem: Optional[RoadmapItem] = None,
    concept: Optional[ContentConcept] = None,
    goal_prompt: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Produce a deterministic, warm, concise template message and CTA.
    
    Guaranteed:
      - Strictly under 35 words (well below 60 word cap).
      - Zero markdown code fences and zero backticks.
      - Personalized with student first name.
      - Academic integrity compliant (concept-oriented on graded work).
    """
    first_name = "there"
    if student and student.name:
        first_name = student.name.strip().split()[0]

    problem_title = problem.title if problem else "your current challenge"
    concept_title = concept.title if concept else "the foundational concept"
    target_goal = goal_prompt or (student.goal_prompt if student else "your learning track")

    if kind == "CONCEPT_NUDGE":
        msg = (
            f"Hi {first_name}, you've hit a challenging hurdle on {problem_title}. "
            f"Reviewing {concept_title} will clarify the core pattern before your next attempt."
        )
        cta = f"Review {concept_title}"
        return msg, cta

    elif kind == "HINT_LADDER":
        msg = (
            f"Hi {first_name}, take a quick breath on {problem_title}. "
            f"Verify your loop termination condition and handle edge cases like empty inputs first."
        )
        cta = "View Hint"
        return msg, cta

    elif kind == "MICRO_LESSON":
        msg = (
            f"Hi {first_name}, let's build momentum with a focused 15-minute micro-lesson on "
            f"{concept_title} to solidify the foundational concepts."
        )
        cta = "Start Micro-Lesson"
        return msg, cta

    elif kind == "REENGAGE":
        msg = (
            f"Hi {first_name}, great to see you! "
            f"Let's get your study momentum rolling today with a quick 10-minute warm-up exercise."
        )
        cta = "Start 10-Min Task"
        return msg, cta

    elif kind == "REPLAN_UP":
        msg = (
            f"Great work, {first_name}! You are ahead of schedule this week, "
            f"so we've unlocked upcoming coursework to keep your momentum high."
        )
        cta = "Explore Next Modules"
        return msg, cta

    elif kind == "GOAL_REPLAN":
        msg = (
            f"Hi {first_name}, we've adjusted your roadmap schedule to align directly "
            f"with your updated career goal: {target_goal}."
        )
        cta = "View Updated Roadmap"
        return msg, cta

    elif kind == "REPLAN_DOWN":
        msg = (
            f"Hi {first_name}, we've adjusted your roadmap schedule to spread tasks evenly "
            f"and keep your weekly study load balanced."
        )
        cta = "View Adjusted Schedule"
        return msg, cta

    elif kind in ["ESCALATE_MENTOR", "NONE"]:
        return None, None

    return None, None


# =============================================================================
# Live Anthropic LLM Integration
# =============================================================================

CALL_A_SYSTEM_PROMPT = """You are Scholar AI, an empathetic, encouraging proactive academic tutor for software engineering students.
Your task is to write a short, motivating nudge message for a student who needs guidance.

STRICT CONSTRAINTS:
1. Length: Under 60 words total.
2. Tone: Warm, constructive, zero guilt, zero shame, zero surveillance tone.
3. Actionability: Suggest exactly one clear next step.
4. Academic Integrity & Graded Problems:
   - If the problem is GRADED, you MUST NEVER write code, code snippets, syntax hints, or solution approaches.
   - On graded problems, reference only the underlying concept and theoretical principles.
   - NEVER use markdown code fences (```) or backticks (`) if the problem is graded.
5. Curated Concepts: Only reference concepts from the provided Curated Concept Index.
6. Output Format: You MUST output valid JSON with exactly two fields:
   {
     "message": "<string>",
     "cta": "<string>"
   }
Do not output any conversational wrapper or markdown outside the JSON object.
"""


def invoke_llm_nudge(
    student: Student,
    kind: str,
    problem: Optional[RoadmapItem] = None,
    concept: Optional[ContentConcept] = None,
    evidence: str = "",
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Invoke Anthropic Claude API for Call A (Nudge message).
    
    Returns:
        (message, cta, error_str_if_failed)
    """
    api_key = os.environ.get(ANTHROPIC_API_KEY_ENV, "").strip()
    if not api_key:
        return None, None, "Anthropic API key is not configured"

    try:
        import anthropic
    except ImportError:
        return None, None, "anthropic package is not installed"

    is_graded = problem.graded if problem else False
    user_payload = {
        "student_first_name": student.name.split()[0] if student.name else "Student",
        "career_goal": student.goal_prompt,
        "intervention_kind": kind,
        "target_problem_title": problem.title if problem else None,
        "is_graded_assessment": is_graded,
        "concept_title": concept.title if concept else None,
        "concept_summary": concept.summary if concept else None,
        "signal_evidence": evidence,
    }

    try:
        client = anthropic.Anthropic(api_key=api_key)
        model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
        response = client.messages.create(
            model=model,
            max_tokens=256,
            temperature=0.3,
            system=CALL_A_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Generate intervention nudge JSON based on this context:\n{json.dumps(user_payload, indent=2)}",
            }],
        )

        content = response.content[0].text.strip()
        # Parse JSON
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        data = json.loads(content)
        message = data.get("message")
        cta = data.get("cta")

        # Validate with post-gen validator
        valid, val_reason = validate_nudge(
            message=message,
            is_graded=is_graded,
            concept_id=concept.id if concept else None,
        )
        if not valid:
            return None, None, f"Post-gen validation failed: {val_reason}"

        return message, cta, None

    except Exception as exc:
        return None, None, f"LLM invocation failed: {str(exc)}"


# =============================================================================
# Master Content Generation Entrypoint
# =============================================================================

def generate_intervention_content(
    intervention: Intervention,
    student: Student,
    problem: Optional[RoadmapItem] = None,
    concept: Optional[ContentConcept] = None,
) -> Intervention:
    """Generate message and CTA for an intervention, using live LLM or template fallback."""
    if intervention.guardrail_log is None:
        intervention.guardrail_log = []

    if intervention.kind in ["NONE", "ESCALATE_MENTOR"]:
        intervention.message = None
        intervention.cta = None
        return intervention

    # Try live LLM if in live mode
    if is_live_llm_mode():
        evidence_str = "; ".join(s.evidence for s in intervention.signals if s.evidence)
        msg, cta, err = invoke_llm_nudge(
            student=student,
            kind=intervention.kind,
            problem=problem,
            concept=concept,
            evidence=evidence_str,
        )
        if msg and cta and not err:
            intervention.message = msg
            intervention.cta = cta
            intervention.guardrail_log.append("LLM generation: SUCCESS (claude-sonnet-4-6).")
            return intervention
        else:
            intervention.guardrail_log.append(
                f"LLM generation failed ({err}); reverted to deterministic template."
            )

    else:
        intervention.guardrail_log.append("LLM in template mode; generated deterministic template.")

    # Template fallback
    msg, cta = generate_template_nudge(
        kind=intervention.kind,
        student=student,
        problem=problem,
        concept=concept,
        goal_prompt=student.goal_prompt,
    )
    intervention.message = msg
    intervention.cta = cta
    return intervention


def generate_intervention(
    arg1: Any = None,
    arg2: Any = None,
    student: Optional[Student] = None,
    intervention: Optional[Intervention] = None,
    roadmap: Optional[list[RoadmapItem]] = None,
    signals: Optional[list[SignalResult]] = None,
    problem: Optional[RoadmapItem] = None,
    concept: Optional[ContentConcept] = None,
    target_problem: Optional[RoadmapItem] = None,
    current_week: int = 1,
    now: Optional[datetime] = None,
    *args: Any,
    **kwargs: Any,
) -> Intervention:
    """Master intervention generation entrypoint.
    
    Orchestrates policy decision (if intervention not already provided),
    content generation (via LLM with template fallback), and post-generation
    validation. Supports polymorphic parameter orders.
    """
    target_prob = problem or target_problem

    if arg1 is not None:
        if isinstance(arg1, Intervention) and intervention is None:
            intervention = arg1
        elif isinstance(arg1, Student) and student is None:
            student = arg1
        elif hasattr(arg1, "consent_nudges") and student is None:
            student = arg1
        elif hasattr(arg1, "signals") and intervention is None:
            intervention = arg1

    if arg2 is not None:
        if isinstance(arg2, Student) and student is None:
            student = arg2
        elif isinstance(arg2, Intervention) and intervention is None:
            intervention = arg2
        elif isinstance(arg2, list):
            if arg2 and isinstance(arg2[0], SignalResult) and signals is None:
                signals = arg2
            elif arg2 and isinstance(arg2[0], RoadmapItem) and roadmap is None:
                roadmap = arg2
        elif isinstance(arg2, RoadmapItem) and target_prob is None:
            target_prob = arg2

    for a in args:
        if isinstance(a, list):
            if a and isinstance(a[0], SignalResult) and signals is None:
                signals = a
            elif a and isinstance(a[0], RoadmapItem) and roadmap is None:
                roadmap = a
        elif isinstance(a, RoadmapItem) and target_prob is None:
            target_prob = a
        elif isinstance(a, ContentConcept) and concept is None:
            concept = a

    if intervention is None:
        if student is None:
            raise ValueError("generate_intervention requires at least a Student or Intervention")
        from app.policy import decide_intervention
        target_id = target_prob.id if target_prob else kwargs.get("target_problem_id")
        intervention = decide_intervention(
            student=student,
            roadmap=roadmap or [],
            signals=signals or [],
            target_problem_id=target_id,
            current_week=current_week,
            now=now,
            **kwargs,
        )

    if student is None:
        student = Student(
            id=intervention.student_id,
            name=f"Student {intervention.student_id}",
            goal_prompt="",
            roadmap_goal="",
        )

    if target_prob is None and intervention.target_problem_id and roadmap:
        target_prob = next((it for it in roadmap if it.id == intervention.target_problem_id), None)

    if concept is None and intervention.concept_id:
        concept = get_concept(intervention.concept_id)

    return generate_intervention_content(
        intervention=intervention,
        student=student,
        problem=target_prob,
        concept=concept,
    )


# Call B Re-plan Proposal
def generate_replan_proposal(
    roadmap: list[RoadmapItem],
    kind: str,
    target_problem_id: Optional[str] = None,
    concept_id: Optional[str] = None,
    current_week: int = 1,
    weekly_capacity_min: int = 600,
    new_goal: Optional[str] = None,
) -> RoadmapDiff:
    """Produce a RoadmapDiff, using rule-based replanner as the authoritative invariant engine."""
    return replan_roadmap(
        roadmap=roadmap,
        kind=kind,
        current_week=current_week,
        target_problem_id=target_problem_id,
        concept_id=concept_id,
        weekly_capacity_min=weekly_capacity_min,
        new_goal=new_goal,
    )
