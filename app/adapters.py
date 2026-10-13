"""Concrete implementations of the ports: free public APIs, Google Places, and a sandbox booker."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

from .http import request_json
from .models import (
    Alert,
    BookingItem,
    DailyWeather,
    GeoPoint,
    Place,
    Reservation,
    Severity,
)
from .planning import haversine_km

_WMO_CODES = [
    (0, "clear"), (3, "partly cloudy to overcast"), (48, "fog"), (57, "drizzle"), (67, "rain"),
    (77, "snow"), (82, "rain showers"), (86, "snow showers"), (99, "thunderstorms"),
]


def describe_weather_code(code: int | None) -> str:
    if code is None:
        return "unknown"
    return next((text for limit, text in _WMO_CODES if code <= limit), "unknown")


def _parse_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class OpenMeteoGeocoder:
    _URL = "https://geocoding-api.open-meteo.com/v1/search"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def locate(self, query: str) -> GeoPoint | None:
        name = query.split(",")[0].strip()  # the API matches place names, not "City, Country"
        data = await request_json(self._http, "GET", self._URL, params={"name": name, "count": 1, "language": "en"})
        results = data.get("results") or []
        if not results:
            return None
        found = results[0]
        return GeoPoint(
            name=found["name"], country=found.get("country", ""),
            latitude=found["latitude"], longitude=found["longitude"],
        )


class OpenMeteoForecast:
    _URL = "https://api.open-meteo.com/v1/forecast"
    _DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max"

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def daily(self, geo: GeoPoint, start: date, end: date) -> list[DailyWeather]:
        params = {
            "latitude": geo.latitude, "longitude": geo.longitude, "daily": self._DAILY,
            "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "auto",
        }
        daily = (await request_json(self._http, "GET", self._URL, params=params))["daily"]
        return [
            DailyWeather(
                date=daily["time"][i],
                summary=describe_weather_code(daily["weather_code"][i]),
                temp_min=daily["temperature_2m_min"][i],
                temp_max=daily["temperature_2m_max"][i],
                precip_probability=daily["precipitation_probability_max"][i],
                wind_max_kmh=daily["wind_speed_10m_max"][i],
            )
            for i in range(len(daily["time"]))
        ]


class UsgsEarthquakes:
    name = "USGS"
    _URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    _RADIUS_KM = 300
    _LOOKBACK_DAYS = 14
    _MIN_MAGNITUDE = 5

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self, geo: GeoPoint) -> list[Alert]:
        since = datetime.now(UTC) - timedelta(days=self._LOOKBACK_DAYS)
        params = {
            "format": "geojson", "latitude": geo.latitude, "longitude": geo.longitude,
            "maxradiuskm": self._RADIUS_KM, "minmagnitude": self._MIN_MAGNITUDE,
            "starttime": since.date().isoformat(),
        }
        data = await request_json(self._http, "GET", self._URL, params=params)
        return [self._to_alert(feature["properties"]) for feature in data.get("features", [])]

    @staticmethod
    def _to_alert(props: dict[str, Any]) -> Alert:
        magnitude = props["mag"]
        severity: Severity = "red" if magnitude >= 7 else "orange" if magnitude >= 6 else "green"
        return Alert(
            kind="earthquake", severity=severity, source="USGS",
            description=f"M{magnitude} earthquake, {props.get('place', 'nearby')}",
            issued_at=datetime.fromtimestamp(props["time"] / 1000, UTC), url=props.get("url"),
        )


class GdacsEvents:
    """Cyclones, floods, volcanoes and wildfires from GDACS (GeoJSON event list, no API key)."""

    name = "GDACS"
    _URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    _RADIUS_KM = 500
    _LOOKBACK_DAYS = 7
    _KINDS = {"TC": "tropical cyclone", "FL": "flood", "VO": "volcano", "WF": "wildfire"}

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self, geo: GeoPoint) -> list[Alert]:
        today = datetime.now(UTC).date()
        params = {
            "eventlist": ";".join(self._KINDS),
            "fromdate": (today - timedelta(days=self._LOOKBACK_DAYS)).isoformat(),
            "todate": today.isoformat(),
        }
        data = await request_json(self._http, "GET", self._URL, params=params)
        alerts = (self._to_alert(feature, geo) for feature in data.get("features", []))
        return [alert for alert in alerts if alert is not None]

    def _to_alert(self, feature: dict[str, Any], geo: GeoPoint) -> Alert | None:
        props, geometry = feature.get("properties", {}), feature.get("geometry", {})
        level = str(props.get("alertlevel", "")).lower()
        if geometry.get("type") != "Point" or level not in ("green", "orange", "red"):
            return None
        lon, lat = geometry["coordinates"][:2]
        if haversine_km(geo.latitude, geo.longitude, lat, lon) > self._RADIUS_KM:
            return None
        kind = self._KINDS.get(props.get("eventtype"), "hazard")
        return Alert(
            kind=kind, severity=level, source="GDACS",  # type: ignore[arg-type]
            description=f"{level.title()} alert {kind}: {props.get('name') or props.get('description', '')}".strip(),
            issued_at=_parse_datetime(props.get("fromdate")), url=(props.get("url") or {}).get("report"),
        )


class GooglePlaces:
    _URL = "https://places.googleapis.com/v1/places:searchText"
    _FIELDS = "places.id,places.displayName,places.rating,places.userRatingCount,places.location,places.formattedAddress"
    _BIAS_RADIUS_M = 30000.0

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._headers = {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": self._FIELDS}

    async def search(self, query: str, geo: GeoPoint) -> list[Place]:
        body = {
            "textQuery": query,
            "maxResultCount": 20,
            "locationBias": {
                "circle": {"center": {"latitude": geo.latitude, "longitude": geo.longitude}, "radius": self._BIAS_RADIUS_M}
            },
        }
        data = await request_json(self._http, "POST", self._URL, json=body, headers=self._headers)
        return [
            Place(
                id=raw["id"], name=raw["displayName"]["text"], rating=raw["rating"],
                review_count=raw.get("userRatingCount", 0),
                latitude=raw["location"]["latitude"], longitude=raw["location"]["longitude"],
                address=raw.get("formattedAddress", ""),
            )
            for raw in data.get("places", [])
            if "rating" in raw
        ]


class SandboxBookingProvider:
    """Testing-phase booker: records the reservation, moves no money. Replace with a real provider later."""

    async def reserve(self, item: BookingItem) -> Reservation:
        return Reservation(item=item, reference=f"SANDBOX-{uuid4().hex[:8].upper()}", status="sandbox-held")
