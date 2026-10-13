import json

from app.errors import RetryableError
from app.models import Stage
from app.prompts import (
    INTAKE_PROMPT,
    ITINERARY_PROMPT,
    PRESENTER_PROMPT,
    SAFETY_ADVISOR_PROMPT,
)
from tests.fakes import (
    CountingBooking,
    FakeForecast,
    FakeGeocoder,
    FakeHazard,
    build,
    intake_json,
    plan_json,
    red_alert,
)


async def test_happy_path_books_because_the_user_asked():
    orch, _, _ = build({INTAKE_PROMPT: [intake_json()], ITINERARY_PROMPT: [plan_json()], PRESENTER_PROMPT: ["REPLY"]})
    from app.models import TripState
    state = TripState(); state.add_user_message("2 days Munnar from Coimbatore on Oct 5, book it")
    await orch.run(state)
    assert state.stage == Stage.DONE and state.reply == "REPLY"
    assert state.safety.level == "low" and len(state.weather) == 2
    assert len(state.itinerary.days) == 2 and len(state.reservations) == 3
    assert all(r.item.refundable_only is False for r in state.reservations)


async def run(orch, message="plan it"):
    from app.models import TripState
    state = TripState(); state.add_user_message(message)
    return await orch.run(state)


async def test_no_booking_unless_requested():
    orch, _, _ = build({INTAKE_PROMPT: [intake_json(booking_requested=False)], ITINERARY_PROMPT: [plan_json()], PRESENTER_PROMPT: ["R"]})
    assert (await run(orch)).reservations == []


async def test_missing_destination_asks_the_traveler():
    orch, _, places = build({INTAKE_PROMPT: [intake_json(destination=None)]})
    state = await run(orch)
    assert state.stage == Stage.NEEDS_INPUT and "destination" in state.question and places.calls == 0


async def test_booking_needs_origin_and_dates_up_front():
    orch, _, _ = build({INTAKE_PROMPT: [intake_json(origin=None)]})
    state = await run(orch)
    assert state.stage == Stage.NEEDS_INPUT and "departure city" in state.question


async def test_unknown_destination_asks_the_traveler():
    orch, _, _ = build({INTAKE_PROMPT: [intake_json()]}, geocoder=FakeGeocoder(geo=None))
    assert (await run(orch)).stage == Stage.NEEDS_INPUT


async def test_high_risk_blocks_search_and_booking():
    advice = json.dumps({"summary": "Strong quake nearby.", "precautions": [], "alternatives": ["Shift dates"]})
    orch, _, places = build(
        {INTAKE_PROMPT: [intake_json()], SAFETY_ADVISOR_PROMPT: [advice], PRESENTER_PROMPT: ["ADVISORY"]},
        hazards=[FakeHazard(alerts=[red_alert()])],
    )
    state = await run(orch)
    assert state.stage == Stage.DONE and state.blocked_by_safety
    assert places.calls == 0 and state.itinerary is None and state.reservations == []


async def test_unreachable_hazard_sources_are_never_treated_as_safe():
    orch, _, _ = build(
        {INTAKE_PROMPT: [intake_json(booking_requested=False)], ITINERARY_PROMPT: [plan_json()], PRESENTER_PROMPT: ["R"]},
        hazards=[FakeHazard(error=RetryableError("down"))],
    )
    state = await run(orch)
    assert state.safety.level == "moderate" and state.safety.unverified_sources == ["USGS"]
    assert any("incomplete" in w for w in state.warnings)


async def test_weather_failure_degrades_instead_of_failing():
    orch, _, _ = build(
        {INTAKE_PROMPT: [intake_json()], ITINERARY_PROMPT: [plan_json()], PRESENTER_PROMPT: ["R"]},
        forecast=FakeForecast(error=RetryableError("down")),
    )
    state = await run(orch)
    assert state.stage == Stage.DONE and state.weather == [] and any("weather" in w for w in state.warnings)


async def test_invalid_llm_json_is_repaired_once():
    orch, backend, _ = build({
        INTAKE_PROMPT: ["not json", intake_json()], ITINERARY_PROMPT: [plan_json()], PRESENTER_PROMPT: ["R"],
    })
    state = await run(orch)
    assert state.stage == Stage.DONE and backend.calls.count(INTAKE_PROMPT) == 2


async def test_critic_sends_the_plan_back_then_accepts_the_fix():
    overlapping = plan_json(start_times=("09:00", "09:00", "09:00"))
    orch, backend, _ = build({
        INTAKE_PROMPT: [intake_json(booking_requested=False)],
        ITINERARY_PROMPT: [overlapping, plan_json()], PRESENTER_PROMPT: ["R"],
    })
    state = await run(orch)
    assert backend.calls.count(ITINERARY_PROMPT) == 2 and state.review_rounds == 1 and state.review_issues == []


async def test_unresolved_review_issues_stop_after_max_rounds_with_a_warning():
    bad = plan_json(start_times=("09:00", "09:00", "09:00"))
    orch, backend, _ = build({
        INTAKE_PROMPT: [intake_json(booking_requested=False)], ITINERARY_PROMPT: [bad, bad, bad], PRESENTER_PROMPT: ["R"],
    })
    state = await run(orch)
    assert backend.calls.count(ITINERARY_PROMPT) == 3 and state.stage == Stage.DONE
    assert any("timing issues" in w for w in state.warnings)


async def test_failed_purchase_is_not_retried():
    booking = CountingBooking(fail_on=2)
    orch, _, _ = build(
        {INTAKE_PROMPT: [intake_json()], ITINERARY_PROMPT: [plan_json()], PRESENTER_PROMPT: ["R"]}, booking=booking
    )
    state = await run(orch)
    assert booking.calls == 2 and len(state.reservations) == 1
    assert state.stage == Stage.DONE and any("Booking did not finish" in w for w in state.warnings)


async def test_presenter_failure_still_returns_the_structured_plan():
    orch, _, _ = build({
        INTAKE_PROMPT: [intake_json(booking_requested=False)], ITINERARY_PROMPT: [plan_json()],
        PRESENTER_PROMPT: [RetryableError("x"), RetryableError("x")],
    })
    state = await run(orch)
    assert state.stage == Stage.DONE and state.reply is None and state.itinerary is not None


async def test_follow_up_message_replans_but_keeps_reservations():
    orch, _, _ = build({
        INTAKE_PROMPT: [intake_json(), intake_json(interests=["waterfalls"])],
        ITINERARY_PROMPT: [plan_json(), plan_json()], PRESENTER_PROMPT: ["R1", "R2"],
    })
    state = await run(orch)
    state.add_user_message("add waterfalls")
    await orch.run(state)
    assert state.reply == "R2" and len(state.reservations) == 3


async def test_human_in_the_loop_pauses_for_confirmation_before_booking():
    orch, _, _ = build({
        INTAKE_PROMPT: [intake_json(booking_requested=True, booking_confirmed=False)],
        ITINERARY_PROMPT: [plan_json()],
    })
    state = await run(orch, "2 days in Munnar from Coimbatore, please book it")
    assert state.stage == Stage.NEEDS_INPUT
    assert state.question is not None and "confirm and book" in state.question
    assert state.itinerary is not None
    assert state.reservations == []


async def test_human_in_the_loop_confirms_via_message_and_books():
    orch, _, _ = build({
        INTAKE_PROMPT: [intake_json(booking_requested=True, booking_confirmed=False)],
        ITINERARY_PROMPT: [plan_json()],
        PRESENTER_PROMPT: ["REPLY_CONFIRMED"],
    })
    state = await run(orch, "2 days in Munnar from Coimbatore, please book it")
    assert state.stage == Stage.NEEDS_INPUT and state.reservations == []

    # Traveler reviews plan and confirms:
    state.add_user_message("Yes, go ahead and book it")
    await orch.run(state)
    assert state.stage == Stage.DONE
    assert len(state.reservations) == 3
    assert state.reply == "REPLY_CONFIRMED"


async def test_human_in_the_loop_cancels_booking_if_declined():
    orch, _, _ = build({
        INTAKE_PROMPT: [intake_json(booking_requested=True, booking_confirmed=False)],
        ITINERARY_PROMPT: [plan_json()],
        PRESENTER_PROMPT: ["REPLY_DECLINED"],
    })
    state = await run(orch, "2 days in Munnar from Coimbatore, please book it")
    assert state.stage == Stage.NEEDS_INPUT and state.reservations == []

    # Traveler reviews plan and declines booking:
    state.add_user_message("No, don't book")
    await orch.run(state)
    assert state.stage == Stage.DONE
    assert len(state.reservations) == 0
    assert state.request.booking_requested is False
