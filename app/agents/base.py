from collections.abc import Callable
from datetime import date
from typing import Protocol

from ..models import TripState

Clock = Callable[[], date]


class Agent(Protocol):
    name: str

    async def run(self, state: TripState) -> None:
        """Read what it needs from `state`, write its own output back into `state`."""
        ...
