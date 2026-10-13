from ..models import TripState
from ..planning import review_itinerary


class ValidatorAgent:
    name = "validator"

    def run(self, state: TripState) -> bool:
        """Check timing feasibility, duplicate places, and budget constraints.
        Returns True if the itinerary is valid (no issues), False otherwise.
        """
        if not state.itinerary:
            return False
        places = {place.id: place for place in state.places}
        issues, fix_instructions = review_itinerary(
            state.itinerary, places, budget_total=state.request.budget_total
        )
        state.review_issues = issues
        state.fix_instructions = fix_instructions
        return len(issues) == 0
