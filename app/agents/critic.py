from ..models import CritiqueResult, TripState
from ..planning import review_itinerary


class CriticAgent:
    """Finds problems and suggests fix instructions in a structured format."""

    name = "critic"

    async def run(self, state: TripState) -> CritiqueResult:
        if not state.itinerary:
            critique = CritiqueResult(
                issues=["No itinerary plan found"],
                fix_instructions=["Create an itinerary first"],
            )
            state.review_issues = critique.issues
            state.fix_instructions = critique.fix_instructions
            return critique

        places = {place.id: place for place in state.places}
        issues, fix_instructions = review_itinerary(
            state.itinerary, places, budget_total=state.request.budget_total
        )
        critique = CritiqueResult(issues=issues, fix_instructions=fix_instructions)
        state.review_issues = critique.issues
        state.fix_instructions = critique.fix_instructions
        return critique
