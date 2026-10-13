import asyncio
import json
from collections.abc import Sequence

from ..errors import RetryableError
from ..llm import LLM
from ..models import Alert, RiskLevel, SafetyAdvice, SafetyReport, TripState
from ..planning import assess_risk
from ..ports import HazardSource
from ..prompts import SAFETY_ADVISOR_PROMPT


class SafetyAgent:
    name = "safety"

    def __init__(self, sources: Sequence[HazardSource], llm: LLM) -> None:
        self._sources = sources
        self._llm = llm

    async def run(self, state: TripState) -> None:
        geo = state.require_geo()
        results = await asyncio.gather(*(source.fetch(geo) for source in self._sources), return_exceptions=True)
        alerts: list[Alert] = []
        unverified: list[str] = []
        for source, result in zip(self._sources, results, strict=True):
            if isinstance(result, RetryableError):
                unverified.append(source.name)
            elif isinstance(result, BaseException):
                raise result
            else:
                alerts.extend(result)
        score, level = assess_risk(alerts, all_sources_failed=len(unverified) == len(self._sources))
        advice = await self._advise(state, alerts, level, unverified)
        state.safety = SafetyReport(
            risk_score=score, level=level, alerts=alerts, unverified_sources=unverified, **advice.model_dump()
        )
        if unverified:
            state.warn(f"Hazard sources unreachable: {', '.join(unverified)}. The safety check is incomplete.")

    async def _advise(
        self, state: TripState, alerts: list[Alert], level: RiskLevel, unverified: list[str]
    ) -> SafetyAdvice:
        if not alerts:
            return SafetyAdvice(
                summary="Safety could not be fully verified."
                if unverified
                else "No active alerts found in the checked sources."
            )
        geo = state.require_geo()
        payload = {
            "destination": geo.name,
            "country": geo.country,
            **state.request.model_dump(mode="json", include={"start_date", "duration_days"}),
            "risk_level": level,
            "alerts": [a.model_dump(mode="json", include={"kind", "severity", "source", "description", "issued_at"}) for a in alerts],
        }
        try:
            return await self._llm.structured(SAFETY_ADVISOR_PROMPT, json.dumps(payload), SafetyAdvice)
        except RetryableError:  # the score is already computed; only the wording degrades
            return SafetyAdvice(summary=f"{len(alerts)} active alert(s): " + "; ".join(a.description for a in alerts[:3]))
