from ..errors import NeedsInput
from ..models import TripState
from ..ports import Geocoder


class LocateAgent:
    name = "locate"

    def __init__(self, geocoder: Geocoder) -> None:
        self._geocoder = geocoder

    async def run(self, state: TripState) -> None:
        destination = state.request.destination or ""
        state.geo = await self._geocoder.locate(destination)
        if state.geo is None:
            raise NeedsInput(f"I could not find '{destination}'. Which city or region, and country, do you mean?")


GeocoderAgent = LocateAgent

