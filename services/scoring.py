from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class MasteryThresholds:
    mastered: float = 80.0
    approaching: float = 70.0
    developing: float = 50.0

    def __post_init__(self) -> None:
        if not 0 <= self.developing <= self.approaching <= self.mastered <= 100:
            raise ValueError("Thresholds must be ordered between 0 and 100.")


def classify_score(percent: float | None, thresholds: MasteryThresholds | None = None) -> str:
    if percent is None:
        return "Incomplete"
    if not 0 <= percent <= 100:
        raise ValueError("Percent must be between 0 and 100.")
    t = thresholds or MasteryThresholds()
    if percent >= t.mastered:
        return "Mastered"
    if percent >= t.approaching:
        return "Approaching"
    if percent >= t.developing:
        return "Developing"
    return "Intensive"


def calculate_student_result(
    scores: Mapping[str, float | None],
    possible_points: Mapping[str, float],
    thresholds: MasteryThresholds | None = None,
) -> dict:
    unknown = set(scores) - set(possible_points)
    if unknown:
        raise ValueError(f"Unknown question(s): {', '.join(sorted(unknown))}")
    for question, possible in possible_points.items():
        if possible <= 0:
            raise ValueError(f"Possible points for {question} must be positive.")
        earned = scores.get(question)
        if earned is not None and not 0 <= earned <= possible:
            raise ValueError(f"Score for {question} must be between 0 and {possible}.")

    complete = all(scores.get(question) is not None for question in possible_points)
    if not complete:
        return {"earned": None, "possible": sum(possible_points.values()), "percent": None, "status": "Incomplete"}

    earned = float(sum(scores[question] for question in possible_points))
    possible = float(sum(possible_points.values()))
    percent = earned / possible * 100
    return {
        "earned": earned,
        "possible": possible,
        "percent": percent,
        "status": classify_score(percent, thresholds),
    }


def summarize_results(results: Iterable[Mapping]) -> dict:
    rows = list(results)
    completed = [row for row in rows if row.get("percent") is not None]
    counts = {name: 0 for name in ("Mastered", "Approaching", "Developing", "Intensive", "Incomplete")}
    for row in rows:
        counts[row.get("status", "Incomplete")] += 1
    average = sum(row["percent"] for row in completed) / len(completed) if completed else None
    return {
        "students": len(rows),
        "completed": len(completed),
        "completion_rate": len(completed) / len(rows) * 100 if rows else 0.0,
        "average": average,
        "counts": counts,
    }


def percentage_point_growth(pre_percent: float | None, post_percent: float | None) -> float | None:
    if pre_percent is None or post_percent is None:
        return None
    return post_percent - pre_percent

