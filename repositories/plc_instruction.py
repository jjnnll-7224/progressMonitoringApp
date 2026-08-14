"""Evidence, CFA assignment, and instructional-response helpers for PLC Cycles.

This repository intentionally collapses the old Student Groups -> Interventions
workflow.  Groups are derived from the latest submitted CFA evidence.  The only
new decision teachers save is the instructional response for each mastery band.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Sequence

from services.access_control import get_data_scope, require_team_access
from services.database import connect
from services.scoring import classify_score


MASTERY_STATUSES = (
    "Mastered",
    "Approaching",
    "Developing",
    "Intensive",
)

RESPONSE_DEFAULTS = {
    "Mastered": {
        "label": "Enrichment",
        "strategy": "Extend the learning through transfer, application, or a more complex task.",
    },
    "Approaching": {
        "label": "Brief Reteach",
        "strategy": "Clarify the misconception, model the target again, and give a short supported practice opportunity.",
    },
    "Developing": {
        "label": "Small-Group Instruction",
        "strategy": "Provide explicit small-group instruction with modeling, guided practice, and immediate feedback.",
    },
    "Intensive": {
        "label": "Prerequisite Support",
        "strategy": "Rebuild the prerequisite knowledge needed to access the current standard before returning to grade-level work.",
    },
}

RESPONSE_TYPES = (
    "Enrichment",
    "Brief Reteach",
    "Small-Group Instruction",
    "Prerequisite Support",
    "Peer Support",
    "Targeted Practice",
    "Teacher Modeling",
    "Alternative Representation",
    "Other",
)


def _cycle_row(cycle_id: int) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                c.cycle_id,
                c.team_id,
                c.name,
                c.standard_id,
                t.name AS team_name,
                t.subject,
                t.grade_level,
                s.code AS primary_standard
            FROM plc_cycles AS c
            JOIN plc_teams AS t ON t.team_id = c.team_id
            JOIN standards AS s ON s.standard_id = c.standard_id
            WHERE c.cycle_id = ?
            """,
            (int(cycle_id),),
        ).fetchone()
    if row is None:
        raise ValueError("That PLC cycle no longer exists.")
    return dict(row)


def _require_cycle_access(
    current_user: dict[str, Any] | None,
    cycle_id: int,
) -> dict[str, Any]:
    cycle = _cycle_row(cycle_id)
    require_team_access(current_user, int(cycle["team_id"]))
    return cycle


def _cycle_standard_ids(connection, cycle_id: int) -> list[int]:
    rows = connection.execute(
        """
        SELECT standard_id
        FROM plc_cycle_standards
        WHERE cycle_id = ?
        UNION
        SELECT standard_id
        FROM plc_cycles
        WHERE cycle_id = ?
        """,
        (int(cycle_id), int(cycle_id)),
    ).fetchall()
    return [int(row["standard_id"]) for row in rows]


def list_compatible_cfas(
    cycle_id: int,
    current_user: dict[str, Any] | None,
    search: str = "",
) -> list[dict[str, Any]]:
    """Return reusable CFAs that overlap at least one standard in this cycle."""
    _require_cycle_access(current_user, cycle_id)
    clean_search = search.strip().lower()

    with connect() as connection:
        parameters: list[Any] = [int(cycle_id), int(cycle_id)]
        search_clause = ""
        if clean_search:
            wildcard = f"%{clean_search}%"
            search_clause = """
                AND (
                    LOWER(a.name) LIKE ?
                    OR LOWER(a.assessment_type) LIKE ?
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
                )
            """
            parameters.extend([wildcard, wildcard, wildcard, wildcard])

        rows = connection.execute(
            f"""
            SELECT
                a.assessment_id,
                a.name,
                a.assessment_type,
                a.status,
                (
                    SELECT GROUP_CONCAT(code, ', ')
                    FROM (
                        SELECT s.code AS code
                        FROM assessment_standards AS ast_codes
                        JOIN standards AS s
                            ON s.standard_id = ast_codes.standard_id
                        WHERE ast_codes.assessment_id = a.assessment_id
                        ORDER BY s.code
                    )
                ) AS standards,
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
                    SELECT GROUP_CONCAT(code, ', ')
                    FROM (
                        SELECT DISTINCT s_overlap.code AS code
                        FROM assessment_standards AS ast_overlap
                        JOIN standards AS s_overlap
                            ON s_overlap.standard_id = ast_overlap.standard_id
                        WHERE ast_overlap.assessment_id = a.assessment_id
                          AND ast_overlap.standard_id IN (
                              SELECT standard_id
                              FROM plc_cycle_standards
                              WHERE cycle_id = ?
                              UNION
                              SELECT standard_id
                              FROM plc_cycles
                              WHERE cycle_id = ?
                          )
                        ORDER BY s_overlap.code
                    )
                ) AS overlapping_standards,
                EXISTS (
                    SELECT 1
                    FROM plc_cycle_assessments AS pca_existing
                    WHERE pca_existing.cycle_id = ?
                      AND pca_existing.assessment_id = a.assessment_id
                ) AS already_assigned
            FROM assessments AS a
            WHERE a.status NOT IN ('Archived')
              AND EXISTS (
                  SELECT 1
                  FROM assessment_standards AS ast
                  WHERE ast.assessment_id = a.assessment_id
                    AND ast.standard_id IN (
                        SELECT standard_id
                        FROM plc_cycle_standards
                        WHERE cycle_id = ?
                        UNION
                        SELECT standard_id
                        FROM plc_cycles
                        WHERE cycle_id = ?
                    )
              )
              {search_clause}
            ORDER BY
                CASE a.status
                    WHEN 'Ready' THEN 0
                    WHEN 'Published' THEN 0
                    WHEN 'Results Entered' THEN 1
                    WHEN 'Draft' THEN 2
                    ELSE 3
                END,
                a.name
            """,
            [
                int(cycle_id),
                int(cycle_id),
                int(cycle_id),
                int(cycle_id),
                int(cycle_id),
                *(
                    [f"%{clean_search}%"] * 4
                    if clean_search
                    else []
                ),
            ],
        ).fetchall()

    return [dict(row) for row in rows]


def list_visible_cycle_sections(
    cycle_id: int,
    current_user: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return subject/grade-matched sections inside the signed-in user's scope."""
    cycle = _require_cycle_access(current_user, cycle_id)
    scope = get_data_scope(current_user)

    if scope.visible_user_ids is None:
        user_clause = ""
        user_params: tuple[Any, ...] = ()
    elif not scope.visible_user_ids:
        return []
    else:
        placeholders = ",".join("?" for _ in scope.visible_user_ids)
        user_clause = f"AND se.teacher_user_id IN ({placeholders})"
        user_params = tuple(scope.visible_user_ids)

    # A teacher's DataScope visible_user_ids contains only the teacher, while
    # Coach/Principal scopes contain their assigned/visible teachers.
    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                se.section_id,
                se.section_name,
                se.term_name,
                co.course_name,
                co.course_code,
                u.display_name AS teacher_name,
                COUNT(en.student_id) AS student_count
            FROM sections AS se
            JOIN courses AS co ON co.course_id = se.course_id
            JOIN app_users AS u ON u.user_id = se.teacher_user_id
            LEFT JOIN section_enrollments AS en
                ON en.section_id = se.section_id
            WHERE co.subject = ?
              AND co.grade_level = ?
              {user_clause}
            GROUP BY
                se.section_id,
                se.section_name,
                se.term_name,
                co.course_name,
                co.course_code,
                u.display_name
            ORDER BY u.display_name, se.term_name, se.section_name
            """,
            (
                cycle["subject"],
                cycle["grade_level"],
                *user_params,
            ),
        ).fetchall()

    return [dict(row) for row in rows]


def assign_cfa_to_cycle(
    *,
    current_user: dict[str, Any] | None,
    cycle_id: int,
    assessment_id: int,
    section_ids: Sequence[int],
) -> int:
    """Link an existing reusable CFA directly to the current PLC cycle."""
    cycle = _require_cycle_access(current_user, cycle_id)
    clean_section_ids = list(dict.fromkeys(int(value) for value in section_ids))
    if not clean_section_ids:
        raise ValueError("Select at least one class section for this CFA.")

    visible_sections = {
        int(row["section_id"])
        for row in list_visible_cycle_sections(cycle_id, current_user)
    }
    if not set(clean_section_ids).issubset(visible_sections):
        raise PermissionError(
            "One or more selected sections are outside your visible PLC scope."
        )

    with connect() as connection:
        assessment = connection.execute(
            """
            SELECT assessment_id, name, status
            FROM assessments
            WHERE assessment_id = ?
            """,
            (int(assessment_id),),
        ).fetchone()
        if assessment is None:
            raise ValueError("That CFA no longer exists.")
        if assessment["status"] == "Archived":
            raise ValueError("Archived CFAs cannot be assigned to a PLC cycle.")

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
            (int(assessment_id), int(cycle_id), int(cycle_id)),
        ).fetchone()
        if overlap is None:
            raise ValueError(
                "This CFA does not measure a standard assigned to the PLC cycle."
            )

        existing = connection.execute(
            """
            SELECT cycle_assessment_id
            FROM plc_cycle_assessments
            WHERE cycle_id = ? AND assessment_id = ?
            """,
            (int(cycle_id), int(assessment_id)),
        ).fetchone()

        if existing:
            cycle_assessment_id = int(existing["cycle_assessment_id"])
            connection.execute(
                """
                UPDATE plc_cycle_assessments
                SET status = 'Assigned'
                WHERE cycle_assessment_id = ?
                """,
                (cycle_assessment_id,),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO plc_cycle_assessments (
                    cycle_id,
                    assessment_id,
                    status
                )
                VALUES (?, ?, 'Assigned')
                """,
                (int(cycle_id), int(assessment_id)),
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
            INSERT INTO cycle_assessment_sections (
                cycle_assessment_id,
                section_id
            )
            VALUES (?, ?)
            """,
            [
                (cycle_assessment_id, section_id)
                for section_id in clean_section_ids
            ],
        )

    return cycle_assessment_id


def list_cycle_cfa_assignments(
    cycle_id: int,
    current_user: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    _require_cycle_access(current_user, cycle_id)

    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                pca.cycle_assessment_id,
                pca.assessment_id,
                pca.assigned_on,
                pca.status,
                a.name AS assessment_name,
                a.assessment_type,
                a.status AS assessment_status,
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
                    SELECT COUNT(*)
                    FROM cycle_assessment_sections AS cas
                    WHERE cas.cycle_assessment_id = pca.cycle_assessment_id
                ) AS section_count,
                (
                    SELECT MAX(ad.administered_on)
                    FROM assessment_administrations AS ad
                    WHERE ad.cycle_assessment_id = pca.cycle_assessment_id
                ) AS latest_date
            FROM plc_cycle_assessments AS pca
            JOIN assessments AS a ON a.assessment_id = pca.assessment_id
            WHERE pca.cycle_id = ?
            ORDER BY pca.assigned_on DESC, pca.cycle_assessment_id DESC
            """,
            (int(cycle_id),),
        ).fetchall()

    return [dict(row) for row in rows]


def _administrations(cycle_id: int) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                ad.administration_id,
                ad.assessment_id,
                ad.cycle_assessment_id,
                ad.administration_type,
                ad.administered_on,
                ad.status,
                a.name AS assessment_name
            FROM assessment_administrations AS ad
            JOIN plc_cycle_assessments AS pca
                ON pca.cycle_assessment_id = ad.cycle_assessment_id
            JOIN assessments AS a ON a.assessment_id = pca.assessment_id
            WHERE pca.cycle_id = ?
              AND ad.status = 'Submitted'
            ORDER BY ad.administered_on DESC, ad.administration_id DESC
            LIMIT 2
            """,
            (int(cycle_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def _administration_evidence(
    administration: dict[str, Any],
    cycle_standard_ids: Sequence[int],
) -> dict[str, Any]:
    """Summarize only questions measuring standards in the current PLC cycle."""
    administration_id = int(administration["administration_id"])
    cycle_standard_id_set = {int(value) for value in cycle_standard_ids}

    with connect() as connection:
        question_rows = connection.execute(
            """
            SELECT
                q.question_id,
                q.max_points,
                q.core_idea_id,
                COALESCE(ci.name, q.subskill, 'Not specified') AS core_idea,
                COALESCE(ci.standard_id, q.standard_id) AS standard_id
            FROM assessment_questions AS q
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            WHERE q.assessment_id = ?
            ORDER BY q.question_number
            """,
            (int(administration["assessment_id"]),),
        ).fetchall()

        score_rows = connection.execute(
            """
            SELECT
                sc.student_id,
                sc.question_id,
                sc.points_earned,
                st.student_number,
                st.last_name || ', ' || st.first_name AS student_name
            FROM student_item_scores AS sc
            JOIN students AS st ON st.student_id = sc.student_id
            WHERE sc.administration_id = ?
            ORDER BY st.last_name, st.first_name
            """,
            (administration_id,),
        ).fetchall()

    questions = {
        int(row["question_id"]): dict(row)
        for row in question_rows
        if row["standard_id"] is not None
        and int(row["standard_id"]) in cycle_standard_id_set
    }
    expected_questions = len(questions)

    by_student: dict[int, dict[str, Any]] = {}
    for row in score_rows:
        student_id = int(row["student_id"])
        student = by_student.setdefault(
            student_id,
            {
                "student_id": student_id,
                "student_number": row["student_number"],
                "student_name": row["student_name"],
                "scores": {},
            },
        )
        student["scores"][int(row["question_id"])] = row["points_earned"]

    students: list[dict[str, Any]] = []
    for student in by_student.values():
        answered = [
            (question_id, value)
            for question_id, value in student["scores"].items()
            if value is not None and question_id in questions
        ]
        if len(answered) != expected_questions or expected_questions == 0:
            continue

        earned = sum(float(value) for _, value in answered)
        possible = sum(float(questions[qid]["max_points"]) for qid, _ in answered)
        percent = earned / possible * 100 if possible else None
        if percent is None:
            continue

        students.append(
            {
                "student_id": student["student_id"],
                "student_number": student["student_number"],
                "student_name": student["student_name"],
                "earned": earned,
                "possible": possible,
                "percent": percent,
                "status": classify_score(percent),
                "scores": student["scores"],
            }
        )

    students_by_status: dict[str, list[dict[str, Any]]] = {
        status: [] for status in MASTERY_STATUSES
    }
    for student in students:
        students_by_status[student["status"]].append(student)

    def core_idea_summary(student_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        totals: dict[tuple[int | None, str], dict[str, float]] = {}
        for student in student_rows:
            for question_id, points in student["scores"].items():
                if points is None or question_id not in questions:
                    continue
                question = questions[question_id]
                key = (question["core_idea_id"], question["core_idea"])
                values = totals.setdefault(key, {"earned": 0.0, "possible": 0.0})
                values["earned"] += float(points)
                values["possible"] += float(question["max_points"])

        output = []
        for (core_idea_id, core_idea), values in totals.items():
            if not values["possible"]:
                continue
            output.append(
                {
                    "core_idea_id": core_idea_id,
                    "core_idea": core_idea,
                    "earned": values["earned"],
                    "possible": values["possible"],
                    "percent": values["earned"] / values["possible"] * 100,
                }
            )
        output.sort(key=lambda row: row["percent"])
        return output

    # Keep per-student Core Idea evidence so the view can recompute group
    # performance after a teacher manually moves a student between groups.
    # The student's actual CFA status remains unchanged.
    for student in students:
        student["core_ideas"] = core_idea_summary([student])

    class_core_ideas = core_idea_summary(students)
    groups = []
    for status in MASTERY_STATUSES:
        members = students_by_status[status]
        performance = core_idea_summary(members)
        weakest = performance[0] if performance else None
        groups.append(
            {
                "status": status,
                "count": len(members),
                "students": [
                    {
                        key: value
                        for key, value in student.items()
                        if key != "scores"
                    }
                    for student in members
                ],
                "core_ideas": performance,
                "recommended_response": RESPONSE_DEFAULTS[status]["label"],
                "recommended_strategy": RESPONSE_DEFAULTS[status]["strategy"],
                "weakest_core_idea_id": (
                    weakest["core_idea_id"] if weakest else None
                ),
                "weakest_core_idea": (
                    weakest["core_idea"] if weakest else None
                ),
                "weakest_percent": (
                    weakest["percent"] if weakest else None
                ),
            }
        )

    counts = {status: len(students_by_status[status]) for status in MASTERY_STATUSES}
    mastered = counts["Mastered"]
    completed = len(students)

    return {
        **administration,
        "students": [
            {key: value for key, value in student.items() if key != "scores"}
            for student in students
        ],
        "completed": completed,
        "counts": counts,
        "mastered": mastered,
        "mastery_rate": mastered / completed * 100 if completed else None,
        "core_ideas": class_core_ideas,
        "weakest_core_idea": class_core_ideas[0] if class_core_ideas else None,
        "groups": groups,
    }


def get_cycle_instruction_workspace(
    cycle_id: int,
    current_user: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return assigned CFA, latest evidence, mastery groups, and mastery growth."""
    cycle = _require_cycle_access(current_user, cycle_id)
    assignments = list_cycle_cfa_assignments(cycle_id, current_user)
    with connect() as connection:
        cycle_standard_ids = _cycle_standard_ids(connection, cycle_id)
    administrations = _administrations(cycle_id)
    latest = (
        _administration_evidence(administrations[0], cycle_standard_ids)
        if administrations
        else None
    )
    previous = (
        _administration_evidence(administrations[1], cycle_standard_ids)
        if len(administrations) > 1
        else None
    )

    growth = {
        "previous_mastery_rate": None,
        "latest_mastery_rate": latest["mastery_rate"] if latest else None,
        "mastery_rate_change": None,
        "previous_mastered": None,
        "latest_mastered": latest["mastered"] if latest else None,
        "mastery_count_change": None,
        "newly_mastered_count": None,
    }

    if latest and previous:
        growth["previous_mastery_rate"] = previous["mastery_rate"]
        growth["mastery_rate_change"] = (
            latest["mastery_rate"] - previous["mastery_rate"]
            if latest["mastery_rate"] is not None
            and previous["mastery_rate"] is not None
            else None
        )
        growth["previous_mastered"] = previous["mastered"]
        growth["mastery_count_change"] = latest["mastered"] - previous["mastered"]

        previous_status = {
            int(student["student_id"]): student["status"]
            for student in previous["students"]
        }
        newly_mastered = [
            student
            for student in latest["students"]
            if student["status"] == "Mastered"
            and previous_status.get(int(student["student_id"])) not in (None, "Mastered")
        ]
        growth["newly_mastered_count"] = len(newly_mastered)

    saved_responses = (
        list_instructional_responses(
            cycle_id,
            int(latest["administration_id"]),
            current_user,
        )
        if latest
        else []
    )

    return {
        "cycle": cycle,
        "assignments": assignments,
        "latest": latest,
        "previous": previous,
        "growth": growth,
        "saved_responses": saved_responses,
    }


def save_instructional_responses(
    *,
    current_user: dict[str, Any] | None,
    cycle_id: int,
    source_administration_id: int,
    reassess_date: str | None,
    responses: Sequence[dict[str, Any]],
) -> None:
    """Upsert the team's response plan for each current mastery band."""
    _require_cycle_access(current_user, cycle_id)
    try:
        parsed_reassess = date.fromisoformat(reassess_date) if reassess_date else None
    except ValueError as error:
        raise ValueError("Reassessment date must be a valid date.") from error

    user_id = int(current_user["user_id"]) if current_user else None

    with connect() as connection:
        source = connection.execute(
            """
            SELECT ad.administration_id
            FROM assessment_administrations AS ad
            JOIN plc_cycle_assessments AS pca
                ON pca.cycle_assessment_id = ad.cycle_assessment_id
            WHERE ad.administration_id = ?
              AND pca.cycle_id = ?
              AND ad.status = 'Submitted'
            """,
            (int(source_administration_id), int(cycle_id)),
        ).fetchone()
        if source is None:
            raise ValueError("The source CFA evidence is no longer available for this PLC cycle.")

        valid_student_rows = connection.execute(
            """
            SELECT DISTINCT student_id
            FROM student_item_scores
            WHERE administration_id = ?
              AND points_earned IS NOT NULL
            """,
            (int(source_administration_id),),
        ).fetchall()
        valid_student_ids = {int(row["student_id"]) for row in valid_student_rows}

        for response in responses:
            mastery_status = str(response.get("mastery_status", "")).strip()
            if mastery_status not in MASTERY_STATUSES:
                raise ValueError("Each response must use a valid mastery status.")

            response_type = str(response.get("response_type", "")).strip()
            if not response_type:
                raise ValueError(f"Choose an instructional response for {mastery_status}.")

            strategy = str(response.get("strategy", "")).strip() or None
            focus_text = str(response.get("focus_text", "")).strip() or None
            focus_core_idea_id = response.get("focus_core_idea_id")
            student_ids = list(
                dict.fromkeys(int(value) for value in response.get("student_ids", []))
            )
            if not student_ids:
                continue
            if not set(student_ids).issubset(valid_student_ids):
                raise PermissionError(
                    "One or more response students are not part of the source CFA evidence."
                )

            existing = connection.execute(
                """
                SELECT response_id
                FROM plc_instructional_responses
                WHERE cycle_id = ?
                  AND source_administration_id = ?
                  AND mastery_status = ?
                """,
                (int(cycle_id), int(source_administration_id), mastery_status),
            ).fetchone()

            if existing:
                response_id = int(existing["response_id"])
                connection.execute(
                    """
                    UPDATE plc_instructional_responses
                    SET response_type = ?,
                        focus_core_idea_id = ?,
                        focus_text = ?,
                        strategy = ?,
                        owner_user_id = ?,
                        reassess_date = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE response_id = ?
                    """,
                    (
                        response_type,
                        int(focus_core_idea_id) if focus_core_idea_id is not None else None,
                        focus_text,
                        strategy,
                        user_id,
                        parsed_reassess.isoformat() if parsed_reassess else None,
                        response_id,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO plc_instructional_responses (
                        cycle_id,
                        source_administration_id,
                        mastery_status,
                        response_type,
                        focus_core_idea_id,
                        focus_text,
                        strategy,
                        owner_user_id,
                        reassess_date,
                        created_by_user_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(cycle_id),
                        int(source_administration_id),
                        mastery_status,
                        response_type,
                        int(focus_core_idea_id) if focus_core_idea_id is not None else None,
                        focus_text,
                        strategy,
                        user_id,
                        parsed_reassess.isoformat() if parsed_reassess else None,
                        user_id,
                    ),
                )
                response_id = int(cursor.lastrowid)

            connection.execute(
                """
                DELETE FROM plc_instructional_response_students
                WHERE response_id = ?
                """,
                (response_id,),
            )
            connection.executemany(
                """
                INSERT INTO plc_instructional_response_students (
                    response_id,
                    student_id
                )
                VALUES (?, ?)
                """,
                [(response_id, student_id) for student_id in student_ids],
            )


def list_instructional_responses(
    cycle_id: int,
    source_administration_id: int,
    current_user: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return saved responses plus the students assigned to each response group."""
    _require_cycle_access(current_user, cycle_id)

    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                r.response_id,
                r.mastery_status,
                r.response_type,
                r.focus_core_idea_id,
                COALESCE(ci.name, r.focus_text) AS focus,
                r.strategy,
                r.owner_user_id,
                COALESCE(u.display_name, 'PLC Team') AS owner_name,
                r.reassess_date,
                r.updated_at,
                COUNT(rs.student_id) AS student_count
            FROM plc_instructional_responses AS r
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = r.focus_core_idea_id
            LEFT JOIN app_users AS u ON u.user_id = r.owner_user_id
            LEFT JOIN plc_instructional_response_students AS rs
                ON rs.response_id = r.response_id
            WHERE r.cycle_id = ?
              AND r.source_administration_id = ?
            GROUP BY
                r.response_id,
                r.mastery_status,
                r.response_type,
                r.focus_core_idea_id,
                ci.name,
                r.focus_text,
                r.strategy,
                r.owner_user_id,
                u.display_name,
                r.reassess_date,
                r.updated_at
            ORDER BY
                CASE r.mastery_status
                    WHEN 'Mastered' THEN 1
                    WHEN 'Approaching' THEN 2
                    WHEN 'Developing' THEN 3
                    WHEN 'Intensive' THEN 4
                    ELSE 5
                END
            """,
            (int(cycle_id), int(source_administration_id)),
        ).fetchall()

        membership_rows = connection.execute(
            """
            SELECT
                rs.response_id,
                rs.student_id
            FROM plc_instructional_response_students AS rs
            JOIN plc_instructional_responses AS r
                ON r.response_id = rs.response_id
            WHERE r.cycle_id = ?
              AND r.source_administration_id = ?
            ORDER BY rs.response_id, rs.student_id
            """,
            (int(cycle_id), int(source_administration_id)),
        ).fetchall()

    student_ids_by_response: dict[int, list[int]] = defaultdict(list)
    for row in membership_rows:
        student_ids_by_response[int(row["response_id"])].append(
            int(row["student_id"])
        )

    return [
        {
            **dict(row),
            "student_ids": student_ids_by_response.get(
                int(row["response_id"]),
                [],
            ),
        }
        for row in rows
    ]


def create_or_get_post_reassessment(
    *,
    current_user: dict[str, Any] | None,
    cycle_id: int,
    source_administration_id: int,
    administered_on: str,
) -> int:
    """Create the planned POST administration, or return it if it already exists."""
    _require_cycle_access(current_user, cycle_id)
    try:
        date.fromisoformat(administered_on)
    except ValueError as error:
        raise ValueError("Reassessment date must be valid.") from error

    with connect() as connection:
        source = connection.execute(
            """
            SELECT
                ad.cycle_assessment_id,
                pca.assessment_id
            FROM assessment_administrations AS ad
            JOIN plc_cycle_assessments AS pca
                ON pca.cycle_assessment_id = ad.cycle_assessment_id
            WHERE ad.administration_id = ?
              AND pca.cycle_id = ?
            """,
            (int(source_administration_id), int(cycle_id)),
        ).fetchone()
        if source is None:
            raise ValueError("The source CFA assignment no longer exists.")

        existing = connection.execute(
            """
            SELECT administration_id
            FROM assessment_administrations
            WHERE cycle_assessment_id = ?
              AND administration_type = 'POST'
              AND administered_on = ?
            """,
            (int(source["cycle_assessment_id"]), administered_on),
        ).fetchone()
        if existing:
            return int(existing["administration_id"])

        cursor = connection.execute(
            """
            INSERT INTO assessment_administrations (
                assessment_id,
                cycle_assessment_id,
                administration_type,
                administered_on,
                status
            )
            VALUES (?, ?, 'POST', ?, 'Draft')
            """,
            (
                int(source["assessment_id"]),
                int(source["cycle_assessment_id"]),
                administered_on,
            ),
        )
        return int(cursor.lastrowid)
