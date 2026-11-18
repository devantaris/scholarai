# E2E Test Infra: Scholar Pulse

## Test Philosophy
- Opaque-box, requirement-driven. Derived from `ORIGINAL_REQUEST.md` and user-facing specifications.
- No dependency on private implementation details; tests interact through FastAPI HTTP endpoints / TestClient and public interface contracts.
- Methodology: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial Testing + Real-World Workload Testing across 4 tiers.

## Feature Inventory
| # | Feature Area | Key Behaviors & Contracts | Tier 1 (Count) | Tier 2 (Count) | Tier 3 | Tier 4 |
|---|--------------|---------------------------|:--------------:|:--------------:|:------:|:------:|
| F1 | Signal Detection Engine | 6 deterministic signals + 2 composite scores (`stuck`, `disengaged`) | 6 | 6 | ✓ | ✓ |
| F2 | Policy Decision Engine | Priorities 1-6 mapping signal states to primary interventions | 6 | 6 | ✓ | ✓ |
| F3 | Pre-Gen Guardrails | Consent, quiet hours, weekly cap (3), cooldown (24h), graded work rule | 5 | 5 | ✓ | ✓ |
| F4 | Post-Gen & Re-planner Validation | Word count <=60, code fence ban on graded, concept index check, topological integrity | 5 | 5 | ✓ | ✓ |
| F5 | Follow-up & Escalation Ladder | +48h outcome evaluation (`recovered`, `partial`, `no_response`) and escalation ladder | 5 | 5 | ✓ | ✓ |
| F6 | Backend REST API & Store | 9 endpoints (`/api/mode`, `/api/students`, `/api/students/{id}`, `/evaluate`, `/interventions`, `/sim/advance`, `/sim/reset`, `/cohort`, `/metrics`) | 9 | 9 | ✓ | ✓ |
| F7 | Seed Scenarios & Simulation | Aarav, Meera, Rohan, Consent-disabled scenarios & reproducible RNG | 5 | 5 | ✓ | ✓ |

## Test Architecture
- **Framework**: `pytest` with `fastapi.testclient.TestClient` / `httpx`.
- **Test Invocation**: `python -m pytest tests/`
- **Pass/Fail Semantics**: 100% exit code 0, no unhandled exceptions, all assertions pass.
- **Directory Layout**:
  - `tests/conftest.py`: Shared pytest fixtures, TestClient initialization, clean state isolation, seed scenario helpers.
  - `tests/test_e2e_t1_features.py`: Tier 1 Feature Coverage (>=5 isolated tests per feature area).
  - `tests/test_e2e_t2_boundaries.py`: Tier 2 Boundary & Corner Cases (>=5 boundary/edge tests per feature area).
  - `tests/test_e2e_t3_combinations.py`: Tier 3 Cross-Feature Combinations (pairwise interactions).
  - `tests/test_e2e_t4_scenarios.py`: Tier 4 Real-World Application Scenarios (end-to-end flows for Aarav, Meera, Rohan, Priya/Consent-disabled).

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Student | Key Characteristics & Expected Behavior |
|---|----------|---------|-----------------------------------------|
| 1 | Main Demo Story | Aarav | Graded problem P-014 stall -> `CONCEPT_NUDGE` + roadmap re-plan -> simulate 3 days -> `recovered` |
| 2 | Silent Drop-off | Meera | Inactivity -> `REENGAGE` -> no response -> escalation ladder (`partial` / `escalated`) |
| 3 | Acceleration & Shift | Rohan | Ahead of pace -> new goal prompt -> `GOAL_REPLAN` full re-plan |
| 4 | Consent-Disabled Student | Priya / Student 4 | Inactivity/stall with consent=False -> guardrail blocks message -> flagged on `/api/cohort` |
| 5 | Complete Simulation Lifecycle | Multi-student | Reset -> advance 1 day -> evaluate all -> advance 3 days -> verify `/api/metrics` and cohort aggregates |

## Coverage Thresholds
- Tier 1: >=5 per feature area (total >=35 tests)
- Tier 2: >=5 boundary/corner tests per feature area (total >=35 tests)
- Tier 3: Pairwise coverage of cross-feature interactions (>=15 tests)
- Tier 4: >=5 realistic end-to-end multi-step application scenarios
- **Total Minimum Target**: ~90-100+ comprehensive opaque-box test cases
