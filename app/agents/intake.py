import json

from ..errors import NeedsInput
from ..llm import LLM
from ..models import TripRequest, TripState
from ..prompts import INTAKE_PROMPT
from .base import Clock

_REQUIRED = {"destination": "destination", "duration_days": "trip length in days"}
_REQUIRED_FOR_BOOKING = {"origin": "departure city", "start_date": "start date"}


def _missing_fields(request: TripRequest) -> list[str]:
    required = dict(_REQUIRED)
    if request.booking_requested:
        required.update(_REQUIRED_FOR_BOOKING)
    return [label for field, label in required.items() if not getattr(request, field)]


class IntakeAgent:
    name = "intake"

    def __init__(self, llm: LLM, today: Clock) -> None:
        self._llm = llm
        self._today = today

    async def run(self, state: TripState) -> None:
        today = self._today()
        payload = {"today": today.isoformat(), "weekday": today.strftime("%A"), "messages": state.user_messages}
        state.request = await self._llm.structured(
            INTAKE_PROMPT,
            json.dumps(payload),
            TripRequest,
            check=lambda request: self._reject_past_date(request, today),
        )
        missing = _missing_fields(state.request)
        if missing:
            raise NeedsInput(f"To continue I still need: {', '.join(missing)}.")

    @staticmethod
    def _reject_past_date(request: TripRequest, today) -> None:
        if request.start_date is not None and request.start_date < today:
            raise ValueError(f"start_date {request.start_date} is in the past; today is {today}.")


ParserAgent = IntakeAgent

