from datetime import timedelta

from ..models import TripState
from ..ports import ForecastProvider
from .base import Clock

FORECAST_HORIZON_DAYS = 16


class WeatherAgent:
    name = "weather"

    def __init__(self, provider: ForecastProvider, today: Clock) -> None:
        self._provider = provider
        self._today = today

    async def run(self, state: TripState) -> None:
        start, end = state.request.start_date, state.request.end_date
        if start is None or end is None:
            state.warn("No travel dates were given, so no weather forecast was fetched.")
            return
        last_forecast_day = self._today() + timedelta(days=FORECAST_HORIZON_DAYS - 1)
        if start > last_forecast_day:
            state.warn(f"Forecasts only reach {last_forecast_day}; check the weather closer to departure.")
            return
        if end > last_forecast_day:
            state.warn(f"The forecast covers only up to {last_forecast_day}; later days are not forecast yet.")
        state.weather = await self._provider.daily(state.require_geo(), start, min(end, last_forecast_day))
