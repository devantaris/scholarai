# Project: Scholar Pulse

## Architecture
Scholar Pulse is an proactive intelligence layer for Scholar AI that detects student struggle and pace drift, decides pedagogical interventions, generates nudges and roadmap re-plans, and follows up at +48h to verify recovery.

```
+-----------------------------------------------------------------------------------+
|                                 SCHOLAR PULSE                                     |
|                                                                                   |
|  +--------------------+     +-------------------+     +------------------------+  |
|  |       DETECT       | --> |      DECIDE       | --> |          ACT           |  |
|  | 6 Signals + 2 Comp |     | Policy Priority   |     | Guardrails -> Gen      |  |
|  | (Deterministic)    |     | (Table 1-6)       |     | (Template / Live LLM)  |  |
|  +--------------------+     +-------------------+     +------------------------+  |
|            ^                                                      |               |
|            |                +-------------------+                 |               |
|            +--------------- |     FOLLOW UP     | <---------------+               |
|                             | +48h Outcome Eval |                                 |
|                             | Escalation Ladder |                                 |
|                             +-------------------+                                 |
+-----------------------------------------------------------------------------------+
```

### Module Architecture
1. `app/config.py`: Centralized thresholds, weights, caps, windows, and model constants.
2. `app/models.py`: Pydantic models for `Student`, `RoadmapItem`, `Event`, `ContentConcept`, `SignalResult`, `Intervention`, `RoadmapDiff`, and API schemas.
3. `app/signals.py`: Pure, deterministic signal detectors (`repeated_failure`, `stall_time`, `inactivity`, `pace_drift`, `pace_ahead`, `goal_change`, `stuck`, `disengaged`).
4. `app/policy.py`: Decision priority table (1: GOAL_REPLAN, 2: CONCEPT_NUDGE, 3: HINT_LADDER, 4: REENGAGE, 5: REPLAN_UP, 6: NONE).
5. `app/replanner.py`: Rule-based roadmap adjuster handling additions, moves, retimings, and topological prerequisite ordering.
6. `app/guardrails.py`: Pre-generation filters (consent, quiet hours, weekly cap 3, cooldown 24h, graded-work rule) and post-generation validators (<=60 words, no code on graded items, concept membership, roadmap integrity).
7. `app/generator.py`: Live Anthropic Claude generation with zero-config deterministic template fallback.
8. `app/followup.py`: Outcome classification (`recovered`, `partial`, `no_response`) and escalation ladder (`MICRO_LESSON`, `REENGAGE`, `ESCALATE_MENTOR`).
9. `app/store.py` & `app/seed.py`: Thread-safe state store and deterministic seed scenarios (Aarav, Meera, Rohan, Consent-disabled) with 12 curated concepts and 28-day history.
10. `app/sim.py`: Scripted simulation advancement.
11. `app/api.py` & `app/main.py`: 9 FastAPI REST endpoints and static file hosting.
12. `static/`: Vanilla HTML5/CSS3/ES6 single-page application (Top bar, Student view, Cohort view).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Configuration & Thresholds | Central constants, weights, thresholds in `app/config.py` | Milestone 1 | Build Plan §0, §5, §7 |
| 2 | Data Models | Pydantic v2 schemas for domain entities | Milestone 1 | Build Plan §4 |
| 3 | Seed Concept Index | 12 curated concept definitions with prerequisites | Milestone 1 | Build Plan §10 |
| 4 | `repeated_failure` detector | Detects 3+ fails on problem within 72h, zero if passed | Milestone 1 | Build Plan §5 |
| 5 | `stall_time` detector | Ratio of cumulative time on problem to est_minutes | Milestone 1 | Build Plan §5 |
| 6 | `inactivity` detector | Measures elapsed days since last session vs median gap | Milestone 1 | Build Plan §5 |
| 7 | `pace_drift` detector | Measures divergence between planned vs completed minutes | Milestone 1 | Build Plan §5 |
| 8 | `pace_ahead` detector | Measures progress exceeding planned curriculum schedule | Milestone 1 | Build Plan §5 |
| 9 | `goal_change` detector | Detects latest goal_prompt differing from roadmap_goal | Milestone 1 | Build Plan §5 |
| 10 | `stuck` composite | Max(repeated_failure, stall_time) with trigger OR | Milestone 1 | Build Plan §5 |
| 11 | `disengaged` composite | Max(inactivity, 0.8 * pace_drift) with trigger OR | Milestone 1 | Build Plan §5 |
| 12 | Decision Priority Table | Maps signal states to primary interventions (Priorities 1-6) | Milestone 2 | Build Plan §6 |
| 13 | Rule-Based Re-planner | Adjusts roadmap items preserving prerequisites & capacity | Milestone 2 | Build Plan §7.2, §7.3 |
| 14 | Pre-Generation Guardrails | Consent, Quiet Hours, Weekly Cap (3), Cooldown (24h), Graded Rule | Milestone 2 | Build Plan §7.1 |
| 15 | Post-Generation Validators | <=60 words, code-fence ban on graded, concept check, diff integrity | Milestone 2 | Build Plan §7.3 |
| 16 | Nudge Generator | Anthropic Claude live call with deterministic template fallback | Milestone 2 | Build Plan §7.2 |
| 17 | 48-Hour Follow-up Evaluator | Classifies outcome: `recovered`, `partial`, `no_response` | Milestone 3 | Build Plan §8 |
| 18 | Escalation Ladder | Progresses: `MICRO_LESSON` -> `REENGAGE` -> `ESCALATE_MENTOR` | Milestone 3 | Build Plan §8 |
| 19 | In-Memory / SQLite Store | Thread-safe store for students, roadmaps, events, interventions | Milestone 3 | Build Plan §2, §4 |
| 20 | Seed Scenarios & 28d History | 4 scenarios (Aarav, Meera, Rohan, Consent-disabled) with seeded RNG | Milestone 3 | Build Plan §10 |
| 21 | Scripted Simulation Clock | Advances time and injects scripted actions per scenario | Milestone 3 | Build Plan §8, §10 |
| 22 | Endpoint `/api/mode` | Returns LLM mode (`live` vs `template`) and current simulated ISO datetime | Milestone 3 | Build Plan §9 |
| 23 | Endpoint `/api/students` | Returns all students with computed risk summaries | Milestone 3 | Build Plan §9 |
| 24 | Endpoint `/api/students/{id}` | Returns student details, roadmap, 30d events, signals | Milestone 3 | Build Plan §9 |
| 25 | Endpoint `/api/students/{id}/evaluate` | Executes full detect -> decide -> guardrails -> generate loop | Milestone 3 | Build Plan §9 |
| 26 | Endpoint `/api/students/{id}/interventions` | Returns chronological intervention history | Milestone 3 | Build Plan §9 |
| 27 | Endpoint `/api/sim/advance` | Advances simulated clock and executes follow-ups | Milestone 3 | Build Plan §9 |
| 28 | Endpoint `/api/sim/reset` | Resets simulation to initial seed state | Milestone 3 | Build Plan §9 |
| 29 | Endpoint `/api/cohort` | Returns at-risk students for mentor / college view | Milestone 3 | Build Plan §9 |
| 30 | Endpoint `/api/metrics` | Returns aggregate proactive metrics (recovery rate, nudges, escalations) | Milestone 3 | Build Plan §9 |
| 31 | Top Control Bar UI | Student dropdown, mode badge, sim date, action buttons, view switch | Milestone 4 | Build Plan §11 |
| 32 | Student View - Timeline | 30-day activity visualizer (sessions, passes, fails) | Milestone 4 | Build Plan §11 |
| 33 | Student View - Roadmap | Visual roadmap cards with status chips and prerequisites | Milestone 4 | Build Plan §11 |
| 34 | Student View - Signal Gauges | Visual gauge bars with trigger marks and evidence strings | Milestone 4 | Build Plan §11 |
| 35 | Student View - Decision Card | Primary action, plain-English rationale, guardrail checklist | Milestone 4 | Build Plan §11 |
| 36 | Student View - Nudge Chat | Scholar AI chat bubble with body and action CTA button | Milestone 4 | Build Plan §11 |
| 37 | Student View - Roadmap Diff | Before/after visual diff (added, moved, removed, retimed) | Milestone 4 | Build Plan §11 |
| 38 | Student View - Follow-up History | Chronological cards with status chips | Milestone 4 | Build Plan §11 |
| 39 | Cohort View - Metrics Strip | Aggregate recovery rate %, avg hours, nudges sent/blocked, escalations | Milestone 4 | Build Plan §11 |
| 40 | Cohort View - At-Risk Table | Table of at-risk students with days at risk and suggested action | Milestone 4 | Build Plan §11 |
| 41 | E2E Integration & Demo Flows | End-to-end verification of Aarav, Meera, Rohan, Priya scenarios | Milestone 5 | Build Plan §10, §12 |
| 42 | Adversarial Hardening | Tier 5 white-box stress testing of boundaries & edge cases | Milestone 5 | Dual Track §Phase 2 |
| 43 | Documentation `README.md` | Pitch, loop diagram, 3-command setup, walkthrough, design decisions | Milestone 5 | Build Plan §13 |
| 44 | Challenge Write-up `docs/writeup.md` | Complete 9-section writeup | Milestone 5 | Build Plan §15 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Core Foundation & Signal Detection Engine | `app/config.py`, `app/models.py`, seed concept data, `app/signals.py`, `tests/test_signals.py` | None | DONE |
| M2 | Policy Engine, Guardrails & Generation | `app/policy.py`, `app/replanner.py`, `app/guardrails.py`, `app/generator.py`, `tests/test_policy.py`, `tests/test_guardrails.py`, `tests/test_replanner.py` | M1 | DONE |
| M3 | Simulation Engine & Backend API | `app/followup.py`, `app/store.py`, `app/seed.py`, `app/sim.py`, `app/api.py`, `app/main.py`, `tests/test_followup.py`, `tests/test_api.py` | M1, M2 | DONE |
| M4 | Interactive Frontend UI | `static/index.html`, `static/style.css`, `static/app.js` (served via FastAPI static mount) | M3 | DONE |
| M5 | Final Milestone: E2E Integration, Hardening & Docs | 100% E2E test pass, adversarial Tier-5 hardening, `README.md`, `docs/writeup.md` | M1, M2, M3, M4, TEST_READY | DONE |

## Interface Contracts
### `app/models.py` <-> Engine Modules (`signals`, `policy`, `guardrails`, `generator`, `followup`)
- `SignalResult`: `name: str`, `score: float` (clamped 0.0..1.0), `triggered: bool`, `evidence: str`
- `Student`: `id: str`, `name: str`, `goal_prompt: str`, `roadmap_goal: str`, `consent_nudges: bool`, `active_hours: tuple[int, int]`, `weekly_capacity_min: int`, `median_gap_days: float`
- `RoadmapItem`: `id: str`, `week: int`, `title: str`, `type: str`, `est_minutes: int`, `status: str`, `graded: bool`, `prereq_concept_ids: list[str]`
- `Event`: `student_id: str`, `ts: datetime`, `type: str`, `problem_id: str | None`, `error_signature: str | None`, `duration_min: int | None`, `goal_text: str | None`
- `Intervention`: `id: str`, `student_id: str`, `ts: datetime`, `kind: str`, `signals: list[SignalResult]`, `rationale: str`, `message: str | None`, `cta: str | None`, `roadmap_diff: RoadmapDiff | None`, `guardrail_log: list[str]`, `status: str`, `followup_due: datetime`
- `RoadmapDiff`: `added: list[RoadmapItem]`, `removed: list[str]`, `moved: list[dict]`, `retimed: list[dict]`

### Engine <-> API & Store
- `store.get_student(id: str) -> Student | None`
- `store.get_roadmap(student_id: str) -> list[RoadmapItem]`
- `store.get_events(student_id: str, since: datetime | None = None) -> list[Event]`
- `store.save_intervention(intervention: Intervention) -> None`
- `store.update_roadmap(student_id: str, roadmap: list[RoadmapItem]) -> None`
- `sim.advance_clock(store: Store, days: int) -> dict`
- `sim.reset_simulation(store: Store) -> None`

## Code Layout
```
c:\Users\Devansh\Desktop\Scholar AI\
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py
│   ├── signals.py
│   ├── policy.py
│   ├── replanner.py
│   ├── guardrails.py
│   ├── generator.py
│   ├── followup.py
│   ├── store.py
│   ├── seed.py
│   ├── sim.py
│   ├── api.py
│   └── main.py
├── static/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── tests/
│   ├── conftest.py
│   ├── test_signals.py
│   ├── test_policy.py
│   ├── test_guardrails.py
│   ├── test_replanner.py
│   ├── test_followup.py
│   └── test_api.py
├── docs/
│   └── writeup.md
├── requirements.txt
├── .env.example
├── README.md
└── PROJECT.md
```
