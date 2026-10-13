import asyncio

from ..errors import NeedsInput
from ..models import TripRequest, TripState
from ..ports import PlacesProvider

MAX_QUERIES = 5


def _queries(request: TripRequest) -> list[str]:
    destination = request.destination
    base = [f"top attractions in {destination}", f"best local restaurants in {destination}"]
    return (base + [f"{interest} in {destination}" for interest in request.interests])[:MAX_QUERIES]


class PlacesAgent:
    name = "places"

    def __init__(self, provider: PlacesProvider) -> None:
        self._provider = provider

    async def run(self, state: TripState) -> None:
        geo = state.require_geo()
        batches = await asyncio.gather(*(self._provider.search(q, geo) for q in _queries(state.request)))
        unique = {place.id: place for batch in batches for place in batch}
        if not unique:
            raise NeedsInput(f"I found no rated places in {geo.name}. Could you name a larger nearby town or region?")
        state.places = list(unique.values())
