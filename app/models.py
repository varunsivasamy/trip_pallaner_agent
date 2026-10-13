"""Domain models and the shared TripState that every agent reads and writes."""

from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field

Severity = Literal["green", "orange", "red"]
RiskLevel = Literal["low", "moderate", "high"]
Recommendation = Literal["proceed", "proceed_flexible", "avoid"]

_RECOMMENDATION_BY_LEVEL: dict[RiskLevel, Recommendation] = {
    "low": "proceed",
    "moderate": "proceed_flexible",
    "high": "avoid",
}
_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class Stage(StrEnum):
    INTAKE = "intake"
    LOCATE = "locate"
    ASSESS = "assess"
    SEARCH = "search"
    ITINERARY = "itinerary"
    REVIEW = "review"
    BOOKING = "booking"
    PRESENT = "present"
    DONE = "done"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class TripRequest(BaseModel):
    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=30)
    travelers: int = Field(default=1, ge=1)
    budget_total: float | None = Field(default=None, gt=0)
    currency: str = "INR"
    interests: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    booking_requested: bool = False
    booking_confirmed: bool = False

    @property
    def end_date(self) -> date | None:
        if self.start_date is None or self.duration_days is None:
            return None
        return self.start_date + timedelta(days=self.duration_days - 1)


class GeoPoint(BaseModel):
    name: str
    country: str
    latitude: float
    longitude: float


class Alert(BaseModel):
    kind: str
    severity: Severity
    source: str
    description: str
    issued_at: datetime | None = None
    url: str | None = None


class SafetyAdvice(BaseModel):
    summary: str
    precautions: list[str] = Field(default_factory=list, max_length=4)
    alternatives: list[str] = Field(default_factory=list, max_length=3)


class SafetyReport(SafetyAdvice):
    risk_score: int = Field(ge=0, le=100)
    level: RiskLevel
    alerts: list[Alert]
    unverified_sources: list[str]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recommendation(self) -> Recommendation:
        return _RECOMMENDATION_BY_LEVEL[self.level]


class DailyWeather(BaseModel):
    date: date
    summary: str
    temp_min: float | None = None
    temp_max: float | None = None
    precip_probability: int | None = None
    wind_max_kmh: float | None = None


class Place(BaseModel):
    id: str
    name: str
    rating: float
    review_count: int
    latitude: float
    longitude: float
    address: str = ""


class Stop(BaseModel):
    place_id: str
    start_time: str = Field(pattern=_TIME_PATTERN)
    duration_minutes: int = Field(ge=15, le=240)
    note: str = ""


class DayPlan(BaseModel):
    day: int = Field(ge=1)
    theme: str
    stops: list[Stop]


class ItineraryPlan(BaseModel):
    days: list[DayPlan]


class CritiqueResult(BaseModel):
    issues: list[str] = Field(default_factory=list)
    fix_instructions: list[str] = Field(default_factory=list)


class BookingItem(BaseModel):
    kind: Literal["transport", "lodging"]
    description: str
    travel_date: date
    refundable_only: bool


class StepTrace(BaseModel):
    stage: Stage
    timestamp: datetime = Field(default_factory=datetime.now)
    status: str = "success"
    detail: str = ""


class Reservation(BaseModel):
    item: BookingItem
    reference: str
    status: str


class TripState(BaseModel):
    trip_id: str = Field(default_factory=lambda: uuid4().hex)
    stage: Stage = Stage.INTAKE
    user_messages: list[str] = Field(default_factory=list)
    request: TripRequest = Field(default_factory=TripRequest)
    geo: GeoPoint | None = None
    safety: SafetyReport | None = None
    weather: list[DailyWeather] = Field(default_factory=list)
    places: list[Place] = Field(default_factory=list)
    itinerary: ItineraryPlan | None = None
    review_issues: list[str] = Field(default_factory=list)
    fix_instructions: list[str] = Field(default_factory=list)
    review_rounds: int = 0
    reservations: list[Reservation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    execution_trace: list[StepTrace] = Field(default_factory=list)
    question: str | None = None
    reply: str | None = None
    error: str | None = None

    @property
    def blocked_by_safety(self) -> bool:
        return self.safety is not None and self.safety.recommendation == "avoid"

    def confirm_booking(self) -> None:
        """Confirm booking in human-in-the-loop flow."""
        self.request.booking_confirmed = True
        self.stage = Stage.BOOKING
        self.question = None

    def cancel_booking(self) -> None:
        """Decline booking in human-in-the-loop flow; presents plan without booking."""
        self.request.booking_requested = False
        self.stage = Stage.PRESENT
        self.question = None

    def add_user_message(self, text: str) -> None:
        """Record a traveler message. If confirming or declining a pending booking, update stage accordingly; otherwise restart planning."""
        self.user_messages.append(text)

        # Check if awaiting booking confirmation in human-in-the-loop flow
        if (
            self.stage == Stage.NEEDS_INPUT
            and self.itinerary is not None
            and self.request.booking_requested
            and not self.request.booking_confirmed
        ):
            lower = text.strip().lower()
            affirmations = {"yes", "y", "confirm", "confirmed", "book", "book it", "proceed", "go ahead", "sure", "ok", "okay", "yes please", "do it"}
            rejections = {"no", "n", "cancel", "don't book", "dont book", "no thanks", "skip"}

            if lower in affirmations or any(word in lower for word in ["confirm", "proceed", "go ahead", "book it"]):
                self.confirm_booking()
                return
            elif lower in rejections or any(word in lower for word in ["don't book", "dont book", "cancel booking"]):
                self.cancel_booking()
                return

        self.stage = Stage.INTAKE
        self.question = self.error = self.reply = None
        self.geo = self.safety = self.itinerary = None
        self.weather, self.places, self.review_issues, self.warnings = [], [], [], []
        self.execution_trace = []
        self.review_rounds = 0

    def ask(self, question: str) -> None:
        self.question = question
        self.stage = Stage.NEEDS_INPUT

    def fail(self, message: str) -> None:
        self.error = message
        self.stage = Stage.FAILED

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def require_geo(self) -> GeoPoint:
        if self.geo is None:
            raise RuntimeError("The destination has not been located yet.")
        return self.geo

    def require_days(self) -> int:
        if self.request.duration_days is None:
            raise RuntimeError("The trip length is unknown.")
        return self.request.duration_days

    def presentation_view(self) -> dict[str, Any]:
        """Compact, self-contained snapshot for the presenter (stops carry place details)."""
        by_id = {place.id: place for place in self.places}
        days = [
            {
                "day": day.day,
                "theme": day.theme,
                "stops": [
                    {
                        **stop.model_dump(),
                        "name": by_id[stop.place_id].name,
                        "rating": by_id[stop.place_id].rating,
                        "review_count": by_id[stop.place_id].review_count,
                    }
                    for stop in day.stops
                ],
            }
            for day in (self.itinerary.days if self.itinerary else [])
        ]
        view = self.model_dump(
            mode="json", include={"request", "safety", "weather", "reservations", "warnings"}
        )
        return {**view, "itinerary_days": days}
