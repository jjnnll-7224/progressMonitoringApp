"""Database operations for CFA administrations and item-level scores.

The Streamlit page calls these small functions instead of containing SQL. This
keeps display problems separate from data problems when you need to debug.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from services.database import connect
from services.scoring import calculate_student_result


def get_entry_context(
    assessment_id: int,
    section_id: int | None = None,
) -> dict[str, Any] | None:
    """Load an assessment, its questions, assigned sections, and selected section roster."""

    with connect() as connection:
        assessment = connection.execute(
            """
            SELECT
                a.assessment_id,
                a.name,
                a.status,
                s.code AS standard_code,
                s.description AS standard_description,
                s.subject,
                s.grade_level
            FROM assessments AS a
            JOIN standards AS s
                ON s.standard_id = a.standard_id
            WHERE a.assessment_id = ?
            """,
            (assessment_id,),
        ).fetchone()

        if assessment is None:
            return None

        questions = connection.execute(
            """
            SELECT
                question_id,
                question_number,
                question_type,
                max_points,
                subskill
            FROM assessment_questions
            WHERE assessment_id = ?
            ORDER BY question_number
            """,
            (assessment_id,),
        ).fetchall()

        sections = connection.execute(
            """
            SELECT
                se.section_id,
                se.section_name,
                se.term_name,
                co.course_code,
                co.course_name,
                co.subject,
                co.grade_level,
                u.user_id AS teacher_user_id,
                u.display_name AS teacher_name,
                COUNT(DISTINCT en.student_id) AS student_count
            FROM assessment_sections AS ass
            JOIN sections AS se
                ON se.section_id = ass.section_id
            JOIN courses AS co
                ON co.course_id = se.course_id
            JOIN app_users AS u
                ON u.user_id = se.teacher_user_id
            LEFT JOIN section_enrollments AS en
                ON en.section_id = se.section_id
            WHERE ass.assessment_id = ?
            GROUP BY
                se.section_id,
                se.section_name,
                se.term_name,
                co.course_code,
                co.course_name,
                co.subject,
                co.grade_level,
                u.user_id,
                u.display_name
            ORDER BY
                co.course_name,
                u.display_name,
                se.section_name
            """,
            (assessment_id,),
        ).fetchall()

        students = []

        if section_id is not None:
            # Make sure the requested section actually belongs to this assessment.
            valid_section = connection.execute(
                """
                SELECT 1
                FROM assessment_sections
                WHERE assessment_id = ?
                  AND section_id = ?
                """,
                (assessment_id, section_id),
            ).fetchone()

            if valid_section is None:
                return None

            students = connection.execute(
                """
                SELECT
                    s.student_id,
                    s.student_number,
                    s.first_name,
                    s.last_name,
                    s.grade_level
                FROM section_enrollments AS en
                JOIN students AS s
                    ON s.student_id = en.student_id
                WHERE en.section_id = ?
                ORDER BY
                    s.last_name,
                    s.first_name
                """,
                (section_id,),
            ).fetchall()

    result = dict(assessment)

    result["questions"] = [
        dict(row)
        for row in questions
    ]

    result["sections"] = [
        dict(row)
        for row in sections
    ]

    result["students"] = [
        dict(row)
        for row in students
    ]

    return result


def get_administrations(assessment_id: int) -> list[dict[str, Any]]:
    """Return existing PRE and POST administrations for an assessment."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT administration_id, administration_type, administered_on, status
            FROM assessment_administrations
            WHERE assessment_id = ?
            ORDER BY administered_on DESC, administration_id DESC
            """,
            (assessment_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_administration(
    assessment_id: int,
    administration_type: str,
    administered_on: str,
) -> int:
    """Create a score-entry event, avoiding an accidental exact duplicate."""
    administration_type = administration_type.upper().strip()
    if administration_type not in {"PRE", "POST"}:
        raise ValueError("Administration type must be PRE or POST.")

    # Parsing here catches malformed dates before opening a write transaction.
    try:
        date.fromisoformat(administered_on)
    except ValueError as error:
        raise ValueError("Administration date must be a valid date.") from error

    with connect() as connection:
        assessment_exists = connection.execute(
            "SELECT 1 FROM assessments WHERE assessment_id = ?", (assessment_id,)
        ).fetchone()
        if assessment_exists is None:
            raise ValueError("The selected assessment no longer exists.")

        duplicate = connection.execute(
            """
            SELECT administration_id
            FROM assessment_administrations
            WHERE assessment_id = ? AND administration_type = ? AND administered_on = ?
            """,
            (assessment_id, administration_type, administered_on),
        ).fetchone()
        if duplicate:
            return int(duplicate["administration_id"])

        cursor = connection.execute(
            """
            INSERT INTO assessment_administrations
                (assessment_id, administration_type, administered_on, status)
            VALUES (?, ?, ?, 'Draft')
            """,
            (assessment_id, administration_type, administered_on),
        )
        return int(cursor.lastrowid)


def get_saved_scores(administration_id: int) -> list[dict[str, Any]]:
    """Load every nonblank item score saved for one administration."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT student_id, question_id, points_earned
            FROM student_item_scores
            WHERE administration_id = ?
            """,
            (administration_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_administration_results(administration_id: int) -> dict[str, Any] | None:
    """Return display-ready CFA evidence for one submitted administration.

    This is intentionally kept in the repository so the Streamlit view never
    needs to know SQL table names or recreate scoring rules.
    """
    with connect() as connection:
        administration = connection.execute(
            """
            SELECT ad.administration_id, ad.assessment_id, ad.administration_type,
                   ad.administered_on, ad.status, a.name AS assessment_name,
                   s.code AS standard_code, s.description AS standard_description
            FROM assessment_administrations AS ad
            JOIN assessments AS a ON a.assessment_id = ad.assessment_id
            JOIN standards AS s ON s.standard_id = a.standard_id
            WHERE ad.administration_id = ?
            """,
            (administration_id,),
        ).fetchone()
        if administration is None:
            return None

        questions = [dict(row) for row in connection.execute(
            """SELECT question_id, question_number, max_points, subskill
               FROM assessment_questions WHERE assessment_id = ?
               ORDER BY question_number""",
            (administration["assessment_id"],),
        ).fetchall()]
        score_rows = [dict(row) for row in connection.execute(
            """
            SELECT sc.student_id, sc.question_id, sc.points_earned,
                   st.student_number, st.last_name || ', ' || st.first_name AS student_name
            FROM student_item_scores AS sc
            JOIN students AS st ON st.student_id = sc.student_id
            WHERE sc.administration_id = ?
            ORDER BY st.last_name, st.first_name
            """,
            (administration_id,),
        ).fetchall()]

    possible = {str(q["question_id"]): float(q["max_points"]) for q in questions}
    scores_by_student: dict[int, dict[str, float | None]] = {}
    students: dict[int, dict[str, Any]] = {}
    for score in score_rows:
        student_id = int(score["student_id"])
        students[student_id] = score
        scores_by_student.setdefault(student_id, {})[str(score["question_id"])] = score["points_earned"]

    student_results = []
    for student_id, scores in scores_by_student.items():
        student_results.append({
            "student_id": student_id,
            "student_number": students[student_id]["student_number"],
            "student_name": students[student_id]["student_name"],
            **calculate_student_result(scores, possible),
        })
    student_results.sort(key=lambda row: (row["percent"] is None, row["percent"] or 0, row["student_name"]))

    completed = [row for row in student_results if row["percent"] is not None]
    counts = {name: 0 for name in ("Mastered", "Approaching", "Developing", "Intensive")}
    for row in completed:
        counts[row["status"]] += 1

    question_performance = []
    for question in questions:
        answered = [float(row["points_earned"]) for row in score_rows
                    if row["question_id"] == question["question_id"] and row["points_earned"] is not None]
        question_performance.append({
            "question": f"Q{question['question_number']}",
            "subskill": question["subskill"] or "Not specified",
            "students_answered": len(answered),
            "percent": sum(answered) / (len(answered) * float(question["max_points"])) * 100 if answered else None,
        })

    subskill_totals: dict[str, dict[str, float]] = {}
    question_by_id = {item["question_id"]: item for item in questions}
    for score in score_rows:
        if score["points_earned"] is None:
            continue
        question = question_by_id[score["question_id"]]
        totals = subskill_totals.setdefault(question["subskill"] or "Not specified", {"earned": 0.0, "possible": 0.0})
        totals["earned"] += float(score["points_earned"])
        totals["possible"] += float(question["max_points"])
    subskill_performance = sorted(
        ({"subskill": name, "percent": totals["earned"] / totals["possible"] * 100}
         for name, totals in subskill_totals.items() if totals["possible"]),
        key=lambda row: row["percent"],
    )

    result = dict(administration)
    result.update({
        "student_results": student_results,
        "completed": len(completed),
        "average": sum(row["percent"] for row in completed) / len(completed) if completed else None,
        "counts": counts,
        "question_performance": question_performance,
        "subskill_performance": subskill_performance,
    })
    return result


def save_scores(
    administration_id: int,
    scores: Sequence[dict[str, Any]],
    *,
    submit: bool = False,
) -> None:
    """Replace one administration's saved scores in a single transaction.

    Drafts may contain blanks. Submission requires every rostered student to
    have a valid score for every question.
    """
    with connect() as connection:
        administration = connection.execute(
            """
            SELECT ad.assessment_id, s.grade_level
            FROM assessment_administrations AS ad
            JOIN assessments AS a ON a.assessment_id = ad.assessment_id
            JOIN standards AS s ON s.standard_id = a.standard_id
            WHERE ad.administration_id = ?
            """,
            (administration_id,),
        ).fetchone()
        if administration is None:
            raise ValueError("The selected administration no longer exists.")

        question_rows = connection.execute(
            """
            SELECT question_id, max_points
            FROM assessment_questions
            WHERE assessment_id = ?
            """,
            (administration["assessment_id"],),
        ).fetchall()
        possible_by_question = {
            int(row["question_id"]): float(row["max_points"]) for row in question_rows
        }

        student_rows = connection.execute(
            "SELECT student_id FROM students WHERE grade_level = ?",
            (administration["grade_level"],),
        ).fetchall()
        allowed_students = {int(row["student_id"]) for row in student_rows}

        clean_scores: dict[tuple[int, int], float] = {}
        for item in scores:
            student_id = int(item["student_id"])
            question_id = int(item["question_id"])
            earned = item.get("points_earned")

            if student_id not in allowed_students:
                raise ValueError(f"Student {student_id} is not in this assessment roster.")
            if question_id not in possible_by_question:
                raise ValueError(f"Question {question_id} is not part of this assessment.")
            if earned is None:
                continue

            earned = float(earned)
            if not 0 <= earned <= possible_by_question[question_id]:
                raise ValueError(
                    f"Question {question_id} score must be between 0 and "
                    f"{possible_by_question[question_id]:g}."
                )
            clean_scores[(student_id, question_id)] = earned

        expected_count = len(allowed_students) * len(possible_by_question)
        if submit and len(clean_scores) != expected_count:
            raise ValueError("Complete every student score before submitting results.")

        # Delete-and-reinsert makes clearing a previously saved cell behave
        # correctly and remains safe because the context manager is atomic.
        connection.execute(
            "DELETE FROM student_item_scores WHERE administration_id = ?",
            (administration_id,),
        )
        connection.executemany(
            """
            INSERT INTO student_item_scores
                (administration_id, student_id, question_id, points_earned)
            VALUES (?, ?, ?, ?)
            """,
            [
                (administration_id, student_id, question_id, earned)
                for (student_id, question_id), earned in clean_scores.items()
            ],
        )
        connection.execute(
            "UPDATE assessment_administrations SET status = ? WHERE administration_id = ?",
            ("Submitted" if submit else "Draft", administration_id),
        )
        if submit:
            connection.execute(
                "UPDATE assessments SET status = 'Results Entered' WHERE assessment_id = ?",
                (administration["assessment_id"],),
            )