"""Interfaces the agents depend on. Adapters implement them; tests fake them."""

from datetime import date
from typing import Protocol

from .models import Alert, BookingItem, DailyWeather, GeoPoint, Place, Reservation


class Geocoder(Protocol):
    async def locate(self, query: str) -> GeoPoint | None: ...


class ForecastProvider(Protocol):
    async def daily(self, geo: GeoPoint, start: date, end: date) -> list[DailyWeather]: ...


class HazardSource(Protocol):
    name: str

    async def fetch(self, geo: GeoPoint) -> list[Alert]: ...


class PlacesProvider(Protocol):
    async def search(self, query: str, geo: GeoPoint) -> list[Place]: ...


class BookingProvider(Protocol):
    async def reserve(self, item: BookingItem) -> Reservation: ...
