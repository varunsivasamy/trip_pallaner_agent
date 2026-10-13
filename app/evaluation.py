"""Comprehensive evaluation framework for LLM-based trip planning applications.

Inspired by the llm-evaluation skill: integrates automated quantitative metrics
(Constraint Satisfaction, Interest Coverage, Safety Adherence, Coherence)
and optional LLM-as-a-Judge scoring.
"""

from typing import Any
from pydantic import BaseModel, Field

from .llm import LLM
from .models import StepTrace, TripState
from .planning import review_day


class MetricResult(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    details: str


class JudgeDimension(BaseModel):
    dimension: str
    rating: int = Field(ge=1, le=5)
    reasoning: str


class JudgeEvaluation(BaseModel):
    dimensions: list[JudgeDimension]
    overall_quality_score: float = Field(ge=0.0, le=1.0)
    summary: str


class EvaluationReport(BaseModel):
    trip_id: str
    overall_score: float = Field(ge=0.0, le=1.0)
    automated_metrics: list[MetricResult]
    judge_evaluation: JudgeEvaluation | None = None
    execution_steps: int = 0
    issues: list[str] = Field(default_factory=list)


def evaluate_constraint_satisfaction(state: TripState) -> MetricResult:
    """Evaluates timing feasibility, stop limits, and day schedules using review_day."""
    if not state.itinerary or not state.itinerary.days:
        return MetricResult(
            name="Constraint Satisfaction",
            score=0.0 if state.stage == "done" else 1.0,
            passed=state.stage != "done",
            details="No itinerary generated to evaluate.",
        )

    places_by_id = {p.id: p for p in state.places}
    all_issues: list[str] = []
    total_days = len(state.itinerary.days)
    failed_days = 0

    for day in state.itinerary.days:
        issues = review_day(day, places_by_id)
        if issues:
            failed_days += 1
            all_issues.extend(issues)

    pass_ratio = 1.0 - (failed_days / total_days) if total_days > 0 else 1.0
    return MetricResult(
        name="Constraint Satisfaction",
        score=round(pass_ratio, 2),
        passed=failed_days == 0,
        details="All day schedules meet feasibility constraints." if not all_issues else f"Issues: {'; '.join(all_issues)}",
    )


def evaluate_interest_coverage(state: TripState) -> MetricResult:
    """Evaluates coverage of user interests in the planned itinerary."""
    interests = state.request.interests
    if not interests:
        return MetricResult(
            name="Interest Coverage",
            score=1.0,
            passed=True,
            details="No specific interests specified by traveler.",
        )

    if not state.itinerary or not state.itinerary.days:
        return MetricResult(
            name="Interest Coverage",
            score=0.0,
            passed=False,
            details="No itinerary available to evaluate interests.",
        )

    all_text = " ".join([
        f"{day.theme} " + " ".join([stop.note for stop in day.stops])
        for day in state.itinerary.days
    ]).lower()

    matched = [interest for interest in interests if interest.lower() in all_text]
    score = len(matched) / len(interests)

    return MetricResult(
        name="Interest Coverage",
        score=round(score, 2),
        passed=score >= 0.5,
        details=f"Matched {len(matched)} of {len(interests)} interests: {', '.join(matched)}",
    )


def evaluate_safety_adherence(state: TripState) -> MetricResult:
    """Evaluates safety compliance and safety gate execution."""
    if not state.safety:
        return MetricResult(
            name="Safety Adherence",
            score=1.0,
            passed=True,
            details="No safety report generated.",
        )

    if state.blocked_by_safety:
        passed = state.stage in ["present", "done"] and state.itinerary is None
        return MetricResult(
            name="Safety Adherence",
            score=1.0 if passed else 0.0,
            passed=passed,
            details="Trip correctly blocked by safety advisory." if passed else "Trip proceeded despite high safety risk!",
        )

    return MetricResult(
        name="Safety Adherence",
        score=1.0,
        passed=True,
        details=f"Safety risk level: {state.safety.level}, recommendation: {state.safety.recommendation}",
    )


def evaluate_coherence(state: TripState) -> MetricResult:
    """Evaluates logical flow, non-empty day plans, and place uniqueness."""
    if not state.itinerary or not state.itinerary.days:
        return MetricResult(
            name="Coherence",
            score=1.0 if state.blocked_by_safety else 0.0,
            passed=state.blocked_by_safety,
            details="No itinerary present.",
        )

    expected_days = list(range(1, len(state.itinerary.days) + 1))
    actual_days = [day.day for day in state.itinerary.days]
    place_ids = [stop.place_id for day in state.itinerary.days for stop in day.stops]
    unique_places = len(set(place_ids)) == len(place_ids)

    days_ok = expected_days == actual_days
    passed = days_ok and unique_places
    score = (1.0 if days_ok else 0.5) * (1.0 if unique_places else 0.5)

    return MetricResult(
        name="Coherence",
        score=round(score, 2),
        passed=passed,
        details="Sequential day numbering and unique place visits verified." if passed else "Day sequence or duplicate place issues detected.",
    )


JUDGE_SYSTEM_PROMPT = """You are an expert AI Evaluator assessing the quality of a generated trip itinerary.
Rate the plan on a 1-5 scale across 5 dimensions:
1. Accuracy: Are details consistent with the location and weather?
2. Coherence: Is the schedule logically ordered and reasonable?
3. Relevance: Does the plan fulfill traveler interests and constraints?
4. Fluency: Is the presentation clear and professional?
5. Safety: Are risks properly communicated and managed?

Provide your rating and concise reasoning for each dimension, an overall quality score between 0.0 and 1.0, and a summary.
"""


async def run_llm_judge(state: TripState, llm: LLM) -> JudgeEvaluation:
    """LLM-as-a-Judge evaluator using structured LLM evaluation standard."""
    view = state.presentation_view()
    user_prompt = f"Trip State:\n{view}"
    return await llm.structured(
        JUDGE_SYSTEM_PROMPT,
        user_prompt,
        JudgeEvaluation,
    )


async def evaluate_trip(state: TripState, llm: LLM | None = None) -> EvaluationReport:
    """Runs automated evaluation metrics and optional LLM judge assessment."""
    metrics = [
        evaluate_constraint_satisfaction(state),
        evaluate_interest_coverage(state),
        evaluate_safety_adherence(state),
        evaluate_coherence(state),
    ]

    automated_score = sum(m.score for m in metrics) / len(metrics)
    judge_eval: JudgeEvaluation | None = None

    if llm is not None and state.itinerary:
        try:
            judge_eval = await run_llm_judge(state, llm)
            overall_score = round(0.5 * automated_score + 0.5 * judge_eval.overall_quality_score, 2)
        except Exception:
            overall_score = round(automated_score, 2)
    else:
        overall_score = round(automated_score, 2)

    issues = [m.details for m in metrics if not m.passed]

    return EvaluationReport(
        trip_id=state.trip_id,
        overall_score=overall_score,
        automated_metrics=metrics,
        judge_evaluation=judge_eval,
        execution_steps=len(state.execution_trace),
        issues=issues,
    )
