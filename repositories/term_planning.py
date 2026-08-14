"""Weekly PLC calendar planning and meeting-workspace helpers."""

from __future__ import annotations

from datetime import date
import re
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


def _normalize_grade(value: object) -> str:
    """Normalize grade labels such as 8, 8th, and Grade 8 to one value."""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""

    compact = (
        raw.replace("grade", "")
        .replace(" ", "")
        .replace("-", "")
        .strip()
    )

    aliases = {
        "k": "k",
        "kg": "k",
        "kindergarten": "k",
        "prekindergarten": "pk",
        "prek": "pk",
        "pk": "pk",
    }
    if compact in aliases:
        return aliases[compact]

    # Remove English ordinal suffixes: 6th, 7th, 8th, 1st, etc.
    compact = re.sub(r"(st|nd|rd|th)$", "", compact)

    # Keep simple numeric grades canonical.
    if compact.isdigit():
        return str(int(compact))

    return compact


def _normalize_subject(value: object) -> str:
    """Normalize common K-12 subject naming variants."""
    raw = str(value or "").strip().lower()
    if not raw:
        return ""

    compact = re.sub(r"[^a-z0-9]+", "", raw)

    aliases = {
        # English / ELA
        "ela": "ela",
        "english": "ela",
        "englishlanguagearts": "ela",
        "languagearts": "ela",
        "literacy": "ela",
        # Mathematics
        "math": "math",
        "maths": "math",
        "mathematics": "math",
        # Science
        "science": "science",
        "sciences": "science",
        # Social studies
        "socialstudies": "socialstudies",
        "socialscience": "socialstudies",
        "socialsciences": "socialstudies",
        "history": "socialstudies",
    }

    return aliases.get(compact, compact)


def _team_standard_match(
    *,
    team_subject: object,
    team_grade: object,
    standard_subject: object,
    standard_grade: object,
) -> bool:
    return (
        _normalize_subject(team_subject) == _normalize_subject(standard_subject)
        and _normalize_grade(team_grade) == _normalize_grade(standard_grade)
    )


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
    """Return standards aligned to a team's normalized grade + subject labels."""
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

        # Do not use exact SQL equality here. Team labels often come from SIS
        # course/section data while standards labels come from a standards source.
        rows = connection.execute(
            """
            SELECT standard_id, code, description, subject, grade_level
            FROM standards
            ORDER BY code
            """
        ).fetchall()

    return [
        dict(row)
        for row in rows
        if _team_standard_match(
            team_subject=team["subject"],
            team_grade=team["grade_level"],
            standard_subject=row["subject"],
            standard_grade=row["grade_level"],
        )
    ]


def get_term_weeks(term_id: int, team_id: int) -> list[dict[str, Any]]:
    """Return term weeks, assignments, and normalized pacing recommendations."""
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

        week_rows = connection.execute(
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
                s.code AS cycle_standard
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
            (team_id, term_id),
        ).fetchall()

        pacing_rows = connection.execute(
            """
            SELECT
                dp.week_id,
                dp.subject,
                dp.grade_level,
                dp.instructional_focus,
                st.code
            FROM district_pacing_week_standards AS dp
            JOIN calendar_weeks AS w
                ON w.week_id = dp.week_id
            JOIN standards AS st
                ON st.standard_id = dp.standard_id
            WHERE w.term_id = ?
            ORDER BY dp.week_id, st.code
            """,
            (term_id,),
        ).fetchall()

    pacing_by_week: dict[int, dict[str, list[str]]] = {}

    for row in pacing_rows:
        if not _team_standard_match(
            team_subject=team["subject"],
            team_grade=team["grade_level"],
            standard_subject=row["subject"],
            standard_grade=row["grade_level"],
        ):
            continue

        bucket = pacing_by_week.setdefault(
            int(row["week_id"]),
            {"standards": [], "focus": []},
        )

        code = str(row["code"] or "").strip()
        if code and code not in bucket["standards"]:
            bucket["standards"].append(code)

        focus = str(row["instructional_focus"] or "").strip()
        if focus and focus not in bucket["focus"]:
            bucket["focus"].append(focus)

    output = []
    for row in week_rows:
        item = dict(row)
        pacing = pacing_by_week.get(
            int(item["week_id"]),
            {"standards": [], "focus": []},
        )
        item["pacing_standards"] = (
            ", ".join(pacing["standards"])
            if pacing["standards"]
            else None
        )
        item["pacing_focus"] = (
            " | ".join(pacing["focus"])
            if pacing["focus"]
            else None
        )
        output.append(item)

    return output


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

        if not _team_standard_match(
            team_subject=team["subject"],
            team_grade=team["grade_level"],
            standard_subject=standard["subject"],
            standard_grade=standard["grade_level"],
        ):
            raise ValueError(
                "Choose a standard aligned to this PLC team "
                f"(team: {team['grade_level']} {team['subject']}; "
                f"standard: {standard['grade_level']} {standard['subject']})."
            )

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