from app.models import Alert, DayPlan, Place, Stop
from app.planning import (
    assess_risk,
    cluster_by_day,
    day_capacities,
    place_score,
    review_day,
)


def place(pid, rating, reviews, lat=10.0, lon=77.0):
    return Place(id=pid, name=pid, rating=rating, review_count=reviews, latitude=lat, longitude=lon)


def test_few_reviews_do_not_beat_many():
    assert place_score(place("a", 5.0, 12)) < place_score(place("b", 4.7, 4000))


def test_day_capacities():
    assert day_capacities(1) == [3]
    assert day_capacities(2) == [3, 3]
    assert day_capacities(4) == [2, 4, 4, 2]


def test_clusters_group_nearby_places_and_never_repeat():
    near = [place(f"n{i}", 4.5, 500, lat=10.0 + 0.001 * i) for i in range(3)]
    far = [place(f"f{i}", 4.4, 500, lat=12.0 + 0.001 * i) for i in range(3)]
    clusters = cluster_by_day(near + far, [3, 3])
    assert {p.id for p in clusters[0]} != {p.id for p in clusters[1]}
    assert all(len({p.id[0] for p in c}) == 1 for c in clusters)
    assert len({p.id for c in clusters for p in c}) == 6


def test_risk_levels_and_unverified_floor():
    red = Alert(kind="x", severity="red", source="s", description="d")
    assert assess_risk([], all_sources_failed=False) == (0, "low")
    assert assess_risk([red], all_sources_failed=False) == (60, "high")
    assert assess_risk([], all_sources_failed=True)[1] == "moderate"


def test_review_flags_overlap_and_overpacked_days():
    places = {p.id: p for p in [place("a", 4, 1), place("b", 4, 1, lat=10.2)]}
    overlap = DayPlan(day=1, theme="t", stops=[
        Stop(place_id="a", start_time="09:00", duration_minutes=60),
        Stop(place_id="b", start_time="10:00", duration_minutes=60),
    ])
    assert any("too soon" in issue for issue in review_day(overlap, places))
    ok = DayPlan(day=1, theme="t", stops=[Stop(place_id="a", start_time="09:00", duration_minutes=60)])
    assert review_day(ok, places) == []
