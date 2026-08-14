"""Database operations for the reusable CFA library and PLC-cycle assignments."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from services.database import connect


def get_standards() -> list[dict[str, Any]]:
    """Return all standards in teacher-friendly order."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT standard_id, code, description, subject, grade_level
            FROM standards
            ORDER BY subject, grade_level, code
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_core_ideas(standard_ids: Sequence[int]) -> list[dict[str, Any]]:
    """Return structured Core Ideas for selected standards."""
    clean_ids = list(dict.fromkeys(int(value) for value in standard_ids))
    if not clean_ids:
        return []

    placeholders = ",".join("?" for _ in clean_ids)
    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                ci.core_idea_id,
                ci.standard_id,
                ci.name,
                ci.description,
                ci.sort_order,
                s.code AS standard_code,
                s.description AS standard_description,
                s.subject,
                s.grade_level
            FROM standard_core_ideas AS ci
            JOIN standards AS s
                ON s.standard_id = ci.standard_id
            WHERE ci.standard_id IN ({placeholders})
            ORDER BY
                s.subject,
                s.grade_level,
                s.code,
                ci.sort_order,
                ci.name
            """,
            clean_ids,
        ).fetchall()
    return [dict(row) for row in rows]


def create_core_idea(
    *,
    standard_id: int,
    name: str,
    description: str = "",
) -> int:
    """Create a reusable Core Idea under one standard."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Core Idea name is required.")

    with connect() as connection:
        if connection.execute(
            "SELECT 1 FROM standards WHERE standard_id = ?",
            (standard_id,),
        ).fetchone() is None:
            raise ValueError("The selected standard no longer exists.")

        existing = connection.execute(
            """
            SELECT core_idea_id
            FROM standard_core_ideas
            WHERE standard_id = ? AND LOWER(name) = LOWER(?)
            """,
            (standard_id, clean_name),
        ).fetchone()
        if existing:
            return int(existing["core_idea_id"])

        next_sort = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(sort_order), 0) + 1
                FROM standard_core_ideas
                WHERE standard_id = ?
                """,
                (standard_id,),
            ).fetchone()[0]
        )

        cursor = connection.execute(
            """
            INSERT INTO standard_core_ideas
                (standard_id, name, description, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (
                standard_id,
                clean_name,
                description.strip() or None,
                next_sort,
            ),
        )
        return int(cursor.lastrowid)


def get_sections_for_user(
    user_id: int | None,
    *,
    subject: str | None = None,
    grade_level: str | None = None,
) -> list[dict[str, Any]]:
    """Return a teacher's sections, optionally limited by subject and grade."""
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

    if subject:
        query += " AND co.subject = ?"
        parameters.append(subject)
    if grade_level:
        query += " AND co.grade_level = ?"
        parameters.append(grade_level)

    query += """
        GROUP BY
            se.section_id,
            se.section_name,
            se.term_name,
            co.course_code,
            co.course_name,
            co.subject,
            co.grade_level
        ORDER BY se.term_name, se.section_name
    """

    with connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def get_sections_for_cycle_user(
    user_id: int | None,
    cycle_id: int,
) -> list[dict[str, Any]]:
    """Return the user's sections aligned to the PLC cycle's team."""
    if user_id is None:
        return []

    with connect() as connection:
        cycle = connection.execute(
            """
            SELECT t.subject, t.grade_level
            FROM plc_cycles AS c
            JOIN plc_teams AS t ON t.team_id = c.team_id
            WHERE c.cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()

    if cycle is None:
        return []

    return get_sections_for_user(
        user_id,
        subject=cycle["subject"],
        grade_level=cycle["grade_level"],
    )


def get_assessments(
    search: str = "",
    status: str = "All statuses",
    assessment_type: str = "All types",
    standard_ids: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    """Return the searchable reusable CFA library."""
    clauses = ["1 = 1"]
    parameters: list[Any] = []

    clean_search = search.strip().lower()
    if clean_search:
        wildcard = f"%{clean_search}%"
        clauses.append(
            """
            (
                LOWER(a.name) LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM assessment_standards AS ast_search
                    JOIN standards AS s_search
                        ON s_search.standard_id = ast_search.standard_id
                    WHERE ast_search.assessment_id = a.assessment_id
                      AND (
                          LOWER(s_search.code) LIKE ?
                          OR LOWER(s_search.description) LIKE ?
                      )
                )
                OR EXISTS (
                    SELECT 1
                    FROM assessment_questions AS q_search
                    LEFT JOIN standard_core_ideas AS ci_search
                        ON ci_search.core_idea_id = q_search.core_idea_id
                    WHERE q_search.assessment_id = a.assessment_id
                      AND LOWER(COALESCE(ci_search.name, q_search.subskill, '')) LIKE ?
                )
            )
            """
        )
        parameters.extend([wildcard, wildcard, wildcard, wildcard])

    if status != "All statuses":
        clauses.append("a.status = ?")
        parameters.append(status)

    if assessment_type != "All types":
        clauses.append("a.assessment_type = ?")
        parameters.append(assessment_type)

    selected_standard_ids = list(
        dict.fromkeys(int(value) for value in (standard_ids or []))
    )
    if selected_standard_ids:
        placeholders = ",".join("?" for _ in selected_standard_ids)
        clauses.append(
            f"""
            EXISTS (
                SELECT 1
                FROM assessment_standards AS ast_filter
                WHERE ast_filter.assessment_id = a.assessment_id
                  AND ast_filter.standard_id IN ({placeholders})
            )
            """
        )
        parameters.extend(selected_standard_ids)

    query = f"""
        SELECT
            a.assessment_id,
            a.name,
            a.assessment_type,
            a.status,
            (
                SELECT GROUP_CONCAT(code, ', ')
                FROM (
                    SELECT s.code AS code
                    FROM assessment_standards AS ast
                    JOIN standards AS s
                        ON s.standard_id = ast.standard_id
                    WHERE ast.assessment_id = a.assessment_id
                    ORDER BY s.code
                )
            ) AS standards,
            (
                SELECT GROUP_CONCAT(cycle_name, ', ')
                FROM (
                    SELECT DISTINCT c.name AS cycle_name
                    FROM plc_cycle_assessments AS pca
                    JOIN plc_cycles AS c
                        ON c.cycle_id = pca.cycle_id
                    WHERE pca.assessment_id = a.assessment_id
                    ORDER BY c.name
                )
            ) AS cycle_names,
            (
                SELECT MAX(ad.administered_on)
                FROM assessment_administrations AS ad
                WHERE ad.assessment_id = a.assessment_id
            ) AS latest_date,
            (
                SELECT COUNT(*)
                FROM assessment_questions AS q
                WHERE q.assessment_id = a.assessment_id
            ) AS question_count,
            (
                SELECT COALESCE(SUM(q.max_points), 0)
                FROM assessment_questions AS q
                WHERE q.assessment_id = a.assessment_id
            ) AS possible_points,
            (
                SELECT COUNT(*)
                FROM plc_cycle_assessments AS pca
                WHERE pca.assessment_id = a.assessment_id
            ) AS cycle_count
        FROM assessments AS a
        WHERE {" AND ".join(clauses)}
        ORDER BY
            CASE a.status
                WHEN 'Ready' THEN 0
                WHEN 'Published' THEN 0
                WHEN 'Draft' THEN 1
                WHEN 'Results Entered' THEN 2
                ELSE 3
            END,
            a.name
    """

    with connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def get_assessment(assessment_id: int) -> dict[str, Any] | None:
    """Return one reusable CFA with standards, Core Ideas, and PLC uses."""
    with connect() as connection:
        assessment = connection.execute(
            """
            SELECT assessment_id, name, assessment_type, status
            FROM assessments
            WHERE assessment_id = ?
            """,
            (assessment_id,),
        ).fetchone()
        if assessment is None:
            return None

        standards = connection.execute(
            """
            SELECT
                s.standard_id,
                s.code,
                s.description,
                s.subject,
                s.grade_level
            FROM assessment_standards AS ast
            JOIN standards AS s
                ON s.standard_id = ast.standard_id
            WHERE ast.assessment_id = ?
            ORDER BY s.code
            """,
            (assessment_id,),
        ).fetchall()

        questions = connection.execute(
            """
            SELECT
                q.question_id,
                q.question_number,
                q.question_type,
                q.max_points,
                q.core_idea_id,
                COALESCE(ci.name, q.subskill, 'Unmapped') AS core_idea_name,
                COALESCE(ci.standard_id, q.standard_id) AS standard_id,
                s.code AS standard_code
            FROM assessment_questions AS q
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            LEFT JOIN standards AS s
                ON s.standard_id = COALESCE(ci.standard_id, q.standard_id)
            WHERE q.assessment_id = ?
            ORDER BY q.question_number
            """,
            (assessment_id,),
        ).fetchall()

        assignment_rows = connection.execute(
            """
            SELECT
                pca.cycle_assessment_id,
                pca.cycle_id,
                pca.assigned_on,
                pca.status,
                c.name AS cycle_name,
                t.name AS team_name,
                t.subject,
                t.grade_level
            FROM plc_cycle_assessments AS pca
            JOIN plc_cycles AS c ON c.cycle_id = pca.cycle_id
            JOIN plc_teams AS t ON t.team_id = c.team_id
            WHERE pca.assessment_id = ?
            ORDER BY c.start_date DESC, c.name
            """,
            (assessment_id,),
        ).fetchall()

        assignments: list[dict[str, Any]] = []
        for assignment_row in assignment_rows:
            assignment = dict(assignment_row)
            section_rows = connection.execute(
                """
                SELECT
                    se.section_id,
                    se.section_name,
                    se.term_name,
                    co.course_name,
                    u.display_name AS teacher_name,
                    COUNT(en.student_id) AS student_count
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
                    co.course_name,
                    u.display_name
                ORDER BY se.section_name
                """,
                (assignment["cycle_assessment_id"],),
            ).fetchall()
            assignment["sections"] = [dict(row) for row in section_rows]
            assignments.append(assignment)

        administration_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM assessment_administrations
            WHERE assessment_id = ?
            """,
            (assessment_id,),
        ).fetchone()[0]

    result = dict(assessment)
    result["standards"] = [dict(row) for row in standards]
    result["questions"] = [dict(row) for row in questions]
    result["assignments"] = assignments
    result["administration_count"] = int(administration_count or 0)
    result["possible_points"] = sum(float(row["max_points"]) for row in questions)
    return result


def create_assessment(
    *,
    name: str,
    standard_ids: Sequence[int],
    assessment_type: str,
    status: str,
    questions: Sequence[dict[str, Any]],
) -> int:
    """Create a reusable CFA; no PLC cycle or section is assigned here."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Assessment name is required.")

    clean_standard_ids = list(dict.fromkeys(int(value) for value in standard_ids))
    if not clean_standard_ids:
        raise ValueError("Select at least one standard.")
    if not questions:
        raise ValueError("Add at least one assessment question.")

    with connect() as connection:
        placeholders = ",".join("?" for _ in clean_standard_ids)
        found_standards = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM standards
                WHERE standard_id IN ({placeholders})
                """,
                clean_standard_ids,
            ).fetchone()[0]
        )
        if found_standards != len(clean_standard_ids):
            raise ValueError("One or more selected standards no longer exist.")

        clean_questions = []
        for number, question in enumerate(questions, start=1):
            question_type = str(question.get("question_type", "")).strip()
            if not question_type:
                raise ValueError(f"Question {number} needs a question type.")

            try:
                max_points = float(question.get("max_points", 0))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Question {number} needs a numeric point value."
                ) from error
            if max_points <= 0:
                raise ValueError(
                    f"Question {number} must be worth more than 0 points."
                )

            core_idea_id = question.get("core_idea_id")
            if core_idea_id is None:
                raise ValueError(
                    f"Question {number} must be mapped to a Core Idea."
                )

            core_idea = connection.execute(
                """
                SELECT core_idea_id, standard_id, name
                FROM standard_core_ideas
                WHERE core_idea_id = ?
                """,
                (int(core_idea_id),),
            ).fetchone()
            if core_idea is None:
                raise ValueError(
                    f"Question {number} uses a Core Idea that no longer exists."
                )

            question_standard_id = int(core_idea["standard_id"])
            if question_standard_id not in clean_standard_ids:
                raise ValueError(
                    f"Question {number}'s Core Idea is not part of the "
                    "assessment's selected standards."
                )

            clean_questions.append(
                (
                    number,
                    question_type,
                    max_points,
                    question_standard_id,
                    str(core_idea["name"]),
                    int(core_idea["core_idea_id"]),
                )
            )

        # cycle_id stays NULL: this is a reusable library asset.
        # standard_id remains populated as a temporary compatibility primary standard.
        cursor = connection.execute(
            """
            INSERT INTO assessments
                (cycle_id, name, standard_id, assessment_type, status)
            VALUES (NULL, ?, ?, ?, ?)
            """,
            (clean_name, clean_standard_ids[0], assessment_type, status),
        )
        assessment_id = int(cursor.lastrowid)

        connection.executemany(
            """
            INSERT INTO assessment_standards
                (assessment_id, standard_id)
            VALUES (?, ?)
            """,
            [
                (assessment_id, selected_standard_id)
                for selected_standard_id in clean_standard_ids
            ],
        )

        connection.executemany(
            """
            INSERT INTO assessment_questions (
                assessment_id,
                question_number,
                question_type,
                max_points,
                standard_id,
                subskill,
                core_idea_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    assessment_id,
                    number,
                    question_type,
                    max_points,
                    question_standard_id,
                    core_idea_name,
                    core_idea_id,
                )
                for (
                    number,
                    question_type,
                    max_points,
                    question_standard_id,
                    core_idea_name,
                    core_idea_id,
                ) in clean_questions
            ],
        )

    return assessment_id


def get_compatible_cycles(
    assessment_id: int,
    *,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return active PLC cycles sharing at least one CFA standard."""
    user_clause = ""
    parameters: list[Any] = [assessment_id, assessment_id]

    if user_id is not None:
        user_clause = """
            AND EXISTS (
                SELECT 1
                FROM plc_team_members AS tm_user
                WHERE tm_user.team_id = c.team_id
                  AND tm_user.user_id = ?
            )
        """
        parameters.append(user_id)

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT
                c.cycle_id,
                c.name,
                c.start_date,
                c.end_date,
                c.stage,
                t.name AS team_name,
                t.subject,
                t.grade_level,
                (
                    SELECT GROUP_CONCAT(code, ', ')
                    FROM (
                        SELECT DISTINCT s_overlap.code AS code
                        FROM assessment_standards AS ast_overlap
                        JOIN standards AS s_overlap
                            ON s_overlap.standard_id = ast_overlap.standard_id
                        WHERE ast_overlap.assessment_id = ?
                          AND ast_overlap.standard_id IN (
                              SELECT pcs.standard_id
                              FROM plc_cycle_standards AS pcs
                              WHERE pcs.cycle_id = c.cycle_id
                              UNION
                              SELECT c.standard_id
                          )
                        ORDER BY s_overlap.code
                    )
                ) AS overlapping_standards
            FROM plc_cycles AS c
            JOIN plc_teams AS t ON t.team_id = c.team_id
            WHERE c.status != 'Complete'
              AND EXISTS (
                  SELECT 1
                  FROM assessment_standards AS ast
                  WHERE ast.assessment_id = ?
                    AND ast.standard_id IN (
                        SELECT pcs.standard_id
                        FROM plc_cycle_standards AS pcs
                        WHERE pcs.cycle_id = c.cycle_id
                        UNION
                        SELECT c.standard_id
                    )
              )
              {user_clause}
            ORDER BY c.start_date DESC, c.name
            """,
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def assign_assessment_to_cycle(
    *,
    assessment_id: int,
    cycle_id: int,
    section_ids: Sequence[int],
) -> int:
    """Assign a reusable CFA to one PLC cycle and participating sections."""
    clean_section_ids = list(dict.fromkeys(int(value) for value in section_ids))
    if not clean_section_ids:
        raise ValueError("Assign at least one class section.")

    with connect() as connection:
        if connection.execute(
            "SELECT 1 FROM assessments WHERE assessment_id = ?",
            (assessment_id,),
        ).fetchone() is None:
            raise ValueError("The selected assessment no longer exists.")

        cycle = connection.execute(
            """
            SELECT c.cycle_id, c.team_id, t.subject, t.grade_level
            FROM plc_cycles AS c
            JOIN plc_teams AS t ON t.team_id = c.team_id
            WHERE c.cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
        if cycle is None:
            raise ValueError("The selected PLC cycle no longer exists.")

        overlap = connection.execute(
            """
            SELECT 1
            FROM assessment_standards AS ast
            WHERE ast.assessment_id = ?
              AND ast.standard_id IN (
                  SELECT standard_id
                  FROM plc_cycle_standards
                  WHERE cycle_id = ?
                  UNION
                  SELECT standard_id
                  FROM plc_cycles
                  WHERE cycle_id = ?
              )
            LIMIT 1
            """,
            (assessment_id, cycle_id, cycle_id),
        ).fetchone()
        if overlap is None:
            raise ValueError(
                "This CFA does not assess any standard in the selected PLC cycle."
            )

        section_placeholders = ",".join("?" for _ in clean_section_ids)
        valid_section_count = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM sections AS se
                JOIN courses AS co ON co.course_id = se.course_id
                WHERE se.section_id IN ({section_placeholders})
                  AND co.subject = ?
                  AND co.grade_level = ?
                """,
                [
                    *clean_section_ids,
                    cycle["subject"],
                    cycle["grade_level"],
                ],
            ).fetchone()[0]
        )
        if valid_section_count != len(clean_section_ids):
            raise ValueError(
                "One or more selected sections do not match the PLC cycle."
            )

        existing = connection.execute(
            """
            SELECT cycle_assessment_id
            FROM plc_cycle_assessments
            WHERE cycle_id = ? AND assessment_id = ?
            """,
            (cycle_id, assessment_id),
        ).fetchone()

        if existing:
            cycle_assessment_id = int(existing["cycle_assessment_id"])
        else:
            cursor = connection.execute(
                """
                INSERT INTO plc_cycle_assessments
                    (cycle_id, assessment_id, status)
                VALUES (?, ?, 'Assigned')
                """,
                (cycle_id, assessment_id),
            )
            cycle_assessment_id = int(cursor.lastrowid)

        connection.execute(
            """
            DELETE FROM cycle_assessment_sections
            WHERE cycle_assessment_id = ?
            """,
            (cycle_assessment_id,),
        )
        connection.executemany(
            """
            INSERT INTO cycle_assessment_sections
                (cycle_assessment_id, section_id)
            VALUES (?, ?)
            """,
            [
                (cycle_assessment_id, section_id)
                for section_id in clean_section_ids
            ],
        )

    return cycle_assessment_id


def get_cycle_assessment_assignments() -> list[dict[str, Any]]:
    """Return every CFA usage instance for score entry and reporting."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                pca.cycle_assessment_id,
                pca.cycle_id,
                pca.assessment_id,
                pca.assigned_on,
                pca.status,
                a.name AS assessment_name,
                a.assessment_type,
                a.status AS assessment_status,
                c.name AS cycle_name,
                t.name AS team_name,
                t.subject,
                t.grade_level,
                (
                    SELECT GROUP_CONCAT(code, ', ')
                    FROM (
                        SELECT s.code AS code
                        FROM assessment_standards AS ast
                        JOIN standards AS s
                            ON s.standard_id = ast.standard_id
                        WHERE ast.assessment_id = a.assessment_id
                        ORDER BY s.code
                    )
                ) AS standards,
                (
                    SELECT MAX(ad.administered_on)
                    FROM assessment_administrations AS ad
                    WHERE ad.cycle_assessment_id = pca.cycle_assessment_id
                ) AS latest_date
            FROM plc_cycle_assessments AS pca
            JOIN assessments AS a
                ON a.assessment_id = pca.assessment_id
            JOIN plc_cycles AS c
                ON c.cycle_id = pca.cycle_id
            JOIN plc_teams AS t
                ON t.team_id = c.team_id
            ORDER BY c.start_date DESC, a.name
            """
        ).fetchall()
    return [dict(row) for row in rows]
