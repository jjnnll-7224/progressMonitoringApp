"""Read-only aggregate queries for the PLC Intelligence Dashboard.

The dashboard is intentionally derived from the same submitted item scores
used by CFA Results and Standards.  Nothing here stores a second copy of a
student's mastery status, so score corrections appear everywhere at once.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from services.database import connect
from services.scoring import classify_score


MASTERY_STATUSES = ("Mastered", "Approaching", "Developing", "Intensive")

def _scope_filter(
    column: str,
    team_ids: tuple[int, ...] | None,
) -> tuple[str, tuple[int, ...]]:
    """Build an optional SQL filter for one or more PLC teams."""
    if team_ids is None:
        return "", ()

    if not team_ids:
        return " AND 1 = 0", ()

    placeholders = ",".join("?" for _ in team_ids)

    return (
        f" AND {column} IN ({placeholders})",
        team_ids,
    )

def _student_standard_results(
    team_ids: tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Calculate submitted evidence per student + measured standard."""
    scope_filter, scope_params = _scope_filter(
        "c.team_id",
        team_ids,
    )

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                ad.administration_id,
                ad.administered_on,
                ad.administration_type,
                pca.cycle_id,
                a.assessment_id,
                a.name AS assessment_name,
                q.standard_id,
                st.student_id,
                st.last_name || ', ' || st.first_name AS student_name,
                SUM(sc.points_earned) AS earned_points,
                SUM(q.max_points) AS possible_points,
                COUNT(sc.question_id) AS answered_questions,
                (
                    SELECT COUNT(*)
                    FROM assessment_questions AS q2
                    WHERE q2.assessment_id = a.assessment_id
                      AND q2.standard_id = q.standard_id
                ) AS question_count
            FROM assessment_administrations AS ad
            JOIN plc_cycle_assessments AS pca
                ON pca.cycle_assessment_id = ad.cycle_assessment_id
            JOIN assessments AS a
                ON a.assessment_id = pca.assessment_id
            JOIN student_item_scores AS sc
                ON sc.administration_id = ad.administration_id
            JOIN assessment_questions AS q
                ON q.question_id = sc.question_id
            JOIN students AS st
                ON st.student_id = sc.student_id
            JOIN plc_cycles AS c
                ON c.cycle_id = pca.cycle_id
            WHERE ad.status = 'Submitted'
              AND sc.points_earned IS NOT NULL
              AND q.standard_id IS NOT NULL
              {scope_filter}
            GROUP BY
                ad.administration_id,
                ad.administered_on,
                ad.administration_type,
                pca.cycle_id,
                a.assessment_id,
                a.name,
                q.standard_id,
                st.student_id,
                st.last_name,
                st.first_name
            """,
            scope_params,
        ).fetchall()

    results: list[dict[str, Any]] = []

    for row in rows:
        if int(row["answered_questions"]) != int(row["question_count"]):
            continue

        possible = float(row["possible_points"])
        percent = (
            float(row["earned_points"]) / possible * 100
            if possible
            else None
        )

        results.append(
            {
                **dict(row),
                "percent": percent,
                "status": classify_score(percent),
            }
        )

    return results


def _latest_results_by_student_standard() -> list[dict[str, Any]]:
    """Keep only each student's newest submitted result for each standard."""
    latest: dict[tuple[int, int], dict[str, Any]] = {}
    for row in _student_standard_results():
        key = (int(row["student_id"]), int(row["standard_id"]))
        existing = latest.get(key)
        if existing is None or (row["administered_on"], row["administration_id"]) > (
            existing["administered_on"], existing["administration_id"]
        ):
            latest[key] = row
    return list(latest.values())


def _cycle_rows(
    team_ids: tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return active cycles with newest submitted assigned-CFA evidence."""
    scope_filter, scope_params = _scope_filter(
        "c.team_id",
        team_ids,
    )

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                c.cycle_id,
                c.team_id,
                c.name AS cycle_name,
                c.stage,
                c.status,
                c.start_date,
                c.end_date,
                t.name AS plc,
                s.code AS standard,
                (
                    SELECT ad.administration_id
                    FROM assessment_administrations AS ad
                    JOIN plc_cycle_assessments AS pca
                        ON pca.cycle_assessment_id = ad.cycle_assessment_id
                    WHERE pca.cycle_id = c.cycle_id
                      AND ad.status = 'Submitted'
                    ORDER BY
                        ad.administered_on DESC,
                        ad.administration_id DESC
                    LIMIT 1
                ) AS latest_administration_id,
                (
                    SELECT ad.administered_on
                    FROM assessment_administrations AS ad
                    JOIN plc_cycle_assessments AS pca
                        ON pca.cycle_assessment_id = ad.cycle_assessment_id
                    WHERE pca.cycle_id = c.cycle_id
                      AND ad.status = 'Submitted'
                    ORDER BY
                        ad.administered_on DESC,
                        ad.administration_id DESC
                    LIMIT 1
                ) AS latest_assessment_date,
                COALESCE(
                    (
                        SELECT completed_steps
                        FROM cycle_meeting_progress AS mp
                        WHERE mp.cycle_id = c.cycle_id
                    ),
                    0
                ) AS meeting_steps_complete
            FROM plc_cycles AS c
            JOIN plc_teams AS t
                ON t.team_id = c.team_id
            JOIN standards AS s
                ON s.standard_id = c.standard_id
            WHERE c.status != 'Complete'
              {scope_filter}
            ORDER BY c.end_date, c.name
            """,
            scope_params,
        ).fetchall()

    results = _student_standard_results(team_ids)
    by_administration: dict[int, list[dict[str, Any]]] = {}

    for result in results:
        by_administration.setdefault(
            int(result["administration_id"]),
            [],
        ).append(result)

    cycles = []
    for row in rows:
        item = dict(row)
        scores = by_administration.get(
            item["latest_administration_id"],
            [],
        )

        item["students_assessed"] = len(scores)
        item["average"] = (
            sum(score["percent"] for score in scores) / len(scores)
            if scores
            else None
        )
        item["mastery_rate"] = (
            sum(score["status"] == "Mastered" for score in scores)
            / len(scores)
            * 100
            if scores
            else None
        )
        cycles.append(item)

    return cycles


def _alerts(today: date) -> list[dict[str, str]]:
    """Create visible, explainable alerts from existing prototype records."""
    alerts: list[dict[str, str]] = []
    today_text = today.isoformat()
    with connect() as connection:
        overdue_commitments = connection.execute(
            """SELECT COUNT(*) FROM commitments
               WHERE status = 'Open' AND due_date < ?""", (today_text,)
        ).fetchone()[0]
        upcoming_commitments = connection.execute(
            """SELECT COUNT(*) FROM commitments
               WHERE status = 'Open' AND due_date >= ? AND due_date <= date(?, '+3 days')""",
            (today_text, today_text),
        ).fetchone()[0]
        overdue_interventions = connection.execute(
            """SELECT COUNT(*) FROM interventions
               WHERE status IN ('Planned', 'Active')
                 AND end_date IS NOT NULL AND end_date < ?""", (today_text,)
        ).fetchone()[0]

    if overdue_commitments:
        alerts.append({"Priority": "High", "Alert": f"{overdue_commitments} commitment(s) overdue", "Action": "Review commitments"})
    if upcoming_commitments:
        alerts.append({"Priority": "Medium", "Alert": f"{upcoming_commitments} commitment(s) due within 3 days", "Action": "Review commitments"})
    if overdue_interventions:
        alerts.append({"Priority": "High", "Alert": f"{overdue_interventions} intervention(s) past end date", "Action": "Review interventions"})

    intensive = sum(item["status"] == "Intensive" for item in _latest_results_by_student_standard())
    if intensive:
        alerts.append({"Priority": "High", "Alert": f"{intensive} student-standard result(s) need intensive support", "Action": "Open Student Groups"})
    return alerts


def get_dashboard_workspace(as_of: date | None = None) -> dict[str, Any]:
    """Return all display-ready Dashboard data from a single shared source."""
    today = as_of or date.today()
    latest_results = _latest_results_by_student_standard()
    counts = {status: 0 for status in MASTERY_STATUSES}
    for result in latest_results:
        counts[result["status"]] += 1

    with connect() as connection:
        active_interventions = connection.execute(
            "SELECT COUNT(*) FROM interventions WHERE status = 'Active'"
        ).fetchone()[0]
        overdue_interventions = connection.execute(
            """SELECT COUNT(*) FROM interventions
               WHERE status IN ('Planned', 'Active') AND end_date IS NOT NULL AND end_date < ?""",
            (today.isoformat(),),
        ).fetchone()[0]
        commitments = connection.execute(
            """
            SELECT cm.name, cm.action_step, cm.due_date, cm.status,
                   c.name AS cycle_name, s.code AS standard,
                   COALESCE(u.display_name, 'Unassigned') AS owner
            FROM commitments cm
            JOIN plc_cycles c ON c.cycle_id = cm.cycle_id
            JOIN standards s ON s.standard_id = c.standard_id
            LEFT JOIN app_users u ON u.user_id = cm.assigned_user_id
            ORDER BY CASE cm.status WHEN 'Open' THEN 0 ELSE 1 END, cm.due_date
            LIMIT 6
            """
        ).fetchall()

    cycles = _cycle_rows()
    return {
        "as_of": today.isoformat(),
        "kpis": {
            "students_assessed": len({item["student_id"] for item in latest_results}),
            "standards_mastered": counts["Mastered"],
            "active_cycles": len(cycles),
            "active_interventions": int(active_interventions),
            "overdue_interventions": int(overdue_interventions),
        },
        "mastery_counts": counts,
        "cycles": cycles,
        "commitments": [dict(row) for row in commitments],
        "alerts": _alerts(today),
    }