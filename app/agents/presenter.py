import json

from ..llm import LLM
from ..models import TripState
from ..prompts import PRESENTER_PROMPT


class PresenterAgent:
    name = "presenter"

    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def run(self, state: TripState) -> None:
        state.reply = await self._llm.text(PRESENTER_PROMPT, json.dumps(state.presentation_view()))
