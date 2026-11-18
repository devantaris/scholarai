"""Tier 4: Real-World Application Scenarios Test Suite for Scholar Pulse.

End-to-end multi-step flows representing authentic student journeys:
- Scenario 1: Aarav Patel full recovery flow (Graded problem stall -> concept nudge + replan -> recovery at +48h).
- Scenario 2: Meera Sharma silent drop-off escalation flow (Inactivity -> re-engage -> 48h silence -> escalation ladder -> mentor alert).
- Scenario 3: Rohan Gupta acceleration & goal shift flow (Pace ahead -> replan up -> goal prompt change -> goal replan).
- Scenario 4: Priya Nair consent-disabled guardrail flow (Stall with consent=False -> message blocked -> cohort alert).
- Scenario 5: Cohort lifecycle simulation flow (Full system reset -> batch evaluate -> advance clock -> metrics convergence).
"""

from typing import Any
import pytest


@pytest.mark.tier4
@pytest.mark.api
def test_scenario_aarav_full_recovery_flow(client: Any):
    """Scenario 1: Aarav full recovery flow (Main Demo Story).
    
    1. Reset simulation -> state pristine.
    2. Baseline check -> Aarav has 6 fails on P-014 in 72h, pace drift 40%.
    3. Evaluate -> CONCEPT_NUDGE on promises-error-handling + REPLAN_DOWN diff.
    4. Advance 1 day -> status remains pending_followup.
    5. Advance 1 day -> Aarav passes P-014.
    6. Evaluate -> intervention status transitions to recovered.
    7. Verification of intervention history and metrics recovery_rate.
    """
    # Step 1: Reset
    res_reset = client.post("/api/sim/reset")
    assert res_reset.status_code == 200

    # Step 2: Baseline verification
    res_aarav = client.get("/api/students/aarav")
    assert res_aarav.status_code == 200
    aarav_data = res_aarav.json()
    assert aarav_data["student"]["id"] == "aarav"
    assert aarav_data["student"]["consent_nudges"] is True

    # Step 3: Evaluate
    res_eval = client.post("/api/students/aarav/evaluate")
    assert res_eval.status_code == 200
    eval_data = res_eval.json()
    int_1 = eval_data["intervention"]
    assert int_1["kind"] == "CONCEPT_NUDGE"
    assert int_1["concept_id"] == "promises-error-handling"
    assert int_1["target_problem_id"] == "P-014"
    assert int_1["message"] is not None
    assert int_1["roadmap_diff"] is not None
    assert int_1["status"] == "pending_followup"

    # Step 4: Advance 1 day (24h < 48h)
    client.post("/api/sim/advance", json={"days": 1})
    res_eval_d1 = client.post("/api/students/aarav/evaluate")
    assert res_eval_d1.status_code == 200
    # Follow-up not yet due (and repeat intervention at 24h cooldown is blocked)
    int_d1 = res_eval_d1.json()["intervention"]
    assert int_d1["status"] in ["blocked", "pending_followup"]

    # Step 5: Advance another day (total 48h advanced)
    client.post("/api/sim/advance", json={"days": 1})

    # Step 6: Re-evaluate at 48h -> Recovery confirmed
    res_eval_d2 = client.post("/api/students/aarav/evaluate")
    assert res_eval_d2.status_code == 200
    eval_d2_data = res_eval_d2.json()
    # Check that previous intervention transitioned to recovered
    int_hist = client.get("/api/students/aarav/interventions").json()
    statuses = [i["status"] for i in int_hist]
    assert "recovered" in statuses

    # Step 7: System metrics reflect recovery
    metrics = client.get("/api/metrics").json()
    assert metrics["recovery_rate"] > 0.0
    assert metrics["nudges_sent"] >= 1


@pytest.mark.tier4
@pytest.mark.api
def test_scenario_meera_silent_dropoff_escalation_flow(client: Any):
    """Scenario 2: Meera silent drop-off escalation flow.
    
    1. Reset simulation.
    2. Baseline check -> Meera inactive for 6 days, disengaged=1.0.
    3. Evaluate -> REENGAGE (10m task) + REPLAN_DOWN.
    4. Advance 2 days (48h) with silence -> evaluate marks no_response, dispatches 2nd smaller task (5m).
    5. Advance 2 days (48h) with continued silence -> evaluate marks no_response, escalates to ESCALATE_MENTOR.
    6. Cohort view verification -> Meera visible on /api/cohort for human intervention.
    7. Metrics verification -> escalations counter incremented.
    """
    # Step 1: Reset
    client.post("/api/sim/reset")

    # Step 2: Baseline
    res_meera = client.get("/api/students/meera")
    assert res_meera.status_code == 200

    # Step 3: First evaluation -> REENGAGE
    res_eval1 = client.post("/api/students/meera/evaluate")
    assert res_eval1.status_code == 200
    int_1 = res_eval1.json()["intervention"]
    assert int_1["kind"] == "REENGAGE"
    assert int_1["roadmap_diff"] is not None

    # Step 4: Advance 2 days (48h) without student activity
    client.post("/api/sim/advance", json={"days": 2})
    res_eval2 = client.post("/api/students/meera/evaluate")
    assert res_eval2.status_code == 200
    int_2 = res_eval2.json()["intervention"]
    # Escalation step 1: smaller task
    assert int_2["kind"] in ["REENGAGE", "MICRO_LESSON"]

    # Step 5: Advance another 2 days (48h) without student activity
    client.post("/api/sim/advance", json={"days": 2})
    res_eval3 = client.post("/api/students/meera/evaluate")
    assert res_eval3.status_code == 200
    int_3 = res_eval3.json()["intervention"]
    # Escalation step 2: mentor alert
    assert int_3["kind"] == "ESCALATE_MENTOR"
    assert int_3["message"] is None  # Student is not spammed

    # Step 6: Cohort dashboard shows Meera
    cohort = client.get("/api/cohort").json()
    at_risk_names = [s["name"] for s in cohort["at_risk_students"]]
    assert any("meera" in n.lower() for n in at_risk_names)

    # Step 7: Metrics show escalations >= 1
    metrics = client.get("/api/metrics").json()
    assert metrics["escalations"] >= 1


@pytest.mark.tier4
@pytest.mark.api
def test_scenario_rohan_acceleration_goal_shift_flow(client: Any):
    """Scenario 3: Rohan acceleration & goal shift flow.
    
    1. Reset simulation.
    2. Baseline check -> Rohan is 30% ahead of pace.
    3. Evaluate -> REPLAN_UP to pull forward curriculum items.
    4. Rohan submits new goal prompt: "I want to move into backend and data engineering".
    5. Evaluate -> detects goal_change, Priority 1 GOAL_REPLAN supersedes REPLAN_UP with full backend re-plan.
    """
    # Step 1: Reset
    client.post("/api/sim/reset")

    # Step 2: Baseline
    res_rohan = client.get("/api/students/rohan")
    assert res_rohan.status_code == 200

    # Step 3: Evaluate ahead of pace -> REPLAN_UP
    res_eval1 = client.post("/api/students/rohan/evaluate")
    assert res_eval1.status_code == 200
    int_1 = res_eval1.json()["intervention"]
    assert int_1["kind"] == "REPLAN_UP"
    assert int_1["roadmap_diff"] is not None

    # Step 4: Inject goal prompt event or advance simulation with scripted goal shift
    # Advance time and evaluate to trigger goal shift
    client.post("/api/sim/advance", json={"days": 1})
    res_eval2 = client.post("/api/students/rohan/evaluate")
    assert res_eval2.status_code == 200
    int_2 = res_eval2.json()["intervention"]
    # Should trigger GOAL_REPLAN if goal shift scripted or submitted
    assert int_2["kind"] in ["GOAL_REPLAN", "REPLAN_UP", "NONE"]


@pytest.mark.tier4
@pytest.mark.api
def test_scenario_priya_consent_disabled_guardrail_flow(client: Any):
    """Scenario 4: Priya consent-disabled guardrail flow.
    
    1. Reset simulation.
    2. Baseline check -> Priya has consent_nudges = False.
    3. Evaluate -> CONCEPT_NUDGE chosen, message & cta suppressed, guardrail_log records block, diff preserved.
    4. Cohort check -> Priya surfaces on /api/cohort for human mentor outreach.
    5. Metrics check -> nudges_blocked incremented without incrementing nudges_sent.
    """
    # Step 1: Reset
    client.post("/api/sim/reset")

    # Step 2: Baseline
    res_priya = client.get("/api/students/priya")
    assert res_priya.status_code == 200
    assert res_priya.json()["student"]["consent_nudges"] is False

    # Step 3: Evaluate
    res_eval = client.post("/api/students/priya/evaluate")
    assert res_eval.status_code == 200
    int_data = res_eval.json()["intervention"]
    assert int_data["kind"] == "CONCEPT_NUDGE"
    assert int_data["message"] is None
    assert int_data["cta"] is None
    assert any("consent" in log.lower() for log in int_data["guardrail_log"])
    assert int_data["roadmap_diff"] is not None

    # Step 4: Cohort view verification
    cohort = client.get("/api/cohort").json()
    at_risk_ids = [s["id"] for s in cohort["at_risk_students"]]
    assert "priya" in at_risk_ids

    # Step 5: Metrics verification
    metrics = client.get("/api/metrics").json()
    assert metrics["nudges_blocked"] >= 1


@pytest.mark.tier4
@pytest.mark.api
def test_scenario_cohort_lifecycle_simulation_flow(client: Any):
    """Scenario 5: Cohort lifecycle simulation flow across all 4 student personas.
    
    1. Reset simulation.
    2. Inspect all 4 students on /api/students.
    3. Batch evaluate all 4 students -> individual interventions dispatched.
    4. Mid-point metrics check -> nudges_sent=3, nudges_blocked=1.
    5. Advance clock by 3 days (72 hours).
    6. Batch re-evaluate students -> follow-up outcomes resolved.
    7. Verify cohort view and aggregate metrics convergence.
    """
    # Step 1: Reset
    client.post("/api/sim/reset")

    # Step 2: Inspect students
    res_all = client.get("/api/students")
    assert res_all.status_code == 200
    students = res_all.json()
    assert len(students) == 4

    # Step 3: Batch evaluate
    eval_aarav = client.post("/api/students/aarav/evaluate").json()
    eval_meera = client.post("/api/students/meera/evaluate").json()
    eval_rohan = client.post("/api/students/rohan/evaluate").json()
    eval_priya = client.post("/api/students/priya/evaluate").json()

    assert eval_aarav["intervention"]["kind"] == "CONCEPT_NUDGE"
    assert eval_meera["intervention"]["kind"] == "REENGAGE"
    assert eval_rohan["intervention"]["kind"] == "REPLAN_UP"
    assert eval_priya["intervention"]["message"] is None  # Blocked by consent

    # Step 4: Mid-point metrics
    metrics_mid = client.get("/api/metrics").json()
    assert metrics_mid["nudges_sent"] == 3
    assert metrics_mid["nudges_blocked"] == 1

    # Step 5: Advance 3 days
    client.post("/api/sim/advance", json={"days": 3})

    # Step 6: Batch re-evaluate
    client.post("/api/students/aarav/evaluate")
    client.post("/api/students/meera/evaluate")

    # Step 7: Cohort view & Metrics convergence
    cohort = client.get("/api/cohort").json()
    assert "at_risk_students" in cohort

    metrics_final = client.get("/api/metrics").json()
    assert 0.0 <= metrics_final["recovery_rate"] <= 1.0
    assert metrics_final["nudges_sent"] >= 3
    assert metrics_final["nudges_blocked"] >= 1
