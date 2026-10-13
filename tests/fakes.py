import json
from datetime import date

from app.adapters import SandboxBookingProvider
from app.agents.booking import BookingAgent
from app.agents.critic import CriticAgent
from app.agents.intake import IntakeAgent
from app.agents.itinerary import ItineraryAgent
from app.agents.locate import LocateAgent
from app.agents.places import PlacesAgent
from app.agents.presenter import PresenterAgent
from app.agents.safety import SafetyAgent
from app.agents.weather import WeatherAgent
from app.llm import LLM
from app.models import Alert, DailyWeather, GeoPoint, Place
from app.orchestrator import AgentSet, Orchestrator
from app.planning import cluster_by_day, day_capacities

TODAY = date(2026, 9, 30)
GEO = GeoPoint(name="Munnar", country="India", latitude=10.0, longitude=77.0)
PLACES = [
    Place(id=f"p{i}", name=f"Place {i}", rating=4.5, review_count=100 + i, latitude=10.0 + 0.001 * i, longitude=77.0)
    for i in range(8)
]
INTAKE_OK = {
    "origin": "Coimbatore", "destination": "Munnar", "start_date": "2026-10-05", "duration_days": 2,
    "travelers": 2, "currency": "INR", "interests": ["tea gardens"], "constraints": [], "booking_requested": True,
    "booking_confirmed": True,
}


def intake_json(**overrides) -> str:
    return json.dumps({**INTAKE_OK, **overrides})


def plan_json(start_times=("09:00", "12:00", "15:00")) -> str:
    clusters = cluster_by_day(PLACES, day_capacities(2))
    days = [
        {"day": n, "theme": f"Day {n} theme",
         "stops": [{"place_id": p.id, "start_time": t, "duration_minutes": 60, "note": ""} for p, t in zip(c, start_times)]}
        for n, c in enumerate(clusters, start=1)
    ]
    return json.dumps({"days": days})


class ScriptedBackend:
    """Replays queued replies per system prompt; an Exception in the queue is raised instead."""

    def __init__(self, script: dict[str, list]) -> None:
        self._script = {key: list(values) for key, values in script.items()}
        self.calls: list[str] = []

    async def chat(self, system, user, schema=None):
        self.calls.append(system)
        item = self._script[system].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeGeocoder:
    def __init__(self, geo=GEO):
        self._geo = geo

    async def locate(self, query):
        return self._geo


class FakeForecast:
    def __init__(self, error=None):
        self._error = error

    async def daily(self, geo, start, end):
        if self._error:
            raise self._error
        return [DailyWeather(date=start, summary="clear"), DailyWeather(date=end, summary="rain")]


class FakeHazard:
    def __init__(self, name="USGS", alerts=(), error=None):
        self.name, self._alerts, self._error = name, list(alerts), error

    async def fetch(self, geo):
        if self._error:
            raise self._error
        return self._alerts


class FakePlaces:
    def __init__(self):
        self.calls = 0

    async def search(self, query, geo):
        self.calls += 1
        return PLACES


class CountingBooking(SandboxBookingProvider):
    def __init__(self, fail_on: int | None = None):
        self.calls, self._fail_on = 0, fail_on

    async def reserve(self, item):
        self.calls += 1
        if self.calls == self._fail_on:
            from app.errors import RetryableError
            raise RetryableError("provider down")
        return await super().reserve(item)


def red_alert() -> Alert:
    return Alert(kind="earthquake", severity="red", source="USGS", description="M7.1 earthquake, 40km away")


def build(script, *, geocoder=None, forecast=None, hazards=None, booking=None):
    backend = ScriptedBackend(script)
    llm, places = LLM(backend), FakePlaces()
    hazards = hazards if hazards is not None else [FakeHazard()]
    orchestrator = Orchestrator(AgentSet(
        intake=IntakeAgent(llm, lambda: TODAY), locate=LocateAgent(geocoder or FakeGeocoder()),
        safety=SafetyAgent(hazards, llm), weather=WeatherAgent(forecast or FakeForecast(), lambda: TODAY),
        places=PlacesAgent(places), itinerary=ItineraryAgent(llm), critic=CriticAgent(),
        booking=BookingAgent(booking or CountingBooking()), presenter=PresenterAgent(llm),
    ))
    return orchestrator, backend, places
