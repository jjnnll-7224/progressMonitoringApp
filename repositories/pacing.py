"""Persistence and read models for district pacing guides."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from services.database import connect


TERMS = ("Term 1", "Term 2", "Term 3", "Term 4")


def list_pacing_dimensions() -> dict[str, list[str]]:
    """Return only grade/subject choices that have district standards."""
    with connect() as connection:
        grades = connection.execute(
            "SELECT DISTINCT grade_level FROM standards ORDER BY CAST(grade_level AS INTEGER), grade_level"
        ).fetchall()
        subjects = connection.execute(
            "SELECT DISTINCT subject FROM standards ORDER BY subject"
        ).fetchall()
    return {
        "grade_levels": [row["grade_level"] for row in grades],
        "subjects": [row["subject"] for row in subjects],
    }


def list_standards_for_pacing(grade_level: str, subject: str) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT standard_id, code, description
               FROM standards WHERE grade_level = ? AND subject = ? ORDER BY code""",
            (grade_level, subject),
        ).fetchall()
    return [dict(row) for row in rows]


def get_pacing_guide(school_year: str, grade_level: str, subject: str) -> dict[str, Any] | None:
    """Return one guide and its saved weekly standard selections."""
    with connect() as connection:
        guide = connection.execute(
            """SELECT pacing_guide_id, school_year, grade_level, subject, current_term, current_week
               FROM pacing_guides
               WHERE school_year = ? AND grade_level = ? AND subject = ?""",
            (school_year, grade_level, subject),
        ).fetchone()
        if guide is None:
            return None
        entries = connection.execute(
            """SELECT p.term_name, p.week_number, s.standard_id, s.code, s.description
               FROM pacing_guide_entries p
               JOIN standards s ON s.standard_id = p.standard_id
               WHERE p.pacing_guide_id = ?
               ORDER BY CASE p.term_name
                    WHEN 'Term 1' THEN 1 WHEN 'Term 2' THEN 2
                    WHEN 'Term 3' THEN 3 WHEN 'Term 4' THEN 4 ELSE 99 END,
                    p.week_number""",
            (guide["pacing_guide_id"],),
        ).fetchall()
    return {**dict(guide), "entries": [dict(row) for row in entries]}


def save_pacing_term(
    *, school_year: str, grade_level: str, subject: str, term_name: str,
    weekly_standard_ids: Sequence[int | None], current_term: str, current_week: int,
) -> None:
    """Replace one term's weekly assignments without changing other terms."""
    if term_name not in TERMS or current_term not in TERMS:
        raise ValueError("Choose a valid term.")
    if not school_year.strip() or not grade_level or not subject:
        raise ValueError("School year, grade, and subject are required.")
    if current_week < 1:
        raise ValueError("The current week must be at least 1.")

    clean_ids = [int(value) if value is not None else None for value in weekly_standard_ids]
    with connect() as connection:
        valid = {
            row["standard_id"] for row in connection.execute(
                "SELECT standard_id FROM standards WHERE grade_level = ? AND subject = ?",
                (grade_level, subject),
            ).fetchall()
        }
        if any(value not in valid for value in clean_ids if value is not None):
            raise ValueError("Every selected standard must match the chosen grade and subject.")

        connection.execute(
            """INSERT INTO pacing_guides
                   (school_year, grade_level, subject, current_term, current_week)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(school_year, grade_level, subject) DO UPDATE SET
                   current_term = excluded.current_term,
                   current_week = excluded.current_week,
                   updated_at = CURRENT_TIMESTAMP""",
            (school_year.strip(), grade_level, subject, current_term, current_week),
        )
        guide_id = connection.execute(
            """SELECT pacing_guide_id FROM pacing_guides
               WHERE school_year = ? AND grade_level = ? AND subject = ?""",
            (school_year.strip(), grade_level, subject),
        ).fetchone()["pacing_guide_id"]
        connection.execute(
            "DELETE FROM pacing_guide_entries WHERE pacing_guide_id = ? AND term_name = ?",
            (guide_id, term_name),
        )
        connection.executemany(
            """INSERT INTO pacing_guide_entries
                   (pacing_guide_id, term_name, week_number, standard_id)
               VALUES (?, ?, ?, ?)""",
            [
                (guide_id, term_name, week_number, standard_id)
                for week_number, standard_id in enumerate(clean_ids, start=1)
                if standard_id is not None
            ],
        )


def get_upcoming_pacing(
    *, team_ids: tuple[int, ...] | None, limit: int = 4
) -> list[dict[str, Any]]:
    """Return the current and next planned weeks relevant to visible PLC teams."""
    if team_ids == ():
        return []
    with connect() as connection:
        clauses = []
        parameters: list[Any] = []
        if team_ids is not None:
            clauses.append(f"t.team_id IN ({','.join('?' for _ in team_ids)})")
            parameters.extend(team_ids)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute(
            f"""SELECT g.school_year, g.grade_level, g.subject, g.current_term, g.current_week,
                       p.term_name, p.week_number, s.standard_id, s.code AS standard_code, s.description,
                       t.team_id, t.name AS team_name, t.school_id,
                       (
                           SELECT c.cycle_id
                           FROM plc_cycles AS c
                           WHERE c.team_id = t.team_id
                             AND c.standard_id = s.standard_id
                             AND c.status != 'Complete'
                           ORDER BY c.start_date DESC, c.cycle_id DESC
                           LIMIT 1
                       ) AS cycle_id,
                       (
                           SELECT c.name
                           FROM plc_cycles AS c
                           WHERE c.team_id = t.team_id
                             AND c.standard_id = s.standard_id
                             AND c.status != 'Complete'
                           ORDER BY c.start_date DESC, c.cycle_id DESC
                           LIMIT 1
                       ) AS cycle_name
                FROM pacing_guides g
                JOIN pacing_guide_entries p ON p.pacing_guide_id = g.pacing_guide_id
                JOIN standards s ON s.standard_id = p.standard_id
                JOIN plc_teams t ON t.grade_level = g.grade_level AND t.subject = g.subject
                {where}
                ORDER BY t.name, g.grade_level, g.subject,
                  CASE p.term_name WHEN 'Term 1' THEN 1 WHEN 'Term 2' THEN 2 WHEN 'Term 3' THEN 3 WHEN 'Term 4' THEN 4 ELSE 99 END,
                  p.week_number""",
            parameters,
        ).fetchall()

    upcoming: list[dict[str, Any]] = []
    term_order = {term: index for index, term in enumerate(TERMS)}
    for row in rows:
        item = dict(row)
        entry_position = (term_order.get(item["term_name"], 99), item["week_number"])
        current_position = (term_order.get(item["current_term"], 0), item["current_week"])
        if entry_position >= current_position:
            item["timing"] = "This week" if entry_position == current_position else "Upcoming"
            upcoming.append(item)
    return upcoming[:limit]