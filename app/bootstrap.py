"""Composition root: the only place that knows which concrete classes are used."""

from datetime import date

import httpx

from .adapters import (
    GdacsEvents,
    GooglePlaces,
    OpenMeteoForecast,
    OpenMeteoGeocoder,
    SandboxBookingProvider,
    UsgsEarthquakes,
)
from .agents.base import Clock
from .agents.booking import BookingAgent
from .agents.critic import CriticAgent
from .agents.intake import IntakeAgent
from .agents.itinerary import ItineraryAgent
from .agents.locate import LocateAgent
from .agents.places import PlacesAgent
from .agents.presenter import PresenterAgent
from .agents.safety import SafetyAgent
from .agents.weather import WeatherAgent
from .config import Settings
from .llm import LLM, OllamaBackend
from .orchestrator import AgentSet, Orchestrator


def build_orchestrator(settings: Settings, http: httpx.AsyncClient, clock: Clock = date.today) -> Orchestrator:
    llm = LLM(OllamaBackend(http, settings.ollama_url, settings.ollama_model))
    return Orchestrator(
        AgentSet(
            intake=IntakeAgent(llm, clock),
            locate=LocateAgent(OpenMeteoGeocoder(http)),
            safety=SafetyAgent([UsgsEarthquakes(http), GdacsEvents(http)], llm),
            weather=WeatherAgent(OpenMeteoForecast(http), clock),
            places=PlacesAgent(GooglePlaces(http, settings.google_places_key)),
            itinerary=ItineraryAgent(llm),
            critic=CriticAgent(),
            booking=BookingAgent(SandboxBookingProvider()),
            presenter=PresenterAgent(llm),
        )
    )
