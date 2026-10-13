import json
from functools import partial

from ..llm import LLM
from ..models import ItineraryPlan, TripState
from ..planning import cluster_by_day, day_capacities
from ..prompts import ITINERARY_PROMPT
from .itinerary import _check_plan


class RepairAgent:
    """Fixes only the reported problems in the itinerary plan."""

    name = "repair"

    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def run(self, state: TripState) -> None:
        if not state.itinerary or not state.review_issues:
            return

        requested_days = state.require_days()
        clusters = cluster_by_day(state.places, day_capacities(requested_days))
        known_ids = {place.id for cluster in clusters for place in cluster}

        payload = {
            "days": [
                {"day": number, "places": [place.model_dump(exclude={"address"}) for place in cluster]}
                for number, cluster in enumerate(clusters, start=1)
            ],
            "current_itinerary": state.itinerary.model_dump(mode="json"),
            "review_issues": state.review_issues,
            "fix_instructions": state.fix_instructions,
            "weather": [w.model_dump(mode="json") for w in state.weather],
            **state.request.model_dump(mode="json", include={"interests", "constraints"}),
        }

        state.itinerary = await self._llm.structured(
            ITINERARY_PROMPT,
            json.dumps(payload),
            ItineraryPlan,
            check=partial(_check_plan, known_ids=known_ids, day_count=len(clusters)),
        )
