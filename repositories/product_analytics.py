"""Read-model for the District Administrator product analytics page."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import statistics
from typing import Any

from services.analytics import (
    backend_name,
    fetch_all,
    is_persistent_backend,
)


FUNNEL = (
    ("login_success", "Logged in"),
    ("plc_cycle_viewed", "Viewed PLC Cycle"),
    ("cfa_assigned", "Assigned CFA"),
    ("cfa_submitted", "Submitted CFA"),
    ("instructional_response_saved", "Saved Response"),
    ("post_cfa_created", "Created POST CFA"),
)


def _parse_metadata(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _cutoff(days: int | None) -> datetime | None:
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=int(days))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def get_product_analytics(days: int | None = 30) -> dict[str, Any]:
    cutoff = _cutoff(days)

    if cutoff is None:
        sessions = fetch_all(
            """
            SELECT *
            FROM analytics_sessions
            ORDER BY last_seen_at DESC
            """
        )
        events = fetch_all(
            """
            SELECT *
            FROM analytics_events
            ORDER BY created_at DESC
            """
        )
    else:
        sessions = fetch_all(
            """
            SELECT *
            FROM analytics_sessions
            WHERE last_seen_at >= ?
            ORDER BY last_seen_at DESC
            """,
            (cutoff,),
        )
        events = fetch_all(
            """
            SELECT *
            FROM analytics_events
            WHERE created_at >= ?
            ORDER BY created_at DESC
            """,
            (cutoff,),
        )

    session_ids = {str(row["session_id"]) for row in sessions}
    events = [
        row
        for row in events
        if str(row["session_id"]) in session_ids
    ]

    named_testers = {
        (str(row.get("tester_email") or "").lower() or str(row.get("tester_name") or "").strip().lower())
        for row in sessions
        if row.get("tester_name") or row.get("tester_email")
    }
    named_testers.discard("")

    page_views = Counter(
        str(row.get("page_name") or "Unknown")
        for row in events
        if row.get("event_type") == "page_view"
    )

    errors = [
        {**row, "metadata": _parse_metadata(row.get("metadata_json"))}
        for row in events
        if row.get("event_type") == "error"
    ]

    performance_events = [
        {**row, "metadata": _parse_metadata(row.get("metadata_json"))}
        for row in events
        if row.get("event_type") == "performance"
        and row.get("duration_ms") is not None
    ]

    duration_values = [
        float(row["duration_ms"])
        for row in performance_events
    ]

    by_operation: dict[str, list[float]] = defaultdict(list)
    for row in performance_events:
        operation = str(row["metadata"].get("operation") or "Unknown")
        by_operation[operation].append(float(row["duration_ms"]))

    performance_summary = [
        {
            "Operation": operation,
            "Runs": len(values),
            "Median ms": round(statistics.median(values), 1),
            "P95 ms": round(_percentile(values, 0.95) or 0.0, 1),
            "Max ms": round(max(values), 1),
        }
        for operation, values in by_operation.items()
    ]
    performance_summary.sort(key=lambda row: row["P95 ms"], reverse=True)

    sessions_by_event: dict[str, set[str]] = defaultdict(set)
    for row in events:
        sessions_by_event[str(row["event_type"])].add(str(row["session_id"]))

    funnel = []
    previous_count: int | None = None
    for event_type, label in FUNNEL:
        count = len(sessions_by_event.get(event_type, set()))
        conversion = (
            count / previous_count * 100
            if previous_count
            else None
        )
        funnel.append(
            {
                "Stage": label,
                "Sessions": count,
                "Step conversion": round(conversion, 1) if conversion is not None else None,
            }
        )
        previous_count = count

    completed_sessions = len(
        sessions_by_event.get("instructional_response_saved", set())
    )
    logged_in_sessions = len(
        sessions_by_event.get("login_success", set())
    )

    recent_sessions = [
        {
            "Tester": row.get("tester_name") or "Anonymous",
            "Email": row.get("tester_email") or "",
            "Demo persona": row.get("demo_display_name") or "",
            "Role": row.get("demo_role") or "",
            "Started": row.get("started_at"),
            "Last seen": row.get("last_seen_at"),
            "Timezone": row.get("timezone") or "",
        }
        for row in sessions[:50]
    ]

    recent_errors = []
    for row in errors[:50]:
        meta = row["metadata"]
        recent_errors.append(
            {
                "When": row.get("created_at"),
                "Tester/session": next(
                    (
                        s.get("tester_name") or str(row["session_id"])[:8]
                        for s in sessions
                        if str(s["session_id"]) == str(row["session_id"])
                    ),
                    str(row["session_id"])[:8],
                ),
                "Page": row.get("page_name") or "",
                "Operation": meta.get("operation") or "",
                "Error": meta.get("exception_type") or "",
                "Message": meta.get("message") or "",
                "Duration ms": round(float(row["duration_ms"]), 1) if row.get("duration_ms") is not None else None,
            }
        )

    return {
        "backend": backend_name(),
        "persistent": is_persistent_backend(),
        "kpis": {
            "testers": len(named_testers),
            "sessions": len(sessions),
            "logged_in_sessions": logged_in_sessions,
            "workflow_completions": completed_sessions,
            "workflow_completion_rate": (
                completed_sessions / logged_in_sessions * 100
                if logged_in_sessions
                else None
            ),
            "errors": len(errors),
            "median_operation_ms": (
                statistics.median(duration_values)
                if duration_values
                else None
            ),
            "p95_operation_ms": _percentile(duration_values, 0.95),
        },
        "funnel": funnel,
        "page_views": [
            {"Page": page, "Views": count}
            for page, count in page_views.most_common()
        ],
        "recent_sessions": recent_sessions,
        "performance": performance_summary,
        "errors": recent_errors,
    }
