# E2E Test Suite Ready

## Test Runner
- Command: `python -m pytest tests/`
- Target E2E Track Command: `python -m pytest tests/test_e2e_t1_features.py tests/test_e2e_t2_boundaries.py tests/test_e2e_t3_combinations.py tests/test_e2e_t4_scenarios.py`
- Marker Commands:
  - `python -m pytest -m tier1`
  - `python -m pytest -m tier2`
  - `python -m pytest -m tier3`
  - `python -m pytest -m tier4`
- Expected: All tests pass with exit code 0 (unimplemented downstream modules skip cleanly with informative messages until implemented).

## Coverage Summary
| Tier | Count | Description |
|------|------:|-------------|
| 1. Feature Coverage | 50 | Isolated nominal tests covering F1 through F7 (>=5 per feature area) |
| 2. Boundary & Corner | 42 | BVA, clamping bounds [0.0, 1.0], exact threshold transitions, pass suppression borders, error bonus cutoffs, and zero-division guards |
| 3. Cross-Feature | 25 | Pairwise cross-feature interactions across Signals x Policy, Policy x Guardrails, Validator x Engine, Follow-up x Clock, and Cohort x Metrics |
| 4. Real-World Application | 5 | End-to-end multi-step flows (Aarav recovery, Meera drop-off, Rohan shift, Priya opt-out, Cohort lifecycle) |
| Unit Suites & Fixtures | 92 | Comprehensive unit coverage (`tests/test_signals.py`, `tests/test_policy.py`, etc.) and test infrastructure |
| **Total Suite Items** | **214** | **122 Dedicated E2E Tier 1-4 tests + 92 Unit tests, 100% exit code 0** |

## Feature Checklist
| Feature Area | Key Contracts & Requirements | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|--------------|------------------------------|:------:|:------:|:------:|:------:|
| **F1: Signal Detection Engine** | 6 signals (`repeated_failure`, `stall_time`, `inactivity`, `pace_drift`, `pace_ahead`, `goal_change`) + 2 composites (`stuck`, `disengaged`) | 7 | 8 | ✓ | ✓ |
| **F2: Policy Decision Engine** | Priorities 1-6 mapping signal states to primary interventions (`GOAL_REPLAN`, `CONCEPT_NUDGE`, `HINT_LADDER`, `REENGAGE`, `REPLAN_UP`, `NONE`) | 6 | 6 | ✓ | ✓ |
| **F3: Pre-Gen Guardrails** | Consent check, quiet hours window, weekly cap (3), cooldown (24h), graded work rule | 6 | 6 | ✓ | ✓ |
| **F4: Post-Gen & Re-planner** | Word cap <= 60, code fence ban on graded, concept index membership, topological integrity & weekly capacity | 6 | 6 | ✓ | ✓ |
| **F5: Follow-up & Escalation** | +48h outcome evaluation (`recovered`, `partial`, `no_response`) and escalation ladder (`MICRO_LESSON`, `REENGAGE`, `ESCALATE_MENTOR`) | 6 | 5 | ✓ | ✓ |
| **F6: Backend REST API & Store** | 9 FastAPI endpoints (`/mode`, `/students`, `/students/{id}`, `/evaluate`, `/interventions`, `/sim/advance`, `/sim/reset`, `/cohort`, `/metrics`) | 9 | 5 | ✓ | ✓ |
| **F7: Seed Scenarios & Sim** | Reproducible RNG seed scenarios: Aarav, Meera, Rohan, Priya (Consent-disabled) | 10 | 6 | ✓ | ✓ |

## Test Suite Architecture & Infrastructure
- `pytest.ini`: Project root configuration with custom markers (`unit`, `api`, `tier1`, `tier2`, `tier3`, `tier4`).
- `tests/conftest.py`: Root pytest fixtures:
  - `reset_simulation_state`: `autouse=True` state isolation fixture resetting store and clock before and after each test.
  - `client`: TestClient with lazy import of FastAPI application.
  - Mock factories: `make_student`, `make_roadmap_item`, `make_event`, `make_roadmap`, `make_failure_events`, `make_stall_events`, `make_inactivity_events`.
  - Baseline scenarios: `aarav_scenario_data`, `meera_scenario_data`, `rohan_scenario_data`, `priya_consent_disabled_scenario_data`.
  - Mode helpers: `force_template_mode`, `force_live_mode`, `api_mode`.
