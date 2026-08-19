"""Team-filter overlay for the existing Dashboard repository.

This deliberately reuses the Dashboard's current private aggregate helpers so
the filter follows whatever CFA model the local dashboard.py currently uses.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import repositories.dashboard as base_dashboard
from services.access_control import get_data_scope
from services.database import connect


def list_dashboard_teams(current_user: dict | None) -> list[dict[str, Any]]:
    scope = get_data_scope(current_user)

    if scope.team_ids is None:
        where = ""
        params: tuple[Any, ...] = ()
    elif not scope.team_ids:
        return []
    else:
        placeholders = ",".join("?" for _ in scope.team_ids)
        where = f"WHERE t.team_id IN ({placeholders})"
        params = tuple(scope.team_ids)

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                t.team_id,
                t.name,
                t.grade_level,
                t.subject,
                COALESCE(s.school_name, t.school_id) AS school_name
            FROM plc_teams AS t
            LEFT JOIN schools AS s
                ON s.school_id = t.school_id
            {where}
            ORDER BY s.school_name, t.grade_level, t.subject, t.name
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def _validate_team(current_user: dict | None, team_id: int) -> dict[str, Any]:
    teams = list_dashboard_teams(current_user)
    selected = next(
        (team for team in teams if int(team["team_id"]) == int(team_id)),
        None,
    )
    if selected is None:
        raise ValueError("That PLC team is outside the current user's access scope.")
    return selected


def get_team_filtered_workspace(
    current_user: dict | None,
    team_id: int,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return the existing dashboard workspace narrowed to one PLC team."""
    selected_team = _validate_team(current_user, team_id)
    today = as_of or date.today()

    # Begin with the base workspace so role-specific people/access-management
    # sections remain intact. Then replace evidence/PLC-work sections.
    workspace = base_dashboard.get_dashboard_workspace(
        current_user,
        as_of=today,
    )

    team_ids = (int(team_id),)
    latest_results = base_dashboard._latest_results_by_student_standard(team_ids)
    cycles = base_dashboard._cycle_rows(team_ids)
    completed_cycles = base_dashboard._completed_cycle_rows(team_ids)
    outcomes = base_dashboard._outcome_summary(latest_results)

    counts = {status: 0 for status in base_dashboard.MASTERY_STATUSES}
    for result in latest_results:
        counts[result["status"]] += 1

    with connect() as connection:
        active_interventions = connection.execute(
            """
            SELECT COUNT(*)
            FROM interventions AS i
            JOIN plc_cycles AS c
                ON c.cycle_id = i.cycle_id
            WHERE i.status = 'Active'
              AND c.team_id = ?
            """,
            (team_id,),
        ).fetchone()[0]

        overdue_interventions = connection.execute(
            """
            SELECT COUNT(*)
            FROM interventions AS i
            JOIN plc_cycles AS c
                ON c.cycle_id = i.cycle_id
            WHERE i.status IN ('Planned', 'Active')
              AND i.end_date IS NOT NULL
              AND i.end_date < ?
              AND c.team_id = ?
            """,
            (today.isoformat(), team_id),
        ).fetchone()[0]

        commitments = connection.execute(
            """
            SELECT
                cm.name,
                cm.action_step,
                cm.due_date,
                cm.status,
                c.name AS cycle_name,
                s.code AS standard,
                COALESCE(u.display_name, 'Unassigned') AS owner
            FROM commitments AS cm
            JOIN plc_cycles AS c
                ON c.cycle_id = cm.cycle_id
            JOIN standards AS s
                ON s.standard_id = c.standard_id
            LEFT JOIN app_users AS u
                ON u.user_id = cm.assigned_user_id
            WHERE c.team_id = ?
            ORDER BY
                CASE cm.status WHEN 'Open' THEN 0 ELSE 1 END,
                cm.due_date
            LIMIT 6
            """,
            (team_id,),
        ).fetchall()

        my_open_commitments = 0
        if current_user and current_user.get("role") == "Teacher":
            my_open_commitments = connection.execute(
                """
                SELECT COUNT(*)
                FROM commitments AS cm
                JOIN plc_cycles AS c
                    ON c.cycle_id = cm.cycle_id
                WHERE cm.status = 'Open'
                  AND cm.assigned_user_id = ?
                  AND c.team_id = ?
                """,
                (int(current_user["user_id"]), team_id),
            ).fetchone()[0]

    actions: list[dict[str, str]] = []

    for commitment in commitments:
        if commitment["status"] == "Open" and commitment["due_date"] <= today.isoformat():
            actions.append(
                {
                    "Priority": "Due",
                    "Action": f"Complete: {commitment['name']}",
                    "Context": f"{commitment['cycle_name']} · due {commitment['due_date']}",
                }
            )

    for cycle in cycles:
        if cycle.get("latest_administration_id") is None:
            actions.append(
                {
                    "Priority": "Evidence needed",
                    "Action": f"Enter CFA results: {cycle['cycle_name']}",
                    "Context": f"{cycle['plc']} · {cycle['standard']}",
                }
            )

    workspace["kpis"] = {
        **workspace["kpis"],
        "students_assessed": len(
            {item["student_id"] for item in latest_results}
        ),
        "evidence_records": outcomes["assessed"],
        "average": outcomes["average"],
        "mastery_rate": outcomes["mastery_rate"],
        "intensive_results": outcomes["intensive"],
        "active_cycles": len(cycles),
        "active_interventions": int(active_interventions),
        "overdue_interventions": int(overdue_interventions),
        "my_open_commitments": int(my_open_commitments),
    }
    workspace["mastery_counts"] = counts
    workspace["cycles"] = cycles
    workspace["completed_cycles"] = completed_cycles
    workspace["commitments"] = [dict(row) for row in commitments]
    workspace["alerts"] = base_dashboard._alerts(today, team_ids)
    workspace["next_actions"] = actions[:6]
    workspace["scope"] = {
        **workspace["scope"],
        "label": (
            f"{selected_team['name']} · {selected_team['grade_level']} "
            f"{selected_team['subject']} · {selected_team['school_name']}"
        ),
    }
    workspace["selected_team"] = selected_team

    return workspace