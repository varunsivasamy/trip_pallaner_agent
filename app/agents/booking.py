from ..models import BookingItem, TripRequest, TripState
from ..ports import BookingProvider


def _items(request: TripRequest, refundable_only: bool) -> list[BookingItem]:
    nights = max((request.duration_days or 1) - 1, 1)
    outbound = BookingItem(
        kind="transport", description=f"{request.origin} to {request.destination}",
        travel_date=request.start_date, refundable_only=refundable_only,
    )
    stay = BookingItem(
        kind="lodging",
        description=f"{nights} night(s) in {request.destination} for {request.travelers} traveler(s)",
        travel_date=request.start_date, refundable_only=refundable_only,
    )
    inbound = BookingItem(
        kind="transport", description=f"{request.destination} to {request.origin}",
        travel_date=request.end_date, refundable_only=refundable_only,
    )
    return [outbound, stay, inbound]


class BookingAgent:
    """Deterministic on purpose: no LLM decides what gets bought. Runs only when the traveler asked to book."""

    name = "booking"

    def __init__(self, provider: BookingProvider) -> None:
        self._provider = provider

    async def run(self, state: TripState) -> None:
        if state.reservations:  # never double-book when the traveler refines the plan
            return
        refundable_only = state.safety is None or state.safety.level != "low"
        for item in _items(state.request, refundable_only):
            state.reservations.append(await self._provider.reserve(item))
