# Scholar Pulse — Proactive Student Struggle & Pace Intervention Engine

[![Tests: 319 Passing](https://img.shields.io/badge/Tests-319%20Passed-success.svg)](#automated-testing)
[![FastAPI Backend](https://img.shields.io/badge/Backend-FastAPI%20%7C%20Pydantic%20v2-blue.svg)](app/main.py)
[![Architecture](https://img.shields.io/badge/Architecture-Detect%20%E2%86%92%20Decide%20%E2%86%92%20Act%20%E2%86%92%20Follow--up-indigo.svg)](#core-proactive-loop-architecture)
[![Coverage](https://img.shields.io/badge/Coverage-Tiers%201--5%20Comprehensive-brightgreen.svg)](#test-tiers)

> **Scholar Pulse** is an intelligent, proactive layer built for **Scholar AI / StepCode** that continuously monitors student telemetry, diagnoses learning struggle and pace drift before dropout occurs, issues pedagogically sound nudges and roadmap re-plans, and systematically tracks 48-hour recovery outcomes.

---

## 1. The Pitch: Why Proactive Intervention Matters

Traditional online learning platforms and AI coding tutors suffer from a fundamental flaw: **they are entirely passive**. They wait for the student to ask a question in chat, submit a ticket, or query a copilot.

However, real-world learning analytics show:
1. **The Silent Drop**: Over **70% of struggling students never ask for help**. They encounter a cryptic error (`TypeError: Cannot read properties of undefined`), spend 3x the expected time, experience cognitive overload, close their browser, and never return. Industry MOOC completion rates persistently hover below **10%**.
2. **Academic Dishonesty Risk**: When passive AI bots offer help on graded assessments, they frequently hallucinate or leak complete code solutions, undermining pedagogical validity and accreditation standards.
3. **Notification Fatigue & Irritation**: Uncoordinated automated reminders often spam students late at night or during active study flows, causing students to disable notifications or drop out entirely.

**Scholar Pulse solves this by closing the loop**:
- **Continuous Telemetry Listening**: Evaluates failure frequencies, stall time, session gaps, and syllabus velocity deterministically.
- **Academic Safety Envelope**: Distinguishes practice problems from graded assessments, completely suppressing solution leaks and direct code fences on graded work.
- **Adaptive Roadmap Re-Planning**: Dynamically adjusts weekly workloads, inserts prerequisite micro-lessons, and cascades excess burden across future weeks without violating capacity constraints.
- **Accountability & Escalation**: Verifies whether an intervention produced real recovery within 48 hours; if silence persists, it advances down a structured escalation ladder to human mentors.

---

## 2. Core Proactive Loop Architecture

Scholar Pulse executes a continuous 4-phase control loop: **Detect $\rightarrow$ Decide $\rightarrow$ Act $\rightarrow$ Follow-up**.

```
                           +-------------------------------------+
                           |      Student Learning Telemetry     |
                           |  (Attempts, Errors, Sessions, Goals)|
                           +------------------+------------------+
                                              |
                                              v
   +---------------------------------------------------------------------------------------+
   | 1. DETECT ENGINE (Deterministic Signal Scoring)                                       |
   |   - Repeated Failure Score   : S_rf = clamp((failures - 2)/4 + bonus)                 |
   |   - Stall Time Score         : S_st = clamp(time_spent / (1.5 * est_time))            |
   |   - Inactivity Score         : S_in = clamp(gap_days / max(2.0, 2 * median_gap))      |
   |   - Pace Drift Score         : S_pd = clamp(1.0 - (actual / planned)) (>= 0.30)       |
   |   - Pace Ahead Score         : S_pa = clamp((actual / planned) - 1.0) (>= 0.25)       |
   |   - Goal Change Score        : S_gc = (latest_prompt != roadmap_goal)                 |
   |   ==> Composite Stuck Score  : max(repeated_failure, stall_time)                      |
   |   ==> Composite Disengaged   : max(inactivity, 0.8 * pace_drift)                      |
   +------------------------------------------+--------------------------------------------+
                                              |
                                              v
   +---------------------------------------------------------------------------------------+
   | 2. DECIDE POLICY (Deterministic Priority Table)                                       |
   |   - Priority 1: Goal Drift Detected             --> GOAL_REPLAN                       |
   |   - Priority 2: Stuck on Graded Assessment      --> CONCEPT_NUDGE                     |
   |   - Priority 3: Stuck on Practice Exercise      --> HINT_LADDER                       |
   |   - Priority 4: Disengaged / Inactive           --> REENGAGE                          |
   |   - Priority 5: Ahead of Schedule               --> REPLAN_UP                         |
   |   - Priority 6: Healthy Flow State              --> NONE                              |
   +------------------------------------------+--------------------------------------------+
                                              |
                                              v
   +---------------------------------------------------------------------------------------+
   | 3. ACT LAYER (Guardrails, Generation & Roadmap Replanning)                            |
   |   Pre-Gen Guardrails  : Consent Check | Quiet Hours (22:00-08:00) | 24h Cooldown     |
   |                         Weekly Cap (<=3 nudges/7 days) | Graded Work Rule             |
   |   Generator           : Live Anthropic API (Claude 3.5) OR Deterministic Template    |
   |   Post-Gen Validation : <= 60 Words | Zero Code/Backticks on Graded | Concept Index  |
   |   Replanner           : Multi-Pass Topological Sort | Weekly Capacity Spillover       |
   +------------------------------------------+--------------------------------------------+
                                              |
                                              v
   +---------------------------------------------------------------------------------------+
   | 4. FOLLOW-UP & 48H ESCALATION LADDER                                                  |
   |   - Evaluates progress 48 hours post-intervention:                                    |
   |       * Target Passed OR >= 2 Sessions --> RECOVERED   (Triggers REPLAN_UP)           |
   |       * 1 Session without Pass         --> PARTIAL     (Triggers MICRO_LESSON)        |
   |       * 0 Sessions (Unresponsive)      --> NO_RESPONSE (Triggers 5-min REENGAGE)      |
   |       * 2nd Consecutive No-Response    --> ESCALATED   (Alerts Human Mentor)          |
   +---------------------------------------------------------------------------------------+
```

---

## 3. Quick Start (3-Command Setup)

Get Scholar Pulse running locally in three commands:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the automated test suite (319 tests across Tiers 1-5)
pytest

# 3. Launch the development server
uvicorn app.main:app --reload --port 8000
```

Once started, open your browser to:
**`http://localhost:8000`**

To enable live LLM generation via Anthropic Claude instead of deterministic template fallback:
```bash
# Optional: Set Anthropic API Key
export ANTHROPIC_API_KEY="sk-ant-..."      # Linux / macOS
$env:ANTHROPIC_API_KEY="sk-ant-..."        # Windows PowerShell
```

---

## 4. Click-by-Click Aarav Walkthrough

To verify the entire proactive intervention and recovery lifecycle in the UI:

### Scenario Profile: Aarav Sharma
- **Track**: Web Development Track
- **Friction**: Stuck on graded assessment `P-014` (*Async Data Fetch Pipeline*).
- **Telemetry**: 6 failed attempts on P-014 in the last 72h (4 with `TypeError:undefined_iterable`), 740 minutes spent (estimated: 180 min, 1.5x threshold 270 min), pace drift score 0.50, composite stuck score 1.00.

### Step-by-Step UI Flow:

1. **Open the Dashboard**:
   - Navigate to `http://localhost:8000`.
   - Ensure the student selector in the top bar is set to **"Aarav Sharma"**.
   - Note the **Template Fallback** (or Live LLM) badge and simulated date (`Nov 18, 2026`).

2. **Inspect Baseline Telemetry**:
   - In the **Activity Timeline**, observe the cluster of red failure dots on `P-014`.
   - In the **Signal Gauges**, observe `Stuck Score: 1.00` (High Risk), `Repeated Failure: 1.00`, `Stall Time: 1.00`, and `Pace Drift: 0.50`.
   - Notice that `P-014` is marked with a **Graded** badge.

3. **Click "Evaluate now"**:
   - Click the indigo **"Evaluate now"** button in the top control bar.
   - Observe the **Decision Card**:
     - Selected Action: `CONCEPT_NUDGE` (Priority 2).
     - Rationale: Plain-English explanation citing Aarav's 6 repeated failures and stall time on graded `P-014` (*"Student is stuck on graded problem P-014: Stuck: 6 failed attempts on P-014 in the last 72h, 4 with the same error TypeError:undefined_iterable; Spent 370 minutes on P-014 (estimated 180 min, 1.5x threshold 270 min) without passing. Graded assessment rules strictly forbid hints; directing to prerequisite concept 'promises-error-handling' without solution code."*).
     - Guardrails Checklist: 5 green checkmarks (Consent: Passed, Quiet Hours: Passed, Weekly Cap: Passed, 24h Cooldown: Passed, Graded Work: Passed — hints & code strictly suppressed).
   - Observe the **Nudge Chat Bubble**:
     - Concise nudge (<60 words) recommending prerequisite concept `promises-error-handling`.
     - Zero code fences or backticks (preserving academic integrity).
     - Interactive Call-to-Action: *"Review Promises & Error Handling Patterns"*.
   - Observe the **Roadmap Diff**:
     - A targeted 25-minute micro-lesson (`promises-error-handling`: *Micro-Lesson: Promises & Error Handling Patterns*) has been inserted into Week 2 *immediately before* `P-014`.

4. **Advance Time to Trigger Recovery**:
   - Click the **"Advance 3 days"** button in the top bar.
   - The simulation clock advances from `Nov 18, 2026` to `Nov 21, 2026` (72 hours elapsed, crossing the 48-hour follow-up threshold).
   - The simulation engine executes Aarav's scripted behavior: he completes the recommended micro-lesson and successfully passes `P-014`.

5. **Observe 48h Follow-up Outcome**:
   - Click **"Evaluate now"** (or view the automated follow-up update).
   - In the **Interventions & Follow-up History** table:
     - Status updates from `pending_followup` to **`recovered`** (green badge).
     - Outcome details: *"Recovered: Target problem passed post-nudge."*
     - Follow-up evaluation triggers `REPLAN_UP` to pull forward future tasks and capitalize on Aarav's recovered momentum!

6. **Switch to Cohort View**:
   - Toggle **"Cohort view"** in the top bar.
   - Observe the cohort table listing at-risk students (Meera: High Risk Inactivity; Rohan: Goal Drift; Priya: Consent-Disabled Alert).
   - View the aggregate metrics strip: Recovery Rate, Average Hours to Recover, Nudges Sent, Nudges Blocked, and Escalations.

---

## 5. Architectural & Design Decisions

### Decision 1: Deterministic Signals over LLM Telemetry Hallucinations
- **Rationale**: Relying on an LLM to read raw JSON logs and guess whether a student is stuck introduces non-deterministic hallucinations, latency (2–4s per check), and uncontrollable variance.
- **Implementation**: Scholar Pulse computes all 6 signals and composite scores (`stuck`, `disengaged`) using pure, deterministic mathematical formulas with explicit evidence strings and 100% unit-tested threshold boundaries.

### Decision 2: Fixed-Priority Pedagogical Policy Table
- **Rationale**: When multiple friction points coincide (e.g., a student changes their goal AND is struggling on a problem), ambiguous heuristics cause conflicting nudges.
- **Implementation**: A strict priority order dictates action:
  1. `GOAL_REPLAN` (Highest priority: learning roadmap must align with student intent)
  2. `CONCEPT_NUDGE` (Student stuck on graded assessment: prerequisite concept reference only)
  3. `HINT_LADDER` (Student stuck on practice exercise: Socratic scaffolding allowed)
  4. `REENGAGE` (Student inactive or drifting behind schedule: low-friction task)
  5. `REPLAN_UP` (Student ahead of pace: accelerate curriculum)
  6. `NONE` (Student in healthy flow state: do not disturb)

### Decision 3: Strict Two-Tier Guardrail Safety Envelope
- **Pre-Generation Envelope**: Checks user consent, quiet hours (10 PM – 8 AM), rolling 7-day volume cap ($\le 3$ delivered nudges), 24-hour kind-specific cooldown, and graded work rules *before* invoking any generator.
- **Post-Generation Envelope**: Validates that generated text is $\le 60$ words, forbids code fences (```` ``` ````) and backticks on graded work, ensures referenced concepts belong to `CURATED_CONCEPTS`, and verifies roadmap diff topological validity.

### Decision 4: Seamless Dual-Mode LLM & Deterministic Fallback
- **Rationale**: CI/CD pipelines, offline grading, and local evaluation must run instantly without requiring paid API keys or risking network timeouts.
- **Implementation**: The generator checks for `ANTHROPIC_API_KEY`. If set, it calls Claude 3.5 Sonnet with temperature 0.2 and structured system prompts; if missing, it seamlessly defaults to deterministic templates producing identical schemas.

### Decision 5: Multi-Pass Topological Roadmap Replanner
- **Rationale**: Moving items forward or backward without topological checks causes prerequisite violations (e.g. attempting advanced async code before basic promises).
- **Implementation**: `replan_roadmap` executes up to $N+10$ iterative passes to resolve prerequisite constraints, handles cyclic dependencies gracefully without infinite recursion, and cascades weekly load overflow when hours exceed `weekly_capacity_min`.

### Decision 6: Student-Isolated Follow-up & Escalation
- **Rationale**: If a multi-tenant or batch evaluation compares prior interventions across students, an unresponsive student might cause another student to be falsely escalated to a mentor.
- **Implementation**: `evaluate_followup` strictly scopes escalation history to `p.student_id == intervention.student_id`, ensuring completely isolated escalation ladders.

---

## 6. Mocked Components vs. StepCode Production Integration

Scholar Pulse is structured with clean interfaces so mocked components can be directly swapped for production StepCode services:

| System Layer | Scholar Pulse Implementation | StepCode Production Target | Integration Interface |
|---|---|---|---|
| **Student Telemetry** | In-memory `Store` with seeded event stream | Kafka / AWS Kinesis / StepCode EventBridge | Consume telemetry stream into `detect_all_signals` |
| **Student Identity & Consent** | In-memory `Student` model with `consent_nudges` | StepCode PostgreSQL `users` & `user_settings` | `GET /api/v1/students/{id}/profile` |
| **Curriculum & Roadmap** | In-memory `RoadmapItem` list with prerequisites | StepCode Graph Curriculum DB (Neo4j / Postgres) | `replan_roadmap()` emits `RoadmapDiff` applied via DB transaction |
| **Simulation Clock** | Simulated mutable `sim_now` with `/api/sim/advance` | Standard UTC wall clock (`datetime.now(timezone.utc)`) | Periodic Celery / Temporal cron worker triggering evaluations |
| **Nudge Delivery** | API response & UI chat bubble | StepCode In-App Notification Websocket / Email / Slack | `send_nudge_dispatch(channel, payload)` |
| **Mentor Alert Portal** | Escalation logged to `Intervention` record (`status=escalated`) | StepCode Mentor Ops Dashboard & PagerDuty/Zendesk webhook | `POST /api/v1/mentor/alerts` with student diagnostic dossier |
| **LLM Provider** | Anthropic Claude 3.5 Sonnet / Haiku | StepCode Central AI Gateway (LiteLLM / Bedrock) | Drop-in Anthropic API endpoint or proxy URL |

---

## 7. Automated Testing

Scholar Pulse features a comprehensive 5-Tier test suite comprising **319 automated tests** with 100% pass rate.

### Test Execution Commands:

```bash
# Run all 319 tests
python -m pytest

# Run with verbose output and timing
python -m pytest -v

# Run by specific module
python -m pytest tests/test_signals.py -v         # Signal scoring & formulas
python -m pytest tests/test_policy.py -v          # Policy table & rationale
python -m pytest tests/test_guardrails.py -v      # Pre/post generation guardrails
python -m pytest tests/test_replanner.py -v       # Roadmap topological replanner
python -m pytest tests/test_followup.py -v        # 48h outcomes & escalation ladder
python -m pytest tests/test_generator.py -v       # Mode detection & template fallbacks
python -m pytest tests/test_api.py -v             # FastAPI REST endpoints & store

# Run by End-to-End Test Tier
python -m pytest tests/test_e2e_t1_features.py -v    # Tier 1: Core Feature Verification
python -m pytest tests/test_e2e_t2_boundaries.py -v  # Tier 2: Boundary & Corner Cases
python -m pytest tests/test_e2e_t3_combinations.py -v# Tier 3: Combinatorial & Multi-Signal
python -m pytest tests/test_e2e_t4_scenarios.py -v   # Tier 4: Full Student Scenarios (Aarav, Meera, Rohan, Priya)
python -m pytest tests/test_e2e_t5_adversarial.py -v # Tier 5: Adversarial, Concurrency & Security
```

### Test Suite Structure (319 Tests):
- **Tier 1 (Features)**: 35 tests covering all signal algorithms, decision branches, guardrail rules, and outcome states.
- **Tier 2 (Boundaries)**: 35 tests covering window edges, zero values, floating point thresholds, and word cap limits.
- **Tier 3 (Combinations)**: 35 tests covering simultaneous signals, conflicting states, and complex policy interactions.
- **Tier 4 (Scenarios)**: 35 tests validating complete end-to-end multi-day lifecycles for all 4 student personas.
- **Tier 5 (Adversarial & Hardening)**: 39 tests covering SQL injection, path traversal, Unicode/emoji inputs, multi-threaded store concurrency (750 operations), clock overflow prevention, and cross-student isolation.
- **Unit & Integration**: 140 tests covering pure models, store CRUD, generator fallback, and REST endpoints.

---

## 8. Repository Layout

```
Scholar AI/
├── app/
│   ├── __init__.py
│   ├── config.py           # System thresholds, constants & quiet hours
│   ├── models.py           # Pydantic v2 schemas & curated concepts catalog
│   ├── store.py            # Thread-safe in-memory store with RLock protection
│   ├── signals.py          # Deterministic signal detection engine
│   ├── policy.py           # Priority pedagogical decision policy
│   ├── guardrails.py       # Pre-generation envelope & post-gen validation
│   ├── generator.py        # Anthropic Claude 3.5 & deterministic templates
│   ├── replanner.py        # Multi-pass topological roadmap replanner
│   ├── followup.py         # 48h outcome evaluator & escalation ladder
│   ├── sim.py              # Clock advancement & student scripted behaviors
│   ├── api.py              # FastAPI route handlers & cohort metrics
│   └── main.py             # App factory, static files & root routes
├── static/
│   ├── index.html          # Single-page interactive frontend
│   ├── style.css           # Modern dark-slate ed-tech stylesheet
│   └── app.js              # Vanilla JavaScript controller (zero external dependencies)
├── tests/
│   ├── conftest.py         # Shared fixtures, client factory & state resets
│   ├── test_signals.py     # Signal formula unit tests
│   ├── test_policy.py      # Policy decision unit tests
│   ├── test_guardrails.py  # Guardrail rule tests
│   ├── test_replanner.py   # Replanner algorithm tests
│   ├── test_followup.py    # Followup outcome tests
│   ├── test_generator.py   # Generator mode tests
│   ├── test_api.py         # REST API integration tests
│   ├── test_e2e_t1_features.py
│   ├── test_e2e_t2_boundaries.py
│   ├── test_e2e_t3_combinations.py
│   ├── test_e2e_t4_scenarios.py
│   └── test_e2e_t5_adversarial.py
├── docs/
│   └── writeup.md          # 9-section architectural challenge writeup
├── pytest.ini              # Pytest configuration & markers
├── requirements.txt        # Pinned production & test dependencies
└── README.md               # System documentation & walkthrough
```
