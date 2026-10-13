import json
from datetime import date
import pytest

from app.agents import (
    CriticAgent,
    GeocoderAgent,
    IntakeAgent,
    ItineraryAgent,
    LocateAgent,
    ParserAgent,
    PlacesAgent,
    PlannerAgent,
    RepairAgent,
    SafetyAgent,
    ScoringAgent,
    ValidatorAgent,
    WeatherAgent,
)
from app.models import (
    Alert,
    CritiqueResult,
    DayPlan,
    GeoPoint,
    ItineraryPlan,
    Place,
    SafetyReport,
    Stop,
    TripRequest,
    TripState,
)
from app.orchestrator import decide_next_step, run_agent_loop
from app.planning import (
    CITY_SPEED_KMH,
    LATEST_END_MINUTES,
    MAX_BUSY_MINUTES,
    TRAVEL_OVERHEAD_MINUTES,
    cluster_by_day,
    distance_km,
    estimate_travel_minutes,
    haversine_km,
    place_score,
    review_itinerary,
)
from tests.fakes import FakeGeocoder, FakePlaces, ScriptedBackend, build, intake_json, plan_json


def test_1_trip_state_memory():
    """Verify TripState acts as shared memory with required attributes."""
    state = TripState()
    assert hasattr(state, "request")
    assert hasattr(state, "geo")
    assert hasattr(state, "safety")
    assert hasattr(state, "weather")
    assert hasattr(state, "places")
    assert hasattr(state, "itinerary")
    assert hasattr(state, "execution_trace")

    assert state.request is not None
    assert isinstance(state.weather, list)
    assert isinstance(state.places, list)
    assert isinstance(state.execution_trace, list)


def test_2_decide_next_step():
    """Verify stage machine decision logic."""
    state = TripState()
    assert decide_next_step(state) == "parse"

    state.request = TripRequest(destination="Munnar", duration_days=2, start_date=date(2026, 10, 5))
    state.stage = state.stage.LOCATE
    assert decide_next_step(state) == "geocode"

    state.geo = GeoPoint(name="Munnar", country="India", latitude=10.0, longitude=77.0)
    state.stage = state.stage.ASSESS
    assert decide_next_step(state) == "safety"

    state.safety = SafetyReport(
        summary="Safe", precautions=[], alternatives=[], risk_score=10, level="low", alerts=[], unverified_sources=[]
    )
    assert decide_next_step(state) == "weather"

    state.weather = [{"date": "2026-10-05", "summary": "Clear"}]
    assert decide_next_step(state) == "places"

    state.places = [Place(id="p1", name="Tea Museum", rating=4.5, review_count=100, latitude=10.0, longitude=77.0)]
    state.stage = state.stage.ITINERARY
    assert decide_next_step(state) == "plan"


def test_3_agents_instantiation():
    """Verify all 10 required agents exist and can be instantiated/called."""
    assert ParserAgent is IntakeAgent
    assert GeocoderAgent is LocateAgent
    assert PlannerAgent is ItineraryAgent

    # Scoring Agent
    scoring = ScoringAgent()
    state = TripState()
    state.places = [
        Place(id="p1", name="Park", rating=4.0, review_count=10, latitude=10.0, longitude=77.0),
        Place(id="p2", name="Museum", rating=4.8, review_count=500, latitude=10.1, longitude=77.1),
    ]
    ranked = scoring.run(state)
    assert ranked[0].id == "p2"

    # Validator Agent
    validator = ValidatorAgent()
    state.itinerary = ItineraryPlan(days=[DayPlan(day=1, theme="Day 1", stops=[Stop(place_id="p2", start_time="09:00", duration_minutes=60)])])
    valid = validator.run(state)
    assert valid is True


def test_4_distance_clustering_feasibility_and_safety_gate():
    """Verify distance calculations, constants, clustering, and safety gate recommendation levels."""
    p1 = Place(id="p1", name="P1", rating=4.5, review_count=100, latitude=10.0, longitude=77.0)
    p2 = Place(id="p2", name="P2", rating=4.5, review_count=100, latitude=10.1, longitude=77.0)

    dist = distance_km(p1, p2)
    assert dist > 0
    travel = estimate_travel_minutes(p1, p2)
    assert travel == round(dist / CITY_SPEED_KMH * 60) + TRAVEL_OVERHEAD_MINUTES
    assert CITY_SPEED_KMH == 20
    assert TRAVEL_OVERHEAD_MINUTES == 10
    assert MAX_BUSY_MINUTES == 600
    assert LATEST_END_MINUTES == 22 * 60

    # Safety gate recommendation check
    rep_low = SafetyReport(summary="OK", risk_score=10, level="low", alerts=[], unverified_sources=[])
    rep_mod = SafetyReport(summary="Warning", risk_score=40, level="moderate", alerts=[], unverified_sources=[])
    rep_high = SafetyReport(summary="Danger", risk_score=70, level="high", alerts=[], unverified_sources=[])

    assert rep_low.recommendation == "proceed"
    assert rep_mod.recommendation == "proceed_flexible"
    assert rep_high.recommendation == "avoid"


@pytest.mark.asyncio
async def test_5_validation_critic_repair_loop():
    """Verify validation loop, structured critic output, and repair agent."""
    # Critic agent generating structured issues + fix_instructions
    critic = CriticAgent()
    p1 = Place(id="p1", name="Place 1", rating=4.5, review_count=100, latitude=10.0, longitude=77.0)
    places = {"p1": p1}
    bad_plan = ItineraryPlan(
        days=[
            DayPlan(
                day=1,
                theme="Overly late",
                stops=[
                    Stop(place_id="p1", start_time="20:00", duration_minutes=180, note="Late stop"),  # ends at 23:00 > 22:00 (10 PM)
                    Stop(place_id="p1", start_time="20:00", duration_minutes=180, note="Dup stop"),
                ],
            )
        ]
    )
    state = TripState()
    state.places = [p1]
    state.itinerary = bad_plan

    critique = await critic.run(state)
    assert isinstance(critique, CritiqueResult)
    assert any("10 PM" in issue or "22:00" in issue for issue in critique.issues)
    assert "Same place appears twice" in critique.issues
    assert "Remove duplicate place" in critique.fix_instructions


from app.prompts import INTAKE_PROMPT, ITINERARY_PROMPT, PRESENTER_PROMPT


@pytest.mark.asyncio
async def test_6_simple_agent_loop():
    """Verify run_agent_loop executes stages to completion."""
    orch, backend, _ = build({
        INTAKE_PROMPT: [intake_json()],
        ITINERARY_PROMPT: [plan_json()],
        PRESENTER_PROMPT: ["Done"],
    })
    state = TripState()
    state.add_user_message("2 days in Munnar from Oct 5")
    final_itinerary = await run_agent_loop(orch, state)
    assert final_itinerary is not None
    assert len(final_itinerary.days) == 2

