"""Role-aware aggregate queries for the PLC Intelligence Dashboard.

All mastery metrics are derived from submitted question-level CFA evidence.
Access is resolved centrally through services.access_control.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from services.access_control import DataScope, get_data_scope
from services.database import connect
from services.scoring import classify_score


MASTERY_STATUSES = (
    "Mastered",
    "Approaching",
    "Developing",
    "Intensive",
)


def _scope_filter(
    column: str,
    team_ids: tuple[int, ...] | None,
) -> tuple[str, tuple[int, ...]]:
    """Build a parameterized team filter.

    None = unrestricted district-wide access.
    ()   = no team access.
    """
    if team_ids is None:
        return "", ()

    if not team_ids:
        return " AND 1 = 0", ()

    placeholders = ",".join("?" for _ in team_ids)
    return (
        f" AND {column} IN ({placeholders})",
        tuple(team_ids),
    )


def _student_standard_results(
    team_ids: tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Calculate complete submitted evidence per student + measured standard."""
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
                c.team_id,
                a.assessment_id,
                a.name AS assessment_name,
                COALESCE(ci.standard_id, q.standard_id) AS standard_id,
                st.student_id,
                st.last_name || ', ' || st.first_name AS student_name,
                SUM(sc.points_earned) AS earned_points,
                SUM(q.max_points) AS possible_points,
                COUNT(DISTINCT sc.question_id) AS answered_questions,
                (
                    SELECT COUNT(*)
                    FROM assessment_questions AS q2
                    LEFT JOIN standard_core_ideas AS ci2
                        ON ci2.core_idea_id = q2.core_idea_id
                    WHERE q2.assessment_id = a.assessment_id
                      AND COALESCE(ci2.standard_id, q2.standard_id)
                          = COALESCE(ci.standard_id, q.standard_id)
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
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            JOIN students AS st
                ON st.student_id = sc.student_id
            JOIN plc_cycles AS c
                ON c.cycle_id = pca.cycle_id
            WHERE ad.status = 'Submitted'
              AND sc.points_earned IS NOT NULL
              AND COALESCE(ci.standard_id, q.standard_id) IS NOT NULL
              {scope_filter}
            GROUP BY
                ad.administration_id,
                ad.administered_on,
                ad.administration_type,
                pca.cycle_id,
                c.team_id,
                a.assessment_id,
                a.name,
                COALESCE(ci.standard_id, q.standard_id),
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

        if percent is None:
            continue

        results.append(
            {
                **dict(row),
                "percent": percent,
                "status": classify_score(percent),
            }
        )

    return results


def _latest_results_by_student_standard(
    team_ids: tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Keep only each student's newest submitted result for each standard."""
    latest: dict[
        tuple[int, int],
        dict[str, Any],
    ] = {}

    for row in _student_standard_results(team_ids):
        key = (
            int(row["student_id"]),
            int(row["standard_id"]),
        )
        existing = latest.get(key)

        if existing is None or (
            row["administered_on"],
            row["administration_id"],
        ) > (
            existing["administered_on"],
            existing["administration_id"],
        ):
            latest[key] = row

    return list(latest.values())


def _outcome_summary(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize a list of student-standard evidence records."""
    assessed = len(results)

    if not assessed:
        return {
            "assessed": 0,
            "average": None,
            "mastery_rate": None,
            "intensive": 0,
        }

    return {
        "assessed": assessed,
        "average": (
            sum(float(row["percent"]) for row in results)
            / assessed
        ),
        "mastery_rate": (
            sum(
                row["status"] == "Mastered"
                for row in results
            )
            / assessed
            * 100
        ),
        "intensive": sum(
            row["status"] == "Intensive"
            for row in results
        ),
    }


def _cycle_rows(
    team_ids: tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return active PLC cycles with their newest submitted CFA evidence."""
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
                    JOIN plc_cycle_assessments AS pca2
                        ON pca2.cycle_assessment_id =
                           ad.cycle_assessment_id
                    WHERE pca2.cycle_id = c.cycle_id
                      AND ad.status = 'Submitted'
                    ORDER BY
                        ad.administered_on DESC,
                        ad.administration_id DESC
                    LIMIT 1
                ) AS latest_administration_id,
                (
                    SELECT ad.administered_on
                    FROM assessment_administrations AS ad
                    JOIN plc_cycle_assessments AS pca3
                        ON pca3.cycle_assessment_id =
                           ad.cycle_assessment_id
                    WHERE pca3.cycle_id = c.cycle_id
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

    by_administration: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for result in results:
        by_administration[
            int(result["administration_id"])
        ].append(result)

    cycles = []

    for row in rows:
        item = dict(row)
        administration_id = item[
            "latest_administration_id"
        ]

        scores = []
        if administration_id is not None:
            # Bracket access on a defaultdict safely defaults to []
            scores = by_administration[int(administration_id)] 
    
    # Optional Safety Check: Ensure the data itself isn't a None object
            if scores is None:
                scores = []
                

        item["students_assessed"] = len(
            {
                int(score["student_id"])
                for score in scores
            }
        )

        item["average"] = (
            sum(
                float(score["percent"])
                for score in scores
            )
            / len(scores)
            if scores
            else None
        )

        item["mastery_rate"] = (
            sum(
                score["status"] == "Mastered"
                for score in scores
            )
            / len(scores)
            * 100
            if scores
            else None
        )

        cycles.append(item)

    return cycles


def _completed_cycle_rows(
    team_ids: tuple[int, ...] | None = None,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return recent completed cycles with their final submitted evidence."""
    scope_filter, scope_params = _scope_filter("c.team_id", team_ids)

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                c.cycle_id,
                c.team_id,
                c.name AS cycle_name,
                c.start_date,
                c.end_date,
                t.name AS plc,
                s.code AS standard
            FROM plc_cycles AS c
            JOIN plc_teams AS t ON t.team_id = c.team_id
            JOIN standards AS s ON s.standard_id = c.standard_id
            WHERE c.status = 'Complete'
              {scope_filter}
            ORDER BY c.end_date DESC, c.cycle_id DESC
            LIMIT ?
            """,
            (*scope_params, int(limit)),
        ).fetchall()

    evidence_by_cycle: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for result in _student_standard_results(team_ids):
        evidence_by_cycle[int(result["cycle_id"])].append(result)

    completed = []
    for row in rows:
        item = dict(row)
        evidence = evidence_by_cycle.get(int(item["cycle_id"]), [])
        if evidence:
            latest_key = max(
                (result["administered_on"], result["administration_id"])
                for result in evidence
            )
            evidence = [
                result
                for result in evidence
                if (result["administered_on"], result["administration_id"])
                == latest_key
            ]

        outcome = _outcome_summary(evidence)
        item.update(
            {
                "students_assessed": len(
                    {int(result["student_id"]) for result in evidence}
                ),
                "average": outcome["average"],
                "mastery_rate": outcome["mastery_rate"],
            }
        )
        completed.append(item)

    return completed


def _scoped_count(
    connection,
    *,
    table_sql: str,
    where_sql: str,
    team_ids: tuple[int, ...] | None,
    params: tuple[Any, ...] = (),
) -> int:
    """Execute a COUNT query that can optionally be narrowed by c.team_id."""
    scope_filter, scope_params = _scope_filter(
        "c.team_id",
        team_ids,
    )

    return int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            {table_sql}
            WHERE {where_sql}
              {scope_filter}
            """,
            (*params, *scope_params),
        ).fetchone()[0]
    )


def _alerts(
    today: date,
    team_ids: tuple[int, ...] | None = None,
) -> list[dict[str, str]]:
    """Create explainable alerts within the user's current team scope."""
    alerts: list[dict[str, str]] = []
    today_text = today.isoformat()

    with connect() as connection:
        overdue_commitments = _scoped_count(
            connection,
            table_sql="""
                FROM commitments AS cm
                JOIN plc_cycles AS c
                    ON c.cycle_id = cm.cycle_id
            """,
            where_sql="""
                cm.status = 'Open'
                AND cm.due_date < ?
            """,
            team_ids=team_ids,
            params=(today_text,),
        )

        upcoming_commitments = _scoped_count(
            connection,
            table_sql="""
                FROM commitments AS cm
                JOIN plc_cycles AS c
                    ON c.cycle_id = cm.cycle_id
            """,
            where_sql="""
                cm.status = 'Open'
                AND cm.due_date >= ?
                AND cm.due_date <= date(?, '+3 days')
            """,
            team_ids=team_ids,
            params=(today_text, today_text),
        )

        overdue_interventions = _scoped_count(
            connection,
            table_sql="""
                FROM interventions AS i
                JOIN plc_cycles AS c
                    ON c.cycle_id = i.cycle_id
            """,
            where_sql="""
                i.status IN ('Planned', 'Active')
                AND i.end_date IS NOT NULL
                AND i.end_date < ?
            """,
            team_ids=team_ids,
            params=(today_text,),
        )

    if overdue_commitments:
        alerts.append(
            {
                "Priority": "High",
                "Alert": (
                    f"{overdue_commitments} "
                    "commitment(s) overdue"
                ),
                "Action": "Review commitments",
            }
        )

    if upcoming_commitments:
        alerts.append(
            {
                "Priority": "Medium",
                "Alert": (
                    f"{upcoming_commitments} "
                    "commitment(s) due within 3 days"
                ),
                "Action": "Review commitments",
            }
        )

    if overdue_interventions:
        alerts.append(
            {
                "Priority": "High",
                "Alert": (
                    f"{overdue_interventions} "
                    "intervention(s) past end date"
                ),
                "Action": "Review interventions",
            }
        )

    intensive = sum(
        item["status"] == "Intensive"
        for item in _latest_results_by_student_standard(
            team_ids
        )
    )

    if intensive:
        alerts.append(
            {
                "Priority": "High",
                "Alert": (
                    f"{intensive} student-standard "
                    "result(s) need intensive support"
                ),
                "Action": "Open Student Groups",
            }
        )

    return alerts


def _people(
    scope: DataScope,
) -> list[dict[str, Any]]:
    """Return people visible under the resolved access scope."""
    if scope.visible_user_ids is None:
        where_sql = ""
        params: tuple[Any, ...] = ()
    elif not scope.visible_user_ids:
        return []
    else:
        placeholders = ",".join(
            "?"
            for _ in scope.visible_user_ids
        )
        where_sql = (
            f"WHERE u.user_id IN ({placeholders})"
        )
        params = tuple(scope.visible_user_ids)

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                u.user_id,
                u.display_name,
                u.email,
                u.role,
                COALESCE(
                    (
                        SELECT GROUP_CONCAT(name, ', ')
                        FROM (
                            SELECT DISTINCT s.school_name AS name
                            FROM schools AS s
                            WHERE s.school_id IN (
                                SELECT usa.school_id
                                FROM user_school_assignments AS usa
                                WHERE usa.user_id = u.user_id

                                UNION

                                SELECT u.school_id
                                WHERE u.school_id IS NOT NULL
                            )
                            ORDER BY name
                        )
                    ),
                    'District-wide / Unassigned'
                ) AS schools
            FROM app_users AS u
            {where_sql}
            ORDER BY u.display_name
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def _teacher_summaries(
    scope: DataScope,
) -> list[dict[str, Any]]:
    """Return follow-through for teachers visible to a Coach."""
    if scope.visible_user_ids is None:
        where_sql = "WHERE u.role = 'Teacher'"
        params: tuple[Any, ...] = ()
    elif not scope.visible_user_ids:
        return []
    else:
        placeholders = ",".join(
            "?"
            for _ in scope.visible_user_ids
        )
        where_sql = (
            "WHERE u.role = 'Teacher' "
            f"AND u.user_id IN ({placeholders})"
        )
        params = tuple(scope.visible_user_ids)

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                u.user_id,
                u.display_name,
                u.email,
                (
                    SELECT COUNT(DISTINCT tm.team_id)
                    FROM plc_team_members AS tm
                    WHERE tm.user_id = u.user_id
                ) AS plc_teams,
                (
                    SELECT COUNT(*)
                    FROM commitments AS cm
                    WHERE cm.assigned_user_id = u.user_id
                      AND cm.status = 'Open'
                ) AS open_commitments,
                (
                    SELECT COUNT(*)
                    FROM commitments AS cm2
                    WHERE cm2.assigned_user_id = u.user_id
                      AND cm2.status = 'Open'
                      AND cm2.due_date < date('now')
                ) AS overdue_commitments
            FROM app_users AS u
            {where_sql}
            ORDER BY u.display_name
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def _school_summaries(
    scope: DataScope,
    latest_results: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize PLC implementation and outcomes by visible school."""
    if scope.school_ids is None:
        school_where = ""
        school_params: tuple[Any, ...] = ()
    elif not scope.school_ids:
        return []
    else:
        placeholders = ",".join(
            "?"
            for _ in scope.school_ids
        )
        school_where = (
            f"WHERE s.school_id IN ({placeholders})"
        )
        school_params = tuple(scope.school_ids)

    with connect() as connection:
        school_rows = connection.execute(
            f"""
            SELECT
                s.school_id,
                s.school_name
            FROM schools AS s
            {school_where}
            ORDER BY s.school_name
            """,
            school_params,
        ).fetchall()

        team_rows = connection.execute(
            """
            SELECT
                team_id,
                school_id
            FROM plc_teams
            """
        ).fetchall()

    school_by_team = {
        int(row["team_id"]): int(row["school_id"])
        for row in team_rows
    }

    output = []

    for school_row in school_rows:
        school_id = int(school_row["school_id"])

        team_ids = {
            team_id
            for team_id, mapped_school_id
            in school_by_team.items()
            if mapped_school_id == school_id
        }

        results = [
            row
            for row in latest_results
            if int(row["team_id"]) in team_ids
        ]

        school_cycles = [
            row
            for row in cycles
            if int(row["team_id"]) in team_ids
        ]

        outcome = _outcome_summary(results)

        output.append(
            {
                "school_id": school_id,
                "school": school_row["school_name"],
                "plc_teams": len(team_ids),
                "active_cycles": len(school_cycles),
                "assessed": len(
                    {
                        int(row["student_id"])
                        for row in results
                    }
                ),
                "average": outcome["average"],
                "mastery_rate": outcome["mastery_rate"],
                "intensive": outcome["intensive"],
            }
        )

    return output


def _commitments(
    team_ids: tuple[int, ...] | None,
) -> list[dict[str, Any]]:
    scope_filter, scope_params = _scope_filter(
        "c.team_id",
        team_ids,
    )

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                cm.name,
                cm.action_step,
                cm.due_date,
                cm.status,
                c.name AS cycle_name,
                s.code AS standard,
                COALESCE(
                    u.display_name,
                    'Unassigned'
                ) AS owner
            FROM commitments AS cm
            JOIN plc_cycles AS c
                ON c.cycle_id = cm.cycle_id
            JOIN standards AS s
                ON s.standard_id = c.standard_id
            LEFT JOIN app_users AS u
                ON u.user_id = cm.assigned_user_id
            WHERE 1 = 1
              {scope_filter}
            ORDER BY
                CASE cm.status
                    WHEN 'Open' THEN 0
                    ELSE 1
                END,
                cm.due_date
            LIMIT 6
            """,
            scope_params,
        ).fetchall()

    return [dict(row) for row in rows]


def _next_actions(
    *,
    today: date,
    commitments: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    today_text = today.isoformat()

    for commitment in commitments:
        if (
            commitment["status"] == "Open"
            and commitment["due_date"] <= today_text
        ):
            actions.append(
                {
                    "Priority": "Due",
                    "Action": (
                        f"Complete: {commitment['name']}"
                    ),
                    "Context": (
                        f"{commitment['cycle_name']} · "
                        f"due {commitment['due_date']}"
                    ),
                }
            )

    for cycle in cycles:
        if cycle["latest_administration_id"] is None:
            actions.append(
                {
                    "Priority": "Evidence needed",
                    "Action": (
                        "Enter CFA results: "
                        f"{cycle['cycle_name']}"
                    ),
                    "Context": (
                        f"{cycle['plc']} · "
                        f"{cycle['standard']}"
                    ),
                }
            )

    return actions[:6]


def get_dashboard_workspace(
    current_user: dict[str, Any] | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return all display-ready Dashboard data for the signed-in user's scope.

    IMPORTANT:
    `current_user` is the FIRST parameter so these both work:

        get_dashboard_workspace(current_user)
        get_dashboard_workspace(current_user, as_of=today)
    """
    today = as_of or date.today()
    scope = get_data_scope(current_user)

    latest_results = (
        _latest_results_by_student_standard(
            scope.team_ids
        )
    )

    counts = {
        status: 0
        for status in MASTERY_STATUSES
    }

    for result in latest_results:
        counts[result["status"]] += 1

    cycles = _cycle_rows(scope.team_ids)
    completed_cycles = _completed_cycle_rows(scope.team_ids)
    outcomes = _outcome_summary(latest_results)
    commitments = _commitments(scope.team_ids)

    with connect() as connection:
        active_interventions = _scoped_count(
            connection,
            table_sql="""
                FROM interventions AS i
                JOIN plc_cycles AS c
                    ON c.cycle_id = i.cycle_id
            """,
            where_sql="i.status = 'Active'",
            team_ids=scope.team_ids,
        )

        overdue_interventions = _scoped_count(
            connection,
            table_sql="""
                FROM interventions AS i
                JOIN plc_cycles AS c
                    ON c.cycle_id = i.cycle_id
            """,
            where_sql="""
                i.status IN ('Planned', 'Active')
                AND i.end_date IS NOT NULL
                AND i.end_date < ?
            """,
            team_ids=scope.team_ids,
            params=(today.isoformat(),),
        )

        my_open_commitments = 0

        if (
            current_user
            and scope.role == "Teacher"
        ):
            my_open_commitments = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM commitments
                    WHERE status = 'Open'
                      AND assigned_user_id = ?
                    """,
                    (
                        int(
                            current_user["user_id"]
                        ),
                    ),
                ).fetchone()[0]
            )

    return {
        "as_of": today.isoformat(),
        "scope": {
            "role": scope.role,
            "label": scope.label,
            "team_ids": scope.team_ids,
            "school_ids": scope.school_ids,
        },
        "kpis": {
            "students_assessed": len(
                {
                    int(item["student_id"])
                    for item in latest_results
                }
            ),
            "evidence_records": outcomes["assessed"],
            "average": outcomes["average"],
            "mastery_rate": outcomes["mastery_rate"],
            "standards_mastered": counts["Mastered"],
            "intensive_results": outcomes["intensive"],
            "active_cycles": len(cycles),
            "active_interventions": int(
                active_interventions
            ),
            "overdue_interventions": int(
                overdue_interventions
            ),
            "my_open_commitments": int(
                my_open_commitments
            ),
        },
        "mastery_counts": counts,
        "cycles": cycles,
        "completed_cycles": completed_cycles,
        "commitments": commitments,
        "alerts": _alerts(
            today,
            scope.team_ids,
        ),
        "next_actions": _next_actions(
            today=today,
            commitments=commitments,
            cycles=cycles,
        ),
        "school_summaries": _school_summaries(
            scope,
            latest_results,
            cycles,
        ),
        "teacher_summaries": _teacher_summaries(
            scope
        ),
        "people": _people(scope),
    }