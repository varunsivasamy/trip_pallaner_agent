import json
from functools import partial

from ..llm import LLM
from ..models import ItineraryPlan, TripState
from ..planning import cluster_by_day, day_capacities
from ..prompts import ITINERARY_PROMPT


def _check_plan(plan: ItineraryPlan, known_ids: set[str], day_count: int) -> None:
    if [day.day for day in plan.days] != list(range(1, day_count + 1)):
        raise ValueError(f"days must be numbered 1..{day_count}, in order, one entry per input day.")
    ids = [stop.place_id for day in plan.days for stop in day.stops]
    if unknown := set(ids) - known_ids:
        raise ValueError(f"unknown place_ids: {sorted(unknown)}. Use only ids from the input.")
    if len(ids) != len(set(ids)):
        raise ValueError("a place appears more than once.")


class ItineraryAgent:
    name = "itinerary"

    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def run(self, state: TripState) -> None:
        requested_days = state.require_days()
        clusters = cluster_by_day(state.places, day_capacities(requested_days))
        if len(clusters) < requested_days:
            state.warn(f"Only enough well-rated places were found for {len(clusters)} of {requested_days} days.")
        payload = {
            "days": [
                {"day": number, "places": [place.model_dump(exclude={"address"}) for place in cluster]}
                for number, cluster in enumerate(clusters, start=1)
            ],
            "weather": [w.model_dump(mode="json") for w in state.weather],
            **state.request.model_dump(mode="json", include={"interests", "constraints"}),
            "review_issues": state.review_issues,
        }
        known_ids = {place.id for cluster in clusters for place in cluster}
        state.itinerary = await self._llm.structured(
            ITINERARY_PROMPT,
            json.dumps(payload),
            ItineraryPlan,
            check=partial(_check_plan, known_ids=known_ids, day_count=len(clusters)),
        )


PlannerAgent = ItineraryAgent

