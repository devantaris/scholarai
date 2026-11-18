"""Scholar Pulse: Central Configuration Constants and Thresholds.

Defines all temporal lookbacks, signal scoring thresholds, guardrail parameters,
and capacity settings per the specification.
"""

from typing import Tuple

# Temporal Windows & Lookbacks
REPEATED_FAILURE_WINDOW_HOURS: int = 72
FOLLOWUP_WINDOW_HOURS: int = 48
INACTIVITY_ABSOLUTE_DAYS_TRIGGER: float = 5.0
WEEKLY_CAP_WINDOW_DAYS: int = 7
COOLDOWN_HOURS: int = 24

# Signal Formulas & Thresholds
REPEATED_FAILURE_BASELINE: int = 2
REPEATED_FAILURE_DIVISOR: float = 4.0
REPEATED_FAILURE_ERROR_BONUS: float = 0.2
REPEATED_FAILURE_THRESHOLD: float = 0.5

STALL_TIME_MULTIPLIER: float = 1.5
STALL_TIME_THRESHOLD: float = 0.6

INACTIVITY_MIN_DENOMINATOR_DAYS: float = 2.0
INACTIVITY_GAP_MULTIPLIER: float = 2.0
INACTIVITY_THRESHOLD: float = 0.6

PACE_DRIFT_THRESHOLD: float = 0.30
PACE_AHEAD_THRESHOLD: float = 0.25

DISENGAGED_DRIFT_WEIGHT: float = 0.8
DISENGAGED_THRESHOLD: float = 0.6

# Guardrail Limits
MAX_NUDGES_PER_WEEK: int = 3
MAX_NUDGE_WORDS: int = 60
DEFAULT_ACTIVE_HOURS: Tuple[int, int] = (9, 22)

# LLM Configuration
DEFAULT_LLM_MODEL: str = "claude-sonnet-4-6"
ANTHROPIC_API_KEY_ENV: str = "ANTHROPIC_API_KEY"

# Task Capacities & Durations
DEFAULT_WEEKLY_CAPACITY_MIN: int = 600
REENGAGE_FIRST_TASK_MIN: int = 10
REENGAGE_SECOND_TASK_MIN: int = 5
