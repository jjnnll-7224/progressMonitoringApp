"""Weekly PLC calendar planning and meeting-workspace helpers."""

from __future__ import annotations

from datetime import date
from typing import Any

from services.access_control import get_data_scope
from services.database import connect


MEETING_STEPS = [
    "Learning Focus",
    "Evidence",
    "Analyze",
    "Respond",
    "Follow Up",
]


def list_visible_teams(current_user: dict | None) -> list[dict[str, Any]]:
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
                t.school_id,
                COALESCE(s.school_name, t.school_id) AS school_name,
                t.grade_level,
                t.subject,
                COUNT(DISTINCT tm.user_id) AS member_count
            FROM plc_teams AS t
            LEFT JOIN schools AS s
                ON s.school_id = t.school_id
            LEFT JOIN plc_team_members AS tm
                ON tm.team_id = t.team_id
            {where}
            GROUP BY
                t.team_id, t.name, t.school_id, s.school_name,
                t.grade_level, t.subject
            ORDER BY s.school_name, t.grade_level, t.subject, t.name
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def list_terms() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT term_id, school_year, term_name, start_date, end_date, sort_order
            FROM school_terms
            ORDER BY school_year DESC, sort_order
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_cycles_for_team(team_id: int) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                c.cycle_id,
                c.name,
                c.start_date,
                c.end_date,
                c.stage,
                c.status,
                s.code AS standard,
                s.description AS standard_description
            FROM plc_cycles AS c
            JOIN standards AS s
                ON s.standard_id = c.standard_id
            WHERE c.team_id = ?
            ORDER BY c.start_date, c.name
            """,
            (team_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_team_standards(team_id: int) -> list[dict[str, Any]]:
    with connect() as connection:
        team = connection.execute(
            """
            SELECT subject, grade_level
            FROM plc_teams
            WHERE team_id = ?
            """,
            (team_id,),
        ).fetchone()

        if team is None:
            return []

        rows = connection.execute(
            """
            SELECT standard_id, code, description, subject, grade_level
            FROM standards
            WHERE subject = ?
              AND grade_level = ?
            ORDER BY code
            """,
            (team["subject"], team["grade_level"]),
        ).fetchall()

    return [dict(row) for row in rows]


def get_term_weeks(term_id: int, team_id: int) -> list[dict[str, Any]]:
    """Return each term week with pacing recommendation + team assignment."""
    with connect() as connection:
        team = connection.execute(
            """
            SELECT subject, grade_level
            FROM plc_teams
            WHERE team_id = ?
            """,
            (team_id,),
        ).fetchone()

        if team is None:
            return []

        rows = connection.execute(
            """
            SELECT
                w.week_id,
                w.term_id,
                w.week_number,
                w.week_start_date,
                w.week_end_date,
                w.label,
                wa.week_assignment_id,
                wa.cycle_id,
                wa.assignment_source,
                COALESCE(wa.completed_steps, 0) AS completed_steps,
                c.name AS cycle_name,
                c.stage AS cycle_stage,
                c.status AS cycle_status,
                s.code AS cycle_standard,
                (
                    SELECT GROUP_CONCAT(code, ', ')
                    FROM (
                        SELECT DISTINCT st.code AS code
                        FROM district_pacing_week_standards AS dp
                        JOIN standards AS st
                            ON st.standard_id = dp.standard_id
                        WHERE dp.week_id = w.week_id
                          AND dp.subject = ?
                          AND dp.grade_level = ?
                        ORDER BY st.code
                    )
                ) AS pacing_standards,
                (
                    SELECT GROUP_CONCAT(instructional_focus, ' | ')
                    FROM district_pacing_week_standards AS dp2
                    WHERE dp2.week_id = w.week_id
                      AND dp2.subject = ?
                      AND dp2.grade_level = ?
                      AND dp2.instructional_focus IS NOT NULL
                ) AS pacing_focus
            FROM calendar_weeks AS w
            LEFT JOIN plc_week_assignments AS wa
                ON wa.week_id = w.week_id
               AND wa.team_id = ?
            LEFT JOIN plc_cycles AS c
                ON c.cycle_id = wa.cycle_id
            LEFT JOIN standards AS s
                ON s.standard_id = c.standard_id
            WHERE w.term_id = ?
            ORDER BY w.week_number
            """,
            (
                team["subject"],
                team["grade_level"],
                team["subject"],
                team["grade_level"],
                team_id,
                term_id,
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def assign_cycle_to_week(
    *,
    team_id: int,
    week_id: int,
    cycle_id: int,
    assignment_source: str = "Team Assigned",
) -> int:
    with connect() as connection:
        cycle = connection.execute(
            """
            SELECT cycle_id
            FROM plc_cycles
            WHERE cycle_id = ?
              AND team_id = ?
            """,
            (cycle_id, team_id),
        ).fetchone()

        if cycle is None:
            raise ValueError("That PLC cycle does not belong to the selected team.")

        connection.execute(
            """
            INSERT INTO plc_week_assignments (
                team_id, week_id, cycle_id, assignment_source
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(team_id, week_id)
            DO UPDATE SET
                cycle_id = excluded.cycle_id,
                assignment_source = excluded.assignment_source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (team_id, week_id, cycle_id, assignment_source),
        )

        row = connection.execute(
            """
            SELECT week_assignment_id
            FROM plc_week_assignments
            WHERE team_id = ? AND week_id = ?
            """,
            (team_id, week_id),
        ).fetchone()

    return int(row["week_assignment_id"])


def create_week_cycle(
    *,
    team_id: int,
    week_id: int,
    standard_id: int,
    cycle_name: str,
    assignment_source: str = "Manual",
) -> int:
    clean_name = cycle_name.strip()
    if not clean_name:
        raise ValueError("Enter a name for the weekly PLC cycle.")

    with connect() as connection:
        week = connection.execute(
            """
            SELECT week_start_date, week_end_date, label
            FROM calendar_weeks
            WHERE week_id = ?
            """,
            (week_id,),
        ).fetchone()

        team = connection.execute(
            """
            SELECT subject, grade_level
            FROM plc_teams
            WHERE team_id = ?
            """,
            (team_id,),
        ).fetchone()

        standard = connection.execute(
            """
            SELECT standard_id, subject, grade_level
            FROM standards
            WHERE standard_id = ?
            """,
            (standard_id,),
        ).fetchone()

        if week is None or team is None or standard is None:
            raise ValueError("Week, team, or standard could not be found.")

        if (
            standard["subject"] != team["subject"]
            or standard["grade_level"] != team["grade_level"]
        ):
            raise ValueError("Choose a standard aligned to this PLC team.")

        cursor = connection.execute(
            """
            INSERT INTO plc_cycles (
                team_id,
                standard_id,
                name,
                start_date,
                end_date,
                stage,
                status
            )
            VALUES (?, ?, ?, ?, ?, 'Assessment', 'In Progress')
            """,
            (
                team_id,
                standard_id,
                clean_name,
                week["week_start_date"],
                week["week_end_date"],
            ),
        )
        cycle_id = int(cursor.lastrowid)

        # If the multi-standard bridge exists, keep the primary standard synced.
        bridge_exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'plc_cycle_standards'
            """
        ).fetchone()

        if bridge_exists:
            connection.execute(
                """
                INSERT OR IGNORE INTO plc_cycle_standards (cycle_id, standard_id)
                VALUES (?, ?)
                """,
                (cycle_id, standard_id),
            )

        connection.execute(
            """
            INSERT INTO plc_week_assignments (
                team_id, week_id, cycle_id, assignment_source
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(team_id, week_id)
            DO UPDATE SET
                cycle_id = excluded.cycle_id,
                assignment_source = excluded.assignment_source,
                completed_steps = 0,
                updated_at = CURRENT_TIMESTAMP
            """,
            (team_id, week_id, cycle_id, assignment_source),
        )

    return cycle_id


def clear_week_assignment(team_id: int, week_id: int) -> None:
    with connect() as connection:
        connection.execute(
            """
            DELETE FROM plc_week_assignments
            WHERE team_id = ?
              AND week_id = ?
            """,
            (team_id, week_id),
        )


def set_week_progress(week_assignment_id: int, completed_steps: int) -> None:
    if not 0 <= completed_steps <= len(MEETING_STEPS):
        raise ValueError("Weekly PLC progress must be between 0 and 5 steps.")

    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE plc_week_assignments
            SET completed_steps = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE week_assignment_id = ?
            """,
            (completed_steps, week_assignment_id),
        )

        if cursor.rowcount == 0:
            raise ValueError("That weekly PLC workspace no longer exists.")


def save_week_note(
    *,
    week_assignment_id: int,
    user_id: int | None,
    note_text: str,
) -> int:
    clean_note = note_text.strip()
    if not clean_note:
        raise ValueError("Enter a meeting note before saving.")

    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO plc_week_notes (
                week_assignment_id,
                user_id,
                note_text
            )
            VALUES (?, ?, ?)
            """,
            (week_assignment_id, user_id, clean_note),
        )
        return int(cursor.lastrowid)


def list_week_notes(
    week_assignment_id: int,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                n.note_id,
                n.note_text,
                n.created_at,
                COALESCE(u.display_name, 'PLC Team') AS author_name
            FROM plc_week_notes AS n
            LEFT JOIN app_users AS u
                ON u.user_id = n.user_id
            WHERE n.week_assignment_id = ?
            ORDER BY n.created_at DESC, n.note_id DESC
            LIMIT ?
            """,
            (week_assignment_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def current_week_id(term_id: int, today: date | None = None) -> int | None:
    today_text = (today or date.today()).isoformat()

    with connect() as connection:
        row = connection.execute(
            """
            SELECT week_id
            FROM calendar_weeks
            WHERE term_id = ?
              AND ? BETWEEN week_start_date AND week_end_date
            LIMIT 1
            """,
            (term_id, today_text),
        ).fetchone()

    return int(row["week_id"]) if row else None
