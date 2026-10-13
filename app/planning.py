"""Pure planning logic: scoring, clustering, feasibility rules. No I/O, no LLM."""

import math
from collections.abc import Sequence

from .models import Alert, DayPlan, ItineraryPlan, Place, RiskLevel

GLOBAL_MEAN_RATING = 4.2
CONFIDENCE_REVIEWS = 200

FULL_DAY_STOPS = 4
EDGE_DAY_STOPS = 2
MAX_STOPS_PER_DAY = FULL_DAY_STOPS
MAX_BUSY_MINUTES = 600
LATEST_END_MINUTES = 22 * 60
CITY_SPEED_KMH = 20
TRAVEL_OVERHEAD_MINUTES = 10

SEVERITY_WEIGHTS = {"green": 10, "orange": 35, "red": 60}
MODERATE_FROM = 30
HIGH_FROM = 60

_EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat, d_lon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def distance_km(a: Place, b: Place) -> float:
    return haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)


def place_score(place: Place) -> float:
    """Bayesian-weighted rating: a 5.0 from 12 reviews must not beat a 4.7 from 4,000."""
    weight = place.review_count / (place.review_count + CONFIDENCE_REVIEWS)
    return weight * place.rating + (1 - weight) * GLOBAL_MEAN_RATING


def day_capacities(days: int) -> list[int]:
    """Stops per day: arrival and departure days of longer trips are half-days."""
    if days <= 2:
        return [FULL_DAY_STOPS - 1] * days
    return [EDGE_DAY_STOPS, *[FULL_DAY_STOPS] * (days - 2), EDGE_DAY_STOPS]


def cluster_by_day(places: Sequence[Place], capacities: Sequence[int]) -> list[list[Place]]:
    """Seed each day with the best remaining place, then fill with its nearest neighbours."""
    remaining = sorted(places, key=place_score, reverse=True)[: 2 * sum(capacities)]
    clusters: list[list[Place]] = []
    for capacity in capacities:
        if not remaining:
            break
        seed, rest = remaining[0], remaining[1:]
        neighbours = sorted(rest, key=lambda place: distance_km(seed, place))[: capacity - 1]
        clusters.append([seed, *neighbours])
        taken = {place.id for place in clusters[-1]}
        remaining = [place for place in remaining if place.id not in taken]
    return clusters


def estimate_travel_minutes(a: Place, b: Place) -> int:
    return round(distance_km(a, b) / CITY_SPEED_KMH * 60) + TRAVEL_OVERHEAD_MINUTES


def to_minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def review_day(day: DayPlan, places: dict[str, Place]) -> list[str]:
    """Feasibility rules for one day. Returns human-readable issues (empty means OK)."""
    label = f"Day {day.day}"
    issues: list[str] = []
    if len(day.stops) > MAX_STOPS_PER_DAY:
        issues.append(f"{label}: {len(day.stops)} stops, the maximum is {MAX_STOPS_PER_DAY}.")
    busy, previous_end, previous_place = 0, None, None
    for stop in day.stops:
        place = places.get(stop.place_id)
        if not place:
            issues.append(f"{label}: Unknown place_id {stop.place_id}")
            continue
        start = to_minutes(stop.start_time)
        if previous_place is not None and previous_end is not None:
            travel = estimate_travel_minutes(previous_place, place)
            busy += travel
            if start < previous_end + travel:
                issues.append(f"{label}: {place.name} starts too soon; the previous stop needs {travel} min of travel.")
        busy += stop.duration_minutes
        previous_end, previous_place = start + stop.duration_minutes, place
        if previous_end > LATEST_END_MINUTES:
            issues.append(f"{label}: {place.name} ends after 10 PM (22:00).")
    if busy > MAX_BUSY_MINUTES:
        issues.append(f"{label}: {busy} busy minutes, the maximum is {MAX_BUSY_MINUTES}.")
    return issues


def review_itinerary(
    itinerary: ItineraryPlan, places: dict[str, Place], budget_total: float | None = None
) -> tuple[list[str], list[str]]:
    """Reviews an entire itinerary plan for day-wise timing, duplicates, and budget limits.
    Returns structured (issues, fix_instructions).
    """
    issues: list[str] = []
    fix_instructions: list[str] = []

    # 1. Check individual days for timing/capacity issues
    for day in itinerary.days:
        day_issues = review_day(day, places)
        for issue in day_issues:
            issues.append(issue)
            if "ends after 10 PM" in issue or "ends after 22:00" in issue:
                fix_instructions.append(f"Remove final stop on Day {day.day}")
            elif "starts too soon" in issue:
                fix_instructions.append(f"Adjust start time or remove overlapping stop on Day {day.day}")
            elif "busy minutes" in issue or "maximum is" in issue:
                fix_instructions.append(f"Reduce number of stops on Day {day.day}")

    # 2. Check for duplicate places across the entire trip
    all_place_ids = [stop.place_id for day in itinerary.days for stop in day.stops]
    seen = set()
    has_duplicate = False
    for pid in all_place_ids:
        if pid in seen:
            has_duplicate = True
            break
        seen.add(pid)

    if has_duplicate:
        issues.append("Same place appears twice")
        fix_instructions.append("Remove duplicate place")

    # 3. Check for budget constraints
    if budget_total is not None and budget_total > 0:
        estimated_cost = len(all_place_ids) * 500
        if estimated_cost > budget_total:
            issues.append("Budget exceeded")
            fix_instructions.append("Choose cheaper options")

    return issues, fix_instructions


def assess_risk(alerts: Sequence[Alert], all_sources_failed: bool) -> tuple[int, RiskLevel]:
    score = min(100, sum(SEVERITY_WEIGHTS[alert.severity] for alert in alerts))
    if all_sources_failed:
        score = max(score, MODERATE_FROM)  # never treat "could not check" as "safe"
    level: RiskLevel = "high" if score >= HIGH_FROM else "moderate" if score >= MODERATE_FROM else "low"
    return score, level
