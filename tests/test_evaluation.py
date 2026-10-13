import pytest
from app.evaluation import (
    evaluate_coherence,
    evaluate_constraint_satisfaction,
    evaluate_interest_coverage,
    evaluate_safety_adherence,
    evaluate_trip,
)
from app.models import (
    Alert,
    DayPlan,
    ItineraryPlan,
    Place,
    SafetyReport,
    Stop,
    TripRequest,
    TripState,
)


def test_evaluate_constraint_satisfaction():
    state = TripState()
    state.places = [
        Place(id="p1", name="Place 1", rating=4.5, review_count=100, latitude=12.0, longitude=77.0),
        Place(id="p2", name="Place 2", rating=4.8, review_count=200, latitude=12.01, longitude=77.01),
    ]
    state.itinerary = ItineraryPlan(
        days=[
            DayPlan(
                day=1,
                theme="City exploration",
                stops=[
                    Stop(place_id="p1", start_time="09:00", duration_minutes=120),
                    Stop(place_id="p2", start_time="12:00", duration_minutes=120),
                ],
            )
        ]
    )

    res = evaluate_constraint_satisfaction(state)
    assert res.passed is True
    assert res.score == 1.0


def test_evaluate_interest_coverage():
    state = TripState()
    state.request = TripRequest(interests=["Museums", "Parks"])
    state.itinerary = ItineraryPlan(
        days=[
            DayPlan(
                day=1,
                theme="Museums tour",
                stops=[
                    Stop(place_id="p1", start_time="09:00", duration_minutes=120, note="Visit famous history museum"),
                ],
            )
        ]
    )

    res = evaluate_interest_coverage(state)
    assert res.score == 0.5
    assert "Museums" in res.details


def test_evaluate_safety_adherence():
    state = TripState()
    state.safety = SafetyReport(
        summary="High risk area",
        risk_score=75,
        level="high",
        alerts=[Alert(kind="weather", severity="red", source="met", description="Storm")],
        unverified_sources=[],
    )
    state.stage = "done"

    res = evaluate_safety_adherence(state)
    assert res.passed is True
    assert res.score == 1.0


@pytest.mark.asyncio
async def test_evaluate_trip_full():
    state = TripState()
    state.request = TripRequest(interests=["Food"])
    state.places = [
        Place(id="p1", name="Cafe", rating=4.5, review_count=100, latitude=12.0, longitude=77.0),
    ]
    state.itinerary = ItineraryPlan(
        days=[
            DayPlan(
                day=1,
                theme="Food tasting",
                stops=[Stop(place_id="p1", start_time="10:00", duration_minutes=60, note="Local food spot")],
            )
        ]
    )

    report = await evaluate_trip(state)
    assert report.trip_id == state.trip_id
    assert len(report.automated_metrics) == 4
    assert report.overall_score >= 0.0
