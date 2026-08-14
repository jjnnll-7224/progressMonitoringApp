"""CFA entry + PRE/POST results repository.

This module supports the cycle-specific CFA workflow used by:
- views/cfa_data_entry.py
- views/cfa_results.py

The key identifier is cycle_assessment_id, not assessment_id. A reusable CFA can
be assigned to more than one PLC cycle, and each assignment can use different
class sections and PRE/POST administrations.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from services.database import connect
from services.scoring import calculate_student_result


MASTERY_STATUSES = (
    "Mastered",
    "Approaching",
    "Developing",
    "Intensive",
)


def _assignment(cycle_assessment_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                pca.cycle_assessment_id,
                pca.cycle_id,
                pca.assessment_id,
                pca.status AS assignment_status,
                a.name,
                a.status AS assessment_status,
                c.name AS cycle_name,
                t.name AS team_name
            FROM plc_cycle_assessments AS pca
            JOIN assessments AS a
                ON a.assessment_id = pca.assessment_id
            JOIN plc_cycles AS c
                ON c.cycle_id = pca.cycle_id
            JOIN plc_teams AS t
                ON t.team_id = c.team_id
            WHERE pca.cycle_assessment_id = ?
            """,
            (int(cycle_assessment_id),),
        ).fetchone()

    return dict(row) if row else None


def get_entry_context(
    cycle_assessment_id: int,
    section_id: int | None = None,
) -> dict[str, Any] | None:
    """Load one cycle-specific CFA assignment and, optionally, one section roster."""
    assignment = _assignment(cycle_assessment_id)
    if assignment is None:
        return None

    with connect() as connection:
        standards = connection.execute(
            """
            SELECT DISTINCT
                s.standard_id,
                s.code,
                s.description
            FROM assessment_standards AS ast
            JOIN standards AS s
                ON s.standard_id = ast.standard_id
            WHERE ast.assessment_id = ?
            ORDER BY s.code
            """,
            (int(assignment["assessment_id"]),),
        ).fetchall()

        questions = connection.execute(
            """
            SELECT
                q.question_id,
                q.question_number,
                q.question_type,
                q.max_points,
                q.subskill,
                q.standard_id,
                q.core_idea_id,
                COALESCE(ci.name, q.subskill, 'Not specified') AS core_idea_name,
                s.code AS standard_code
            FROM assessment_questions AS q
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            LEFT JOIN standards AS s
                ON s.standard_id = COALESCE(ci.standard_id, q.standard_id)
            WHERE q.assessment_id = ?
            ORDER BY q.question_number
            """,
            (int(assignment["assessment_id"]),),
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
            FROM cycle_assessment_sections AS cas
            JOIN sections AS se
                ON se.section_id = cas.section_id
            JOIN courses AS co
                ON co.course_id = se.course_id
            JOIN app_users AS u
                ON u.user_id = se.teacher_user_id
            LEFT JOIN section_enrollments AS en
                ON en.section_id = se.section_id
            WHERE cas.cycle_assessment_id = ?
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
                u.display_name,
                se.term_name,
                se.section_name
            """,
            (int(cycle_assessment_id),),
        ).fetchall()

        students: list[Any] = []

        if section_id is not None:
            valid_section = connection.execute(
                """
                SELECT 1
                FROM cycle_assessment_sections
                WHERE cycle_assessment_id = ?
                  AND section_id = ?
                """,
                (int(cycle_assessment_id), int(section_id)),
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
                (int(section_id),),
            ).fetchall()

    result = dict(assignment)
    result["standard_codes"] = [str(row["code"]) for row in standards]
    result["standards"] = [dict(row) for row in standards]
    result["questions"] = [dict(row) for row in questions]
    result["sections"] = [dict(row) for row in sections]
    result["students"] = [dict(row) for row in students]
    return result


def get_result_sections(
    cycle_assessment_id: int,
) -> list[dict[str, Any]]:
    """Return the sections assigned to a cycle-specific CFA use."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                se.section_id,
                se.section_name,
                se.term_name,
                co.course_code,
                co.course_name,
                u.user_id AS teacher_user_id,
                u.display_name AS teacher_name,
                COUNT(DISTINCT en.student_id) AS student_count
            FROM cycle_assessment_sections AS cas
            JOIN sections AS se
                ON se.section_id = cas.section_id
            JOIN courses AS co
                ON co.course_id = se.course_id
            JOIN app_users AS u
                ON u.user_id = se.teacher_user_id
            LEFT JOIN section_enrollments AS en
                ON en.section_id = se.section_id
            WHERE cas.cycle_assessment_id = ?
            GROUP BY
                se.section_id,
                se.section_name,
                se.term_name,
                co.course_code,
                co.course_name,
                u.user_id,
                u.display_name
            ORDER BY
                u.display_name,
                se.term_name,
                se.section_name
            """,
            (int(cycle_assessment_id),),
        ).fetchall()

    return [dict(row) for row in rows]


def get_administrations(
    cycle_assessment_id: int,
) -> list[dict[str, Any]]:
    """Return PRE/POST administrations belonging to one PLC-cycle CFA assignment."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                administration_id,
                assessment_id,
                cycle_assessment_id,
                administration_type,
                administered_on,
                status
            FROM assessment_administrations
            WHERE cycle_assessment_id = ?
            ORDER BY administered_on DESC, administration_id DESC
            """,
            (int(cycle_assessment_id),),
        ).fetchall()

    return [dict(row) for row in rows]


def create_administration(
    cycle_assessment_id: int,
    administration_type: str,
    administered_on: str,
) -> int:
    """Create or return an existing PRE/POST event for this cycle-specific CFA."""
    administration_type = administration_type.upper().strip()

    if administration_type not in {"PRE", "POST"}:
        raise ValueError("Administration type must be PRE or POST.")

    try:
        date.fromisoformat(administered_on)
    except ValueError as error:
        raise ValueError("Administration date must be a valid date.") from error

    assignment = _assignment(cycle_assessment_id)
    if assignment is None:
        raise ValueError("The selected PLC-cycle CFA assignment no longer exists.")

    with connect() as connection:
        duplicate = connection.execute(
            """
            SELECT administration_id
            FROM assessment_administrations
            WHERE cycle_assessment_id = ?
              AND administration_type = ?
              AND administered_on = ?
            """,
            (
                int(cycle_assessment_id),
                administration_type,
                administered_on,
            ),
        ).fetchone()

        if duplicate:
            return int(duplicate["administration_id"])

        cursor = connection.execute(
            """
            INSERT INTO assessment_administrations (
                assessment_id,
                cycle_assessment_id,
                administration_type,
                administered_on,
                status
            )
            VALUES (?, ?, ?, ?, 'Draft')
            """,
            (
                int(assignment["assessment_id"]),
                int(cycle_assessment_id),
                administration_type,
                administered_on,
            ),
        )

        return int(cursor.lastrowid)


def get_saved_scores(
    administration_id: int,
) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                student_id,
                question_id,
                points_earned
            FROM student_item_scores
            WHERE administration_id = ?
            """,
            (int(administration_id),),
        ).fetchall()

    return [dict(row) for row in rows]


def _assigned_student_ids(
    connection,
    cycle_assessment_id: int,
    *,
    section_id: int | None = None,
) -> set[int]:
    if section_id is not None:
        valid = connection.execute(
            """
            SELECT 1
            FROM cycle_assessment_sections
            WHERE cycle_assessment_id = ?
              AND section_id = ?
            """,
            (int(cycle_assessment_id), int(section_id)),
        ).fetchone()

        if valid is None:
            return set()

        rows = connection.execute(
            """
            SELECT DISTINCT student_id
            FROM section_enrollments
            WHERE section_id = ?
            """,
            (int(section_id),),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT DISTINCT en.student_id
            FROM cycle_assessment_sections AS cas
            JOIN section_enrollments AS en
                ON en.section_id = cas.section_id
            WHERE cas.cycle_assessment_id = ?
            """,
            (int(cycle_assessment_id),),
        ).fetchall()

    return {int(row["student_id"]) for row in rows}


def save_scores(
    administration_id: int,
    scores: Sequence[dict[str, Any]],
    *,
    section_id: int | None = None,
    submit: bool = False,
) -> None:
    """Save one section without deleting scores already entered for other sections."""
    with connect() as connection:
        administration = connection.execute(
            """
            SELECT
                administration_id,
                assessment_id,
                cycle_assessment_id
            FROM assessment_administrations
            WHERE administration_id = ?
            """,
            (int(administration_id),),
        ).fetchone()

        if administration is None:
            raise ValueError("The selected administration no longer exists.")

        cycle_assessment_id = administration["cycle_assessment_id"]
        if cycle_assessment_id is None:
            raise ValueError(
                "This administration is not linked to a PLC-cycle CFA assignment."
            )

        question_rows = connection.execute(
            """
            SELECT question_id, max_points
            FROM assessment_questions
            WHERE assessment_id = ?
            ORDER BY question_number
            """,
            (int(administration["assessment_id"]),),
        ).fetchall()

        possible_by_question = {
            int(row["question_id"]): float(row["max_points"])
            for row in question_rows
        }

        allowed_students = _assigned_student_ids(
            connection,
            int(cycle_assessment_id),
            section_id=section_id,
        )

        if not allowed_students:
            raise ValueError(
                "No students are assigned to this CFA section."
                if section_id is not None
                else "No students are assigned to this CFA."
            )

        clean_scores: dict[tuple[int, int], float] = {}

        for item in scores:
            student_id = int(item["student_id"])
            question_id = int(item["question_id"])
            earned = item.get("points_earned")

            if student_id not in allowed_students:
                raise ValueError(
                    f"Student {student_id} is not in this assessment roster."
                )

            if question_id not in possible_by_question:
                raise ValueError(
                    f"Question {question_id} is not part of this assessment."
                )

            if earned is None:
                continue

            earned = float(earned)
            possible = possible_by_question[question_id]

            if not 0 <= earned <= possible:
                raise ValueError(
                    f"Question {question_id} score must be between "
                    f"0 and {possible:g}."
                )

            clean_scores[(student_id, question_id)] = earned

        # Only replace scores for the currently edited roster. This prevents
        # period 2 from wiping period 1 when both sections share one administration.
        placeholders = ",".join("?" for _ in allowed_students)
        connection.execute(
            f"""
            DELETE FROM student_item_scores
            WHERE administration_id = ?
              AND student_id IN ({placeholders})
            """,
            (int(administration_id), *sorted(allowed_students)),
        )

        connection.executemany(
            """
            INSERT INTO student_item_scores (
                administration_id,
                student_id,
                question_id,
                points_earned
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    int(administration_id),
                    student_id,
                    question_id,
                    earned,
                )
                for (student_id, question_id), earned in clean_scores.items()
            ],
        )

        # A click on Submit means this section should be complete.
        expected_section_count = len(allowed_students) * len(possible_by_question)
        if submit and len(clean_scores) != expected_section_count:
            raise ValueError(
                "Complete every student score in this class section before submitting."
            )

        # The shared administration becomes Submitted only once every assigned
        # student's entire item set is complete.
        all_students = _assigned_student_ids(
            connection,
            int(cycle_assessment_id),
        )
        expected_all_count = len(all_students) * len(possible_by_question)

        saved_all_count = connection.execute(
            """
            SELECT COUNT(*) AS score_count
            FROM student_item_scores
            WHERE administration_id = ?
              AND points_earned IS NOT NULL
            """,
            (int(administration_id),),
        ).fetchone()["score_count"]

        all_complete = (
            expected_all_count > 0
            and int(saved_all_count) == expected_all_count
        )

        connection.execute(
            """
            UPDATE assessment_administrations
            SET status = ?
            WHERE administration_id = ?
            """,
            (
                "Submitted" if all_complete else "Draft",
                int(administration_id),
            ),
        )

        if all_complete:
            connection.execute(
                """
                UPDATE assessments
                SET status = 'Results Entered'
                WHERE assessment_id = ?
                """,
                (int(administration["assessment_id"]),),
            )


def get_administration_results(
    administration_id: int,
    *,
    section_id: int | None = None,
) -> dict[str, Any] | None:
    """Return PRE/POST evidence, optionally filtered to one assigned section."""
    with connect() as connection:
        administration = connection.execute(
            """
            SELECT
                ad.administration_id,
                ad.assessment_id,
                ad.cycle_assessment_id,
                ad.administration_type,
                ad.administered_on,
                ad.status,
                a.name AS assessment_name,
                pca.cycle_id,
                c.name AS cycle_name,
                t.name AS team_name
            FROM assessment_administrations AS ad
            JOIN assessments AS a
                ON a.assessment_id = ad.assessment_id
            LEFT JOIN plc_cycle_assessments AS pca
                ON pca.cycle_assessment_id = ad.cycle_assessment_id
            LEFT JOIN plc_cycles AS c
                ON c.cycle_id = pca.cycle_id
            LEFT JOIN plc_teams AS t
                ON t.team_id = c.team_id
            WHERE ad.administration_id = ?
            """,
            (int(administration_id),),
        ).fetchone()

        if administration is None:
            return None

        cycle_assessment_id = administration["cycle_assessment_id"]
        if cycle_assessment_id is None:
            return None

        if section_id is not None:
            valid_section = connection.execute(
                """
                SELECT 1
                FROM cycle_assessment_sections
                WHERE cycle_assessment_id = ?
                  AND section_id = ?
                """,
                (int(cycle_assessment_id), int(section_id)),
            ).fetchone()

            if valid_section is None:
                return None

        questions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                    q.question_id,
                    q.question_number,
                    q.question_type,
                    q.max_points,
                    q.subskill,
                    q.core_idea_id,
                    COALESCE(ci.name, q.subskill, 'Not specified') AS core_idea,
                    COALESCE(ci.standard_id, q.standard_id) AS standard_id,
                    s.code AS standard
                FROM assessment_questions AS q
                LEFT JOIN standard_core_ideas AS ci
                    ON ci.core_idea_id = q.core_idea_id
                LEFT JOIN standards AS s
                    ON s.standard_id = COALESCE(ci.standard_id, q.standard_id)
                WHERE q.assessment_id = ?
                ORDER BY q.question_number
                """,
                (int(administration["assessment_id"]),),
            ).fetchall()
        ]

        if section_id is None:
            allowed_students = _assigned_student_ids(
                connection,
                int(cycle_assessment_id),
            )
        else:
            allowed_students = _assigned_student_ids(
                connection,
                int(cycle_assessment_id),
                section_id=int(section_id),
            )

        if not allowed_students:
            score_rows: list[dict[str, Any]] = []
        else:
            placeholders = ",".join("?" for _ in allowed_students)
            score_rows = [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT
                        sc.student_id,
                        sc.question_id,
                        sc.points_earned,
                        st.student_number,
                        st.last_name || ', ' || st.first_name AS student_name
                    FROM student_item_scores AS sc
                    JOIN students AS st
                        ON st.student_id = sc.student_id
                    WHERE sc.administration_id = ?
                      AND sc.student_id IN ({placeholders})
                    ORDER BY
                        st.last_name,
                        st.first_name,
                        sc.question_id
                    """,
                    (int(administration_id), *sorted(allowed_students)),
                ).fetchall()
            ]

    possible = {
        str(question["question_id"]): float(question["max_points"])
        for question in questions
    }

    student_meta: dict[int, dict[str, Any]] = {}
    scores_by_student: dict[int, dict[str, float | None]] = {}

    for row in score_rows:
        student_id = int(row["student_id"])
        student_meta[student_id] = row
        scores_by_student.setdefault(student_id, {})[
            str(row["question_id"])
        ] = row["points_earned"]

    student_results: list[dict[str, Any]] = []
    for student_id, scores in scores_by_student.items():
        result = calculate_student_result(scores, possible)
        student_results.append(
            {
                "student_id": student_id,
                "student_number": student_meta[student_id]["student_number"],
                "student_name": student_meta[student_id]["student_name"],
                **result,
            }
        )

    student_results.sort(
        key=lambda row: (
            row["percent"] is None,
            row["percent"] if row["percent"] is not None else 101,
            row["student_name"],
        )
    )

    completed = [
        row
        for row in student_results
        if row["percent"] is not None
    ]

    counts = {status: 0 for status in MASTERY_STATUSES}
    for row in completed:
        if row["status"] in counts:
            counts[row["status"]] += 1

    question_performance: list[dict[str, Any]] = []
    for question in questions:
        answered = [
            float(row["points_earned"])
            for row in score_rows
            if int(row["question_id"]) == int(question["question_id"])
            and row["points_earned"] is not None
        ]
        possible_total = len(answered) * float(question["max_points"])
        question_performance.append(
            {
                "question": f"Q{question['question_number']}",
                "subskill": question["subskill"] or "Not specified",
                "core_idea": question["core_idea"] or "Not specified",
                "standard": question["standard"] or "Not specified",
                "students_answered": len(answered),
                "percent": (
                    sum(answered) / possible_total * 100
                    if possible_total
                    else None
                ),
            }
        )

    question_by_id = {
        int(question["question_id"]): question
        for question in questions
    }

    core_totals: dict[str, dict[str, float]] = {}
    standard_totals: dict[str, dict[str, float]] = {}

    for row in score_rows:
        if row["points_earned"] is None:
            continue

        question = question_by_id.get(int(row["question_id"]))
        if question is None:
            continue

        earned = float(row["points_earned"])
        possible_points = float(question["max_points"])

        core_label = question["core_idea"] or "Not specified"
        core = core_totals.setdefault(
            core_label,
            {"earned": 0.0, "possible": 0.0},
        )
        core["earned"] += earned
        core["possible"] += possible_points

        standard_label = question["standard"] or "Not specified"
        standard = standard_totals.setdefault(
            standard_label,
            {"earned": 0.0, "possible": 0.0},
        )
        standard["earned"] += earned
        standard["possible"] += possible_points

    core_idea_performance = sorted(
        (
            {
                "core_idea": label,
                "percent": values["earned"] / values["possible"] * 100,
            }
            for label, values in core_totals.items()
            if values["possible"]
        ),
        key=lambda row: row["percent"],
    )

    standard_performance = sorted(
        (
            {
                "standard": label,
                "percent": values["earned"] / values["possible"] * 100,
            }
            for label, values in standard_totals.items()
            if values["possible"]
        ),
        key=lambda row: row["standard"],
    )

    result = dict(administration)
    result.update(
        {
            "student_results": student_results,
            "completed": len(completed),
            "average": (
                sum(float(row["percent"]) for row in completed) / len(completed)
                if completed
                else None
            ),
            "counts": counts,
            "question_performance": question_performance,
            "core_idea_performance": core_idea_performance,
            "standard_performance": standard_performance,
        }
    )
    return result