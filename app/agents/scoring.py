from ..models import Place, TripState
from ..planning import place_score


class ScoringAgent:
    name = "scoring"

    def run(self, state: TripState) -> list[Place]:
        """Rank places by Bayesian rating, review count, distance, and traveler interests."""
        interests = {i.lower() for i in state.request.interests}

        def rank_key(p: Place) -> float:
            base = place_score(p)
            boost = 0.5 if any(interest in p.name.lower() or interest in p.address.lower() for interest in interests) else 0.0
            return base + boost

        state.places.sort(key=rank_key, reverse=True)
        return state.places
