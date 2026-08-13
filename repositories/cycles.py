"""Database and calculation helpers for the PLC working dashboard."""

from __future__ import annotations

from datetime import date
from typing import Any

from services.database import connect
from services.scoring import calculate_student_result, classify_score


CYCLE_STAGES = [
    "Assessment",
    "Analysis",
    "Intervention",
    "Reteach",
    "Reassessment",
    "Complete",
]


def list_active_cycles() -> list[dict[str, Any]]:
    """Return active cycles teachers can choose in the PLC workspace."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                c.cycle_id,
                c.name,
                c.team_id,
                t.name AS plc,
                s.code AS standard,
                s.description AS standard_description,
                c.stage,
                c.status,
                c.start_date,
                c.end_date
            FROM plc_cycles AS c
            JOIN plc_teams AS t
                ON t.team_id = c.team_id
            JOIN standards AS s
                ON s.standard_id = c.standard_id
            WHERE c.status != 'Complete'
            ORDER BY c.end_date, c.name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _administration_summary(
    connection,
    administration: dict[str, Any],
) -> dict[str, Any]:
    """Calculate student, question, Core Idea, and standard evidence."""
    questions = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                q.question_id,
                q.question_number,
                q.max_points,
                q.subskill,
                q.standard_id,
                q.core_idea_id,
                COALESCE(ci.name, q.subskill, 'Not specified')
                    AS core_idea_name,
                s.code AS standard_code
            FROM assessment_questions AS q
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            LEFT JOIN standards AS s
                ON s.standard_id = COALESCE(ci.standard_id, q.standard_id)
            WHERE q.assessment_id = ?
            ORDER BY q.question_number
            """,
            (administration["assessment_id"],),
        ).fetchall()
    ]

    score_rows = [
        dict(row)
        for row in connection.execute(
            """
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
            ORDER BY st.last_name, st.first_name
            """,
            (administration["administration_id"],),
        ).fetchall()
    ]

    possible = {
        str(row["question_id"]): float(row["max_points"])
        for row in questions
    }

    scores_by_student: dict[int, dict[str, float | None]] = {}
    student_details: dict[int, dict[str, Any]] = {}

    for row in score_rows:
        student_id = int(row["student_id"])
        student_details[student_id] = {
            "student_number": row["student_number"],
            "student_name": row["student_name"],
        }
        scores_by_student.setdefault(student_id, {})[
            str(row["question_id"])
        ] = row["points_earned"]

    student_results = []
    for student_id, item_scores in scores_by_student.items():
        result = calculate_student_result(item_scores, possible)
        student_results.append(
            {
                "student_id": student_id,
                **student_details[student_id],
                **result,
            }
        )

    completed = [
        row for row in student_results
        if row["percent"] is not None
    ]

    counts = {
        name: 0
        for name in (
            "Mastered",
            "Approaching",
            "Developing",
            "Intensive",
        )
    }
    for row in completed:
        counts[row["status"]] += 1

    question_performance = []
    for question in questions:
        answered = [
            float(row["points_earned"])
            for row in score_rows
            if row["question_id"] == question["question_id"]
            and row["points_earned"] is not None
        ]

        performance = (
            sum(answered)
            / (len(answered) * float(question["max_points"]))
            * 100
            if answered
            else None
        )

        question_performance.append(
            {
                "question": f"Q{question['question_number']}",
                "standard": question["standard_code"],
                "core_idea": question["core_idea_name"],
                "students_answered": len(answered),
                "percent": performance,
            }
        )

    question_by_id = {
        int(row["question_id"]): row
        for row in questions
    }

    core_idea_totals: dict[str, dict[str, float]] = {}
    standard_totals: dict[str, dict[str, float]] = {}

    for row in score_rows:
        if row["points_earned"] is None:
            continue

        question = question_by_id[int(row["question_id"])]
        earned = float(row["points_earned"])
        possible_points = float(question["max_points"])

        core_values = core_idea_totals.setdefault(
            question["core_idea_name"],
            {"earned": 0.0, "possible": 0.0},
        )
        core_values["earned"] += earned
        core_values["possible"] += possible_points

        standard_code = question["standard_code"] or "Not specified"
        standard_values = standard_totals.setdefault(
            standard_code,
            {"earned": 0.0, "possible": 0.0},
        )
        standard_values["earned"] += earned
        standard_values["possible"] += possible_points

    core_idea_performance = [
        {
            "core_idea": name,
            "percent": values["earned"] / values["possible"] * 100,
        }
        for name, values in core_idea_totals.items()
        if values["possible"]
    ]
    core_idea_performance.sort(key=lambda row: row["percent"])

    standard_performance = [
        {
            "standard": code,
            "percent": values["earned"] / values["possible"] * 100,
        }
        for code, values in standard_totals.items()
        if values["possible"]
    ]
    standard_performance.sort(key=lambda row: row["standard"])

    result = dict(administration)
    result.update(
        {
            "student_results": student_results,
            "completed": len(completed),
            "average": (
                sum(row["percent"] for row in completed) / len(completed)
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


def get_cycle_analysis(cycle_id: int) -> dict[str, Any] | None:
    """Return one cycle plus its newest submitted CFA evidence."""
    with connect() as connection:
        cycle = connection.execute(
            """
            SELECT
                c.cycle_id,
                c.name,
                c.team_id,
                c.stage,
                c.status,
                c.start_date,
                c.end_date,
                t.name AS plc,
                t.subject,
                t.grade_level,
                s.code AS standard,
                s.description AS standard_description
            FROM plc_cycles AS c
            JOIN plc_teams AS t
                ON t.team_id = c.team_id
            JOIN standards AS s
                ON s.standard_id = c.standard_id
            WHERE c.cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()

        if cycle is None:
            return None

        cycle_standards = connection.execute(
            """
            SELECT DISTINCT
                s.standard_id,
                s.code,
                s.description
            FROM standards AS s
            WHERE s.standard_id IN (
                SELECT standard_id
                FROM plc_cycle_standards
                WHERE cycle_id = ?

                UNION

                SELECT standard_id
                FROM plc_cycles
                WHERE cycle_id = ?
            )
            ORDER BY s.code
            """,
            (cycle_id, cycle_id),
        ).fetchall()

        administration_rows = connection.execute(
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
            JOIN assessments AS a
                ON a.assessment_id = pca.assessment_id
            WHERE pca.cycle_id = ?
              AND ad.status = 'Submitted'
            ORDER BY
                ad.administered_on DESC,
                ad.administration_id DESC
            LIMIT 2
            """,
            (cycle_id,),
        ).fetchall()

        summaries = [
            _administration_summary(connection, dict(row))
            for row in administration_rows
        ]

    result = dict(cycle)
    result["standards"] = [dict(row) for row in cycle_standards]
    result["latest"] = summaries[0] if summaries else None
    result["previous"] = summaries[1] if len(summaries) > 1 else None
    result["growth_points"] = None

    if result["latest"] and result["previous"]:
        latest_average = result["latest"]["average"]
        previous_average = result["previous"]["average"]
        if latest_average is not None and previous_average is not None:
            result["growth_points"] = latest_average - previous_average

    return result


def get_team_members(cycle_id: int) -> list[dict[str, Any]]:
    """Return teachers/coaches assigned to the PLC team."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT u.user_id, u.display_name, u.role
            FROM plc_cycles AS c
            JOIN plc_team_members AS tm
                ON tm.team_id = c.team_id
            JOIN app_users AS u
                ON u.user_id = tm.user_id
            WHERE c.cycle_id = ?
            ORDER BY u.display_name
            """,
            (cycle_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_cycle_standard_mastery(
    cycle_id: int,
    administration_id: int | None,
) -> list[dict[str, Any]]:
    """Calculate percent of students Mastered for every standard in the cycle."""
    with connect() as connection:
        standard_rows = connection.execute(
            """
            SELECT DISTINCT
                s.standard_id,
                s.code,
                s.description
            FROM standards AS s
            WHERE s.standard_id IN (
                SELECT standard_id
                FROM plc_cycle_standards
                WHERE cycle_id = ?

                UNION

                SELECT standard_id
                FROM plc_cycles
                WHERE cycle_id = ?
            )
            ORDER BY s.code
            """,
            (cycle_id, cycle_id),
        ).fetchall()

        if administration_id is None:
            return [
                {
                    **dict(row),
                    "students_assessed": 0,
                    "students_mastered": 0,
                    "mastery_rate": None,
                    "average_score": None,
                    "status": None,
                }
                for row in standard_rows
            ]

        result_rows = connection.execute(
            """
            SELECT
                sc.student_id,
                COALESCE(ci.standard_id, q.standard_id) AS standard_id,
                SUM(sc.points_earned) AS earned_points,
                SUM(q.max_points) AS possible_points,
                COUNT(DISTINCT sc.question_id) AS answered_questions
            FROM student_item_scores AS sc
            JOIN assessment_questions AS q
                ON q.question_id = sc.question_id
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            WHERE sc.administration_id = ?
              AND sc.points_earned IS NOT NULL
              AND COALESCE(ci.standard_id, q.standard_id) IS NOT NULL
            GROUP BY
                sc.student_id,
                COALESCE(ci.standard_id, q.standard_id)
            """,
            (administration_id,),
        ).fetchall()

        expected_rows = connection.execute(
            """
            SELECT
                COALESCE(ci.standard_id, q.standard_id) AS standard_id,
                COUNT(*) AS question_count
            FROM assessment_questions AS q
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            JOIN assessment_administrations AS ad
                ON ad.assessment_id = q.assessment_id
            WHERE ad.administration_id = ?
              AND COALESCE(ci.standard_id, q.standard_id) IS NOT NULL
            GROUP BY COALESCE(ci.standard_id, q.standard_id)
            """,
            (administration_id,),
        ).fetchall()

    expected_by_standard = {
        int(row["standard_id"]): int(row["question_count"])
        for row in expected_rows
    }

    student_results_by_standard: dict[int, list[dict[str, Any]]] = {}

    for row in result_rows:
        standard_id = int(row["standard_id"])
        expected = expected_by_standard.get(standard_id, 0)

        if int(row["answered_questions"]) != expected:
            continue

        possible = float(row["possible_points"])
        percent = (
            float(row["earned_points"]) / possible * 100
            if possible
            else None
        )

        if percent is None:
            continue

        student_results_by_standard.setdefault(
            standard_id,
            [],
        ).append(
            {
                "percent": percent,
                "status": classify_score(percent),
            }
        )

    output = []
    for standard_row in standard_rows:
        standard = dict(standard_row)
        student_results = student_results_by_standard.get(
            int(standard["standard_id"]),
            [],
        )
        students_assessed = len(student_results)
        students_mastered = sum(
            row["status"] == "Mastered"
            for row in student_results
        )
        mastery_rate = (
            students_mastered / students_assessed * 100
            if students_assessed
            else None
        )
        average_score = (
            sum(row["percent"] for row in student_results)
            / students_assessed
            if students_assessed
            else None
        )

        output.append(
            {
                **standard,
                "students_assessed": students_assessed,
                "students_mastered": students_mastered,
                "mastery_rate": mastery_rate,
                "average_score": average_score,
                "status": (
                    classify_score(mastery_rate)
                    if mastery_rate is not None
                    else None
                ),
            }
        )

    return output


def get_teacher_mastery(
    cycle_id: int,
    administration_id: int | None,
) -> list[dict[str, Any]]:
    """Calculate latest overall CFA mastery for each teacher's assigned section(s)."""
    if administration_id is None:
        return []

    with connect() as connection:
        administration = connection.execute(
            """
            SELECT assessment_id, cycle_assessment_id
            FROM assessment_administrations
            WHERE administration_id = ?
            """,
            (administration_id,),
        ).fetchone()

        if administration is None:
            return []

        question_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM assessment_questions
                WHERE assessment_id = ?
                """,
                (administration["assessment_id"],),
            ).fetchone()[0]
            or 0
        )

        teacher_rows = connection.execute(
            """
            SELECT
                u.user_id AS teacher_user_id,
                u.display_name AS teacher_name,
                COUNT(DISTINCT en.student_id) AS roster_students
            FROM cycle_assessment_sections AS cas
            JOIN sections AS se
                ON se.section_id = cas.section_id
            JOIN app_users AS u
                ON u.user_id = se.teacher_user_id
            LEFT JOIN section_enrollments AS en
                ON en.section_id = se.section_id
            WHERE cas.cycle_assessment_id = ?
            GROUP BY u.user_id, u.display_name
            ORDER BY u.display_name
            """,
            (administration["cycle_assessment_id"],),
        ).fetchall()

        item_rows = connection.execute(
            """
            SELECT DISTINCT
                u.user_id AS teacher_user_id,
                u.display_name AS teacher_name,
                en.student_id,
                sc.question_id,
                sc.points_earned,
                q.max_points
            FROM cycle_assessment_sections AS cas
            JOIN sections AS se
                ON se.section_id = cas.section_id
            JOIN app_users AS u
                ON u.user_id = se.teacher_user_id
            JOIN section_enrollments AS en
                ON en.section_id = se.section_id
            JOIN student_item_scores AS sc
                ON sc.student_id = en.student_id
               AND sc.administration_id = ?
            JOIN assessment_questions AS q
                ON q.question_id = sc.question_id
            WHERE cas.cycle_assessment_id = ?
              AND sc.points_earned IS NOT NULL
            """,
            (
                administration_id,
                administration["cycle_assessment_id"],
            ),
        ).fetchall()

    by_teacher_student: dict[
        tuple[int, int],
        dict[str, Any],
    ] = {}

    for row in item_rows:
        key = (
            int(row["teacher_user_id"]),
            int(row["student_id"]),
        )
        record = by_teacher_student.setdefault(
            key,
            {
                "teacher_user_id": int(row["teacher_user_id"]),
                "teacher_name": row["teacher_name"],
                "student_id": int(row["student_id"]),
                "earned": 0.0,
                "possible": 0.0,
                "answered": 0,
            },
        )
        record["earned"] += float(row["points_earned"])
        record["possible"] += float(row["max_points"])
        record["answered"] += 1

    completed_by_teacher: dict[int, list[dict[str, Any]]] = {}

    for record in by_teacher_student.values():
        if record["answered"] != question_count:
            continue

        percent = (
            record["earned"] / record["possible"] * 100
            if record["possible"]
            else None
        )
        if percent is None:
            continue

        completed_by_teacher.setdefault(
            record["teacher_user_id"],
            [],
        ).append(
            {
                "percent": percent,
                "status": classify_score(percent),
            }
        )

    output = []
    for teacher_row in teacher_rows:
        teacher = dict(teacher_row)
        results = completed_by_teacher.get(
            int(teacher["teacher_user_id"]),
            [],
        )
        students_assessed = len(results)
        students_mastered = sum(
            result["status"] == "Mastered"
            for result in results
        )
        mastery_rate = (
            students_mastered / students_assessed * 100
            if students_assessed
            else None
        )

        output.append(
            {
                **teacher,
                "students_assessed": students_assessed,
                "students_mastered": students_mastered,
                "mastery_rate": mastery_rate,
                "status": (
                    classify_score(mastery_rate)
                    if mastery_rate is not None
                    else None
                ),
            }
        )

    return output


def get_cycle_assessment_evidence(
    cycle_id: int,
) -> list[dict[str, Any]]:
    """Return reusable CFAs assigned to the cycle and their administrations."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                pca.cycle_assessment_id,
                pca.assigned_on,
                pca.status AS assignment_status,
                a.assessment_id,
                a.name AS assessment_name,
                a.assessment_type,
                ad.administration_id,
                ad.administration_type,
                ad.administered_on,
                ad.status AS administration_status,
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
                ) AS section_count
            FROM plc_cycle_assessments AS pca
            JOIN assessments AS a
                ON a.assessment_id = pca.assessment_id
            LEFT JOIN assessment_administrations AS ad
                ON ad.cycle_assessment_id = pca.cycle_assessment_id
            WHERE pca.cycle_id = ?
            ORDER BY
                CASE WHEN ad.administered_on IS NULL THEN 1 ELSE 0 END,
                ad.administered_on DESC,
                a.name
            """,
            (cycle_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_cycle_notes(
    cycle_id: int,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return newest PLC notes with author context."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                n.note_id,
                n.cycle_id,
                n.note_text,
                n.created_at,
                n.updated_at,
                COALESCE(u.display_name, 'PLC Team') AS author_name
            FROM plc_cycle_notes AS n
            LEFT JOIN app_users AS u
                ON u.user_id = n.user_id
            WHERE n.cycle_id = ?
            ORDER BY n.created_at DESC, n.note_id DESC
            LIMIT ?
            """,
            (cycle_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def create_cycle_note(
    *,
    cycle_id: int,
    note_text: str,
    user_id: int | None,
) -> int:
    """Append one meeting note to the cycle's shared history."""
    clean_note = note_text.strip()
    if not clean_note:
        raise ValueError("Enter a note before saving.")

    with connect() as connection:
        if connection.execute(
            "SELECT 1 FROM plc_cycles WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone() is None:
            raise ValueError("The selected PLC cycle no longer exists.")

        if user_id is not None:
            if connection.execute(
                "SELECT 1 FROM app_users WHERE user_id = ?",
                (user_id,),
            ).fetchone() is None:
                raise ValueError("The current user could not be found.")

        cursor = connection.execute(
            """
            INSERT INTO plc_cycle_notes (
                cycle_id,
                user_id,
                note_text
            )
            VALUES (?, ?, ?)
            """,
            (cycle_id, user_id, clean_note),
        )
        return int(cursor.lastrowid)


def list_commitments(cycle_id: int) -> list[dict[str, Any]]:
    """Return teacher commitments with assignee names."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                cm.commitment_id,
                cm.name,
                cm.action_step,
                cm.evidence,
                cm.due_date,
                cm.notes,
                cm.status,
                COALESCE(u.display_name, 'Unassigned')
                    AS assigned_teacher
            FROM commitments AS cm
            LEFT JOIN app_users AS u
                ON u.user_id = cm.assigned_user_id
            WHERE cm.cycle_id = ?
            ORDER BY
                CASE cm.status WHEN 'Open' THEN 0 ELSE 1 END,
                cm.due_date
            """,
            (cycle_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_commitment(
    *,
    cycle_id: int,
    name: str,
    action_step: str,
    evidence: str,
    due_date: str,
    assigned_user_id: int | None,
    notes: str = "",
) -> int:
    """Validate and save a teacher's next-step commitment."""
    clean_name = name.strip()
    clean_action = action_step.strip()

    if not clean_name:
        raise ValueError("Commitment name is required.")
    if not clean_action:
        raise ValueError("Action step is required.")

    try:
        date.fromisoformat(due_date)
    except ValueError as error:
        raise ValueError("Due date must be a valid date.") from error

    with connect() as connection:
        if connection.execute(
            "SELECT 1 FROM plc_cycles WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone() is None:
            raise ValueError("The selected PLC cycle no longer exists.")

        if assigned_user_id is not None:
            is_member = connection.execute(
                """
                SELECT 1
                FROM plc_cycles AS c
                JOIN plc_team_members AS tm
                    ON tm.team_id = c.team_id
                WHERE c.cycle_id = ?
                  AND tm.user_id = ?
                """,
                (cycle_id, assigned_user_id),
            ).fetchone()

            if is_member is None:
                raise ValueError(
                    "Assigned teacher must be a member of this PLC team."
                )

        cursor = connection.execute(
            """
            INSERT INTO commitments (
                cycle_id,
                name,
                action_step,
                evidence,
                due_date,
                assigned_user_id,
                notes,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Open')
            """,
            (
                cycle_id,
                clean_name,
                clean_action,
                evidence.strip() or None,
                due_date,
                assigned_user_id,
                notes.strip() or None,
            ),
        )
        return int(cursor.lastrowid)


def set_commitment_status(
    commitment_id: int,
    status: str,
) -> None:
    """Mark a commitment Open or Complete."""
    if status not in {"Open", "Complete"}:
        raise ValueError(
            "Commitment status must be Open or Complete."
        )

    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE commitments
            SET status = ?
            WHERE commitment_id = ?
            """,
            (status, commitment_id),
        )

        if cursor.rowcount == 0:
            raise ValueError("That commitment no longer exists.")


# Compatibility functions retained for other pages that still import them.
# The redesigned PLC page no longer exposes meeting-step/stage controls.

def get_meeting_progress(cycle_id: int) -> int:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT completed_steps
            FROM cycle_meeting_progress
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()
    return int(row["completed_steps"]) if row else 0


def set_meeting_progress(
    cycle_id: int,
    completed_steps: int,
) -> None:
    if not 0 <= completed_steps <= 5:
        raise ValueError(
            "Completed steps must be between 0 and 5."
        )

    with connect() as connection:
        connection.execute(
            """
            INSERT INTO cycle_meeting_progress (
                cycle_id,
                completed_steps
            )
            VALUES (?, ?)
            ON CONFLICT(cycle_id)
            DO UPDATE SET
                completed_steps = excluded.completed_steps
            """,
            (cycle_id, completed_steps),
        )


def advance_cycle_stage(cycle_id: int) -> str:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT stage
            FROM plc_cycles
            WHERE cycle_id = ?
            """,
            (cycle_id,),
        ).fetchone()

        if row is None:
            raise ValueError(
                "The selected PLC cycle no longer exists."
            )
        if row["stage"] not in CYCLE_STAGES:
            raise ValueError(
                f"Unknown cycle stage: {row['stage']}."
            )

        current_index = CYCLE_STAGES.index(row["stage"])
        new_stage = CYCLE_STAGES[
            min(current_index + 1, len(CYCLE_STAGES) - 1)
        ]
        new_status = (
            "Complete"
            if new_stage == "Complete"
            else "In Progress"
        )

        connection.execute(
            """
            UPDATE plc_cycles
            SET stage = ?, status = ?
            WHERE cycle_id = ?
            """,
            (new_stage, new_status, cycle_id),
        )

    return new_stage
