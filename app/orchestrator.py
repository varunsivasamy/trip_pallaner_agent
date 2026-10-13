"""Stage machine over the shared TripState. It routes, retries and degrades; it never calls a tool itself."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .agents.base import Agent
from .errors import NeedsInput, RetryableError
from .models import ItineraryPlan, Stage, StepTrace, TripState

log = logging.getLogger(__name__)

_TERMINAL = {Stage.DONE, Stage.NEEDS_INPUT, Stage.FAILED}


@dataclass(frozen=True)
class AgentSet:
    intake: Agent
    locate: Agent
    safety: Agent
    weather: Agent
    places: Agent
    itinerary: Agent
    critic: Agent
    booking: Agent
    presenter: Agent

    @property
    def parser(self) -> Agent:
        return self.intake

    @property
    def geocoder(self) -> Agent:
        return self.locate

    @property
    def planner(self) -> Agent:
        return self.itinerary

    @property
    def validator(self) -> Agent:
        return self.critic


def decide_next_step(state: TripState) -> str:
    """Decides which step to run next given the current TripState memory.
    Returns one of: 'parse', 'geocode', 'safety', 'weather', 'places', 'plan', 'validate', 'repair', 'done'.
    """
    if state.stage in _TERMINAL:
        return "done"
    if state.stage == Stage.INTAKE:
        return "parse"
    if state.stage == Stage.LOCATE:
        return "geocode"
    if state.stage == Stage.ASSESS:
        if state.safety is None:
            return "safety"
        if not state.weather and state.request.start_date is not None:
            return "weather"
        if state.blocked_by_safety:
            return "done"
        return "places"
    if state.stage == Stage.SEARCH:
        return "places"
    if state.stage == Stage.ITINERARY:
        if state.review_issues:
            return "repair"
        return "plan"
    if state.stage == Stage.REVIEW:
        if state.review_issues:
            return "repair"
        return "validate"
    if state.stage in (Stage.BOOKING, Stage.PRESENT):
        return "done"
    return "done"


class Orchestrator:
    def __init__(self, agents: AgentSet, *, max_attempts: int = 2, max_review_rounds: int = 2) -> None:
        self._agents = agents
        self._max_attempts = max_attempts
        self._max_review_rounds = max_review_rounds
        self._handlers: dict[Stage, Callable[[TripState], Awaitable[None]]] = {
            Stage.INTAKE: self._intake,
            Stage.LOCATE: self._locate,
            Stage.ASSESS: self._assess,
            Stage.SEARCH: self._search,
            Stage.ITINERARY: self._itinerary,
            Stage.REVIEW: self._review,
            Stage.BOOKING: self._booking,
            Stage.PRESENT: self._present,
        }

    async def run(self, state: TripState) -> TripState:
        while state.stage not in _TERMINAL:
            stage = state.stage
            try:
                await self._handlers[stage](state)
                state.execution_trace.append(
                    StepTrace(stage=stage, status="success", detail=f"Completed {stage}")
                )
            except NeedsInput as need:
                state.execution_trace.append(
                    StepTrace(stage=stage, status="needs_input", detail=need.question)
                )
                state.ask(need.question)
            except Exception as exc:  # top-level boundary: log details, show the traveler a safe message
                state.execution_trace.append(
                    StepTrace(stage=stage, status="failed", detail=str(exc))
                )
                log.exception("Stage %s failed", stage)
                state.fail(f"The '{stage}' step failed. Please try again in a moment.")
        return state

    async def _run(self, agent: Agent, state: TripState, attempts: int | None = None) -> None:
        limit = attempts or self._max_attempts
        for attempt in range(1, limit + 1):
            try:
                await agent.run(state)
                return
            except RetryableError as exc:
                log.warning("%s attempt %d/%d failed: %s", agent.name, attempt, limit, exc)
                if attempt == limit:
                    raise

    async def _run_optional(self, agent: Agent, state: TripState) -> None:
        try:
            await self._run(agent, state)
        except RetryableError:
            state.warn(f"The {agent.name} step was unavailable, so the plan continues without it.")

    async def _intake(self, state: TripState) -> None:
        await self._run(self._agents.intake, state)
        state.stage = Stage.LOCATE

    async def _locate(self, state: TripState) -> None:
        await self._run(self._agents.locate, state)
        state.stage = Stage.ASSESS

    async def _assess(self, state: TripState) -> None:
        await asyncio.gather(
            self._run(self._agents.safety, state),
            self._run_optional(self._agents.weather, state),
        )
        state.stage = Stage.PRESENT if state.blocked_by_safety else Stage.SEARCH  # the safety gate

    async def _search(self, state: TripState) -> None:
        await self._run(self._agents.places, state)
        state.stage = Stage.ITINERARY

    async def _itinerary(self, state: TripState) -> None:
        await self._run(self._agents.itinerary, state)
        state.stage = Stage.REVIEW

    async def _review(self, state: TripState) -> None:
        await self._run(self._agents.critic, state)
        if state.review_issues and state.review_rounds < self._max_review_rounds:
            state.review_rounds += 1
            state.stage = Stage.ITINERARY
            return
        if state.review_issues:
            state.warn("Some timing issues remain in the plan: " + " ".join(state.review_issues))
        state.stage = Stage.BOOKING

    async def _booking(self, state: TripState) -> None:
        if state.request.booking_requested:
            if not state.request.booking_confirmed:
                # Human-in-the-loop checkpoint: pause and ask traveler before committing bookings
                dest = state.request.destination or "your destination"
                state.ask(
                    f"Your itinerary is ready! Would you like me to confirm and book the reservations for {dest}?"
                )
                return

            try:
                await self._run(self._agents.booking, state, attempts=1)  # never retry a purchase
            except RetryableError:
                state.warn("Booking did not finish. Check the reservations listed; nothing further was booked.")
        state.stage = Stage.PRESENT

    async def _present(self, state: TripState) -> None:
        try:
            await self._run(self._agents.presenter, state)
        except RetryableError:
            state.warn("The written summary could not be generated; the structured plan is attached instead.")
        state.stage = Stage.DONE

    # Step-by-step methods matching Section 6 interface
    async def parse_request(self, state: TripState) -> None:
        await self._intake(state)

    async def geocode_destination(self, state: TripState) -> None:
        await self._locate(state)

    async def check_safety(self, state: TripState) -> None:
        await self._run(self._agents.safety, state)

    async def get_weather(self, state: TripState) -> None:
        await self._run_optional(self._agents.weather, state)

    async def find_places(self, state: TripState) -> None:
        await self._search(state)

    async def create_itinerary(self, state: TripState) -> None:
        await self._itinerary(state)

    async def validate_itinerary(self, state: TripState) -> None:
        await self._review(state)

    async def repair_itinerary(self, state: TripState) -> None:
        await self._itinerary(state)


async def run_agent_loop(orchestrator: Orchestrator, state: TripState) -> ItineraryPlan | None:
    """Simple agent loop implementing the stage machine iteration."""
    finished = False
    while not finished:
        current_step = decide_next_step(state)

        if current_step == "parse":
            await orchestrator.parse_request(state)

        elif current_step == "geocode":
            await orchestrator.geocode_destination(state)

        elif current_step == "safety":
            await orchestrator.check_safety(state)

        elif current_step == "weather":
            await orchestrator.get_weather(state)

        elif current_step == "places":
            await orchestrator.find_places(state)

        elif current_step == "plan":
            await orchestrator.create_itinerary(state)

        elif current_step == "validate":
            await orchestrator.validate_itinerary(state)

        elif current_step == "repair":
            await orchestrator.repair_itinerary(state)

        elif current_step == "done":
            finished = True
            return state.itinerary

    return state.itinerary
