"""Database queries used by the Assessments page.

Keeping SQL here (instead of in the Streamlit page) makes UI errors easier to
separate from database errors and gives us small functions we can test.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from services.database import connect

def get_sections_for_user(
    user_id: int | None,
    *,
    standard_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return the signed-in teacher's sections."""
    if user_id is None:
        return []

    query = """
        SELECT
            se.section_id,
            se.section_name,
            se.term_name,
            co.course_code,
            co.course_name,
            co.subject,
            co.grade_level,
            COUNT(en.student_id) AS student_count
        FROM sections AS se
        JOIN courses AS co
            ON co.course_id = se.course_id
        LEFT JOIN section_enrollments AS en
            ON en.section_id = se.section_id
        WHERE se.teacher_user_id = ?
    """
    parameters: list[Any] = [user_id]

    if standard_id is not None:
        query += """
            AND co.subject = (
                SELECT subject
                FROM standards
                WHERE standard_id = ?
            )
        """
        parameters.append(standard_id)

    query += """
        GROUP BY se.section_id
        ORDER BY se.term_name, se.section_name
    """

    with connect() as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [dict(row) for row in rows]

def set_assessment_sections(
    assessment_id: int,
    section_ids: Sequence[int],
) -> None:
    """Replace an assessment's assigned class sections."""
    clean_ids = list(dict.fromkeys(int(section_id) for section_id in section_ids))

    if not clean_ids:
        raise ValueError("Assign at least one class section to this assessment.")

    with connect() as connection:
        assessment = connection.execute(
            "SELECT status FROM assessments WHERE assessment_id = ?",
            (assessment_id,),
        ).fetchone()

        if assessment is None:
            raise ValueError("The selected assessment no longer exists.")

        if assessment["status"] == "Results Entered":
            raise ValueError("Sections cannot change after results are entered.")

        placeholders = ",".join("?" for _ in clean_ids)
        found = connection.execute(
            f"SELECT COUNT(*) FROM sections WHERE section_id IN ({placeholders})",
            clean_ids,
        ).fetchone()[0]

        if found != len(clean_ids):
            raise ValueError("One or more selected sections no longer exist.")

        connection.execute(
            "DELETE FROM assessment_sections WHERE assessment_id = ?",
            (assessment_id,),
        )
        connection.executemany(
            """
            INSERT INTO assessment_sections (assessment_id, section_id)
            VALUES (?, ?)
            """,
            [(assessment_id, section_id) for section_id in clean_ids],
        )

def get_standards() -> list[dict[str, Any]]:
    """Return standards in a teacher-friendly sort order."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT standard_id, code, description, subject, grade_level
            FROM standards
            ORDER BY subject, grade_level, code
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_cycles(standard_id: int | None = None) -> list[dict[str, Any]]:
    """Return active PLC cycles, optionally limited to one standard."""
    query = """
        SELECT c.cycle_id, c.name, c.standard_id, t.name AS team_name
        FROM plc_cycles AS c
        JOIN plc_teams AS t ON t.team_id = c.team_id
        WHERE c.status != 'Complete'
    """
    parameters: list[Any] = []

    if standard_id is not None:
        query += " AND c.standard_id = ?"
        parameters.append(standard_id)

    query += " ORDER BY c.start_date DESC, c.name"

    with connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def get_assessments(
    search: str = "",
    status: str = "All statuses",
    assessment_type: str = "All types",
) -> list[dict[str, Any]]:
    """Return assessment list rows with calculated completion information."""
    query = """
        SELECT
            a.assessment_id,
            a.name,
            s.code AS standard_code,
            s.subject,
            s.grade_level,
            a.assessment_type,
            a.status,
            MAX(ad.administered_on) AS latest_date,
            COUNT(DISTINCT q.question_id) AS question_count,
            (
                SELECT COALESCE(SUM(question.max_points), 0)
                FROM assessment_questions AS question
                WHERE question.assessment_id = a.assessment_id
            ) AS possible_points,
            COUNT(DISTINCT scores.student_id) AS students_scored,
            (
                SELECT COUNT(*)
                FROM students AS roster
                WHERE roster.grade_level = s.grade_level
            ) AS roster_count
        FROM assessments AS a
        JOIN standards AS s ON s.standard_id = a.standard_id
        LEFT JOIN assessment_questions AS q
            ON q.assessment_id = a.assessment_id
        LEFT JOIN assessment_administrations AS ad
            ON ad.assessment_id = a.assessment_id
        LEFT JOIN student_item_scores AS scores
            ON scores.administration_id = ad.administration_id
        WHERE LOWER(a.name || ' ' || s.code) LIKE ?
    """
    parameters: list[Any] = [f"%{search.strip().lower()}%"]

    # Add optional filters only when the teacher chooses a specific value.
    if status != "All statuses":
        query += " AND a.status = ?"
        parameters.append(status)
    if assessment_type != "All types":
        query += " AND a.assessment_type = ?"
        parameters.append(assessment_type)

    query += " GROUP BY a.assessment_id, s.standard_id ORDER BY a.assessment_id DESC"

    with connect() as connection:
        rows = connection.execute(query, parameters).fetchall()

    assessments = []
    for row in rows:
        item = dict(row)
        roster_count = item["roster_count"] or 0
        students_scored = item["students_scored"] or 0
        item["completion_rate"] = (
            min(students_scored / roster_count * 100, 100.0) if roster_count else 0.0
        )
        assessments.append(item)
    return assessments

def get_standards_for_cycle(cycle_id: int) -> list[dict]:
    """Return the standards assigned to one PLC cycle."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                s.standard_id,
                s.code,
                s.description,
                s.subject,
                s.grade_level
            FROM plc_cycle_standards AS pcs
            JOIN standards AS s
                ON s.standard_id = pcs.standard_id
            WHERE pcs.cycle_id = ?
            ORDER BY s.code
            """,
            (cycle_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_assessment(assessment_id: int) -> dict[str, Any] | None:
    """Return one assessment and all of its questions."""
    with connect() as connection:
        assessment = connection.execute(
            """
            SELECT
                a.assessment_id, a.name, a.assessment_type, a.status,
                s.code AS standard_code, s.description AS standard_description,
                s.subject, s.grade_level,
                c.name AS cycle_name
            FROM assessments AS a
            JOIN standards AS s ON s.standard_id = a.standard_id
            LEFT JOIN plc_cycles AS c ON c.cycle_id = a.cycle_id
            WHERE a.assessment_id = ?
            """,
            (assessment_id,),
        ).fetchone()

        if assessment is None:
            return None

        questions = connection.execute(
            """
            SELECT question_number, question_type, max_points, subskill
            FROM assessment_questions
            WHERE assessment_id = ?
            ORDER BY question_number
            """,
            (assessment_id,),
        ).fetchall()

        administrations = connection.execute(
            """
            SELECT COUNT(*)
            FROM assessment_administrations
            WHERE assessment_id = ?
            """,
            (assessment_id,),
        ).fetchone()[0]

    result = dict(assessment)
    result["questions"] = [dict(row) for row in questions]
    result["administration_count"] = administrations
    result["possible_points"] = sum(row["max_points"] for row in questions)
    return result


def create_assessment(
    *,
    name: str,
    standard_id: int,
    assessment_type: str,
    status: str,
    cycle_id: int | None,
    questions: Sequence[dict[str, Any]],
) -> int:
    """Create an assessment and its questions in a single transaction."""

    clean_name = name.strip()

    if not clean_name:
        raise ValueError("Assessment name is required.")

    if not questions:
        raise ValueError("Add at least one assessment question.")

    # Validate everything before opening the write transaction.
    clean_questions = []

    for number, question in enumerate(questions, start=1):
        question_type = str(
            question.get("question_type", "")
        ).strip()

        if not question_type:
            raise ValueError(
                f"Question {number} needs a question type."
            )

        try:
            max_points = float(
                question.get("max_points", 0)
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Question {number} needs a numeric point value."
            ) from error

        if max_points <= 0:
            raise ValueError(
                f"Question {number} must be worth more than 0 points."
            )

        question_standard_id = question.get("standard_id")

        if question_standard_id is None:
            raise ValueError(
                f"Question {number} must be mapped to a standard."
            )

        try:
            question_standard_id = int(question_standard_id)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Question {number} has an invalid standard."
            ) from error

        subskill = (
            str(question.get("subskill", "")).strip()
            or None
        )

        clean_questions.append(
            (
                number,
                question_type,
                max_points,
                subskill,
                question_standard_id,
            )
        )

    # The context manager commits all inserts together,
    # or rolls them all back.
    with connect() as connection:

        # Validate that the assessment-level standard exists.
        assessment_standard = connection.execute(
            """
            SELECT standard_id
            FROM standards
            WHERE standard_id = ?
            """,
            (standard_id,),
        ).fetchone()

        if assessment_standard is None:
            raise ValueError(
                "The selected assessment standard does not exist."
            )

        # If this assessment belongs to a PLC cycle, make sure every
        # question-level standard belongs to that cycle.
        if cycle_id is not None:
            allowed_standard_rows = connection.execute(
                """
                SELECT standard_id
                FROM plc_cycle_standards
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchall()

            allowed_standard_ids = {
                int(row["standard_id"])
                for row in allowed_standard_rows
            }

            for (
                number,
                _question_type,
                _max_points,
                _subskill,
                question_standard_id,
            ) in clean_questions:

                if question_standard_id not in allowed_standard_ids:
                    raise ValueError(
                        f"Question {number} uses a standard "
                        "that is not assigned to the selected PLC cycle."
                    )

        cursor = connection.execute(
            """
            INSERT INTO assessments (
                cycle_id,
                name,
                standard_id,
                assessment_type,
                status
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                cycle_id,
                clean_name,
                standard_id,
                assessment_type,
                status,
            ),
        )

        assessment_id = int(cursor.lastrowid)

        connection.executemany(
            """
            INSERT INTO assessment_questions (
                assessment_id,
                question_number,
                question_type,
                max_points,
                subskill,
                standard_id
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    assessment_id,
                    number,
                    question_type,
                    max_points,
                    subskill,
                    question_standard_id,
                )
                for (
                    number,
                    question_type,
                    max_points,
                    subskill,
                    question_standard_id,
                ) in clean_questions
            ],
        )

    return assessment_id