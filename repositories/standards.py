"""Standards mastery map and longitudinal student-standard evidence.

The source of truth remains question-level CFA scores. The heatmap, learner
pattern, priorities, and Backpack are all derived from submitted evidence.
Nothing here permanently labels a student.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from services.database import connect
from services.scoring import classify_score, percentage_point_growth


MASTERY_STATUSES = ("Mastered", "Approaching", "Developing", "Intensive")
STATUS_PRIORITY = {
    "Intensive": 0,
    "Developing": 1,
    "Approaching": 2,
    "No Evidence": 3,
    "Mastered": 4,
}


def _scope_clause(current_user: dict | None, section_alias: str = "se") -> tuple[str, list[Any]]:
    """Return an RLS-aware section filter using the demo app's existing scope tables."""
    if not current_user:
        return "1 = 0", []

    user_id = int(current_user["user_id"])
    role = current_user["role"]

    if role == "Teacher":
        return f"{section_alias}.teacher_user_id = ?", [user_id]

    if role == "Coach":
        return (
            f"""
            {section_alias}.teacher_user_id IN (
                SELECT teacher_user_id
                FROM coach_teacher_assignments
                WHERE coach_user_id = ?
            )
            """,
            [user_id],
        )

    return (
        f"""
        {section_alias}.school_id IN (
            SELECT school_id
            FROM user_school_assignments
            WHERE user_id = ?
        )
        """,
        [user_id],
    )


def list_visible_sections(current_user: dict | None) -> list[dict[str, Any]]:
    """Return sections the current user can legitimately inspect."""
    scope_sql, params = _scope_clause(current_user, "se")

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                se.section_id,
                se.section_name,
                se.term_name,
                se.school_id,
                sch.school_name,
                se.teacher_user_id,
                u.display_name AS teacher_name,
                co.course_id,
                co.course_code,
                co.course_name,
                co.subject,
                co.grade_level,
                COUNT(DISTINCT en.student_id) AS student_count
            FROM sections AS se
            JOIN courses AS co
                ON co.course_id = se.course_id
            JOIN schools AS sch
                ON sch.school_id = se.school_id
            JOIN app_users AS u
                ON u.user_id = se.teacher_user_id
            LEFT JOIN section_enrollments AS en
                ON en.section_id = se.section_id
            WHERE {scope_sql}
            GROUP BY
                se.section_id,
                se.section_name,
                se.term_name,
                se.school_id,
                sch.school_name,
                se.teacher_user_id,
                u.display_name,
                co.course_id,
                co.course_code,
                co.course_name,
                co.subject,
                co.grade_level
            ORDER BY
                sch.school_name,
                co.grade_level,
                co.subject,
                u.display_name,
                se.section_name
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def _scoped_student_ids(
    current_user: dict | None,
    *,
    subject: str,
    grade_level: str,
    section_id: int | None = None,
) -> list[int]:
    scope_sql, params = _scope_clause(current_user, "se")
    clauses = [scope_sql, "co.subject = ?", "co.grade_level = ?"]
    query_params: list[Any] = [*params, subject, grade_level]

    if section_id is not None:
        clauses.append("se.section_id = ?")
        query_params.append(int(section_id))

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT en.student_id
            FROM sections AS se
            JOIN courses AS co
                ON co.course_id = se.course_id
            JOIN section_enrollments AS en
                ON en.section_id = se.section_id
            WHERE {" AND ".join(clauses)}
            ORDER BY en.student_id
            """,
            query_params,
        ).fetchall()

    return [int(row["student_id"]) for row in rows]


def _latest_standard_results(
    student_ids: list[int],
    *,
    subject: str,
    grade_level: str,
) -> list[dict[str, Any]]:
    """Return each student's newest complete submitted result for each standard."""
    if not student_ids:
        return []

    placeholders = ",".join("?" for _ in student_ids)

    with connect() as connection:
        rows = connection.execute(
            f"""
            WITH question_standard AS (
                SELECT
                    q.question_id,
                    q.assessment_id,
                    q.max_points,
                    COALESCE(ci.standard_id, q.standard_id) AS standard_id
                FROM assessment_questions AS q
                LEFT JOIN standard_core_ideas AS ci
                    ON ci.core_idea_id = q.core_idea_id
                WHERE COALESCE(ci.standard_id, q.standard_id) IS NOT NULL
            ),
            expected AS (
                SELECT
                    ad.administration_id,
                    qs.standard_id,
                    COUNT(*) AS expected_questions
                FROM assessment_administrations AS ad
                JOIN question_standard AS qs
                    ON qs.assessment_id = ad.assessment_id
                JOIN standards AS s
                    ON s.standard_id = qs.standard_id
                WHERE ad.status = 'Submitted'
                  AND s.subject = ?
                  AND s.grade_level = ?
                GROUP BY ad.administration_id, qs.standard_id
            ),
            events AS (
                SELECT
                    sc.student_id,
                    qs.standard_id,
                    ad.administration_id,
                    ad.administered_on,
                    ad.administration_type,
                    a.name AS assessment_name,
                    SUM(sc.points_earned) AS earned_points,
                    SUM(qs.max_points) AS possible_points,
                    COUNT(DISTINCT sc.question_id) AS answered_questions,
                    e.expected_questions
                FROM student_item_scores AS sc
                JOIN assessment_administrations AS ad
                    ON ad.administration_id = sc.administration_id
                JOIN assessments AS a
                    ON a.assessment_id = ad.assessment_id
                JOIN question_standard AS qs
                    ON qs.question_id = sc.question_id
                JOIN expected AS e
                    ON e.administration_id = ad.administration_id
                   AND e.standard_id = qs.standard_id
                WHERE ad.status = 'Submitted'
                  AND sc.points_earned IS NOT NULL
                  AND sc.student_id IN ({placeholders})
                GROUP BY
                    sc.student_id,
                    qs.standard_id,
                    ad.administration_id,
                    ad.administered_on,
                    ad.administration_type,
                    a.name,
                    e.expected_questions
                HAVING COUNT(DISTINCT sc.question_id) = e.expected_questions
            ),
            ranked AS (
                SELECT
                    events.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY student_id, standard_id
                        ORDER BY administered_on DESC, administration_id DESC
                    ) AS rn
                FROM events
            )
            SELECT
                r.student_id,
                r.standard_id,
                s.code AS standard_code,
                s.description AS standard_description,
                s.subject,
                s.grade_level,
                r.administration_id,
                r.administered_on,
                r.administration_type,
                r.assessment_name,
                r.earned_points,
                r.possible_points
            FROM ranked AS r
            JOIN standards AS s
                ON s.standard_id = r.standard_id
            WHERE r.rn = 1
              AND s.subject = ?
              AND s.grade_level = ?
            ORDER BY r.student_id, s.code
            """,
            [subject, grade_level, *student_ids, subject, grade_level],
        ).fetchall()

    output = []
    for row in rows:
        item = dict(row)
        possible = float(item["possible_points"])
        percent = float(item["earned_points"]) / possible * 100 if possible else None
        output.append(
            {
                **item,
                "percent": percent,
                "status": classify_score(percent) if percent is not None else "No Evidence",
            }
        )
    return output


def _latest_core_idea_results(
    student_ids: list[int],
    *,
    subject: str,
    grade_level: str,
) -> list[dict[str, Any]]:
    """Return each student's newest complete result for each structured Core Idea."""
    if not student_ids:
        return []

    placeholders = ",".join("?" for _ in student_ids)

    with connect() as connection:
        rows = connection.execute(
            f"""
            WITH core_questions AS (
                SELECT
                    q.question_id,
                    q.assessment_id,
                    q.max_points,
                    q.core_idea_id,
                    ci.name AS core_idea_name,
                    ci.standard_id
                FROM assessment_questions AS q
                JOIN standard_core_ideas AS ci
                    ON ci.core_idea_id = q.core_idea_id
            ),
            expected AS (
                SELECT
                    ad.administration_id,
                    cq.core_idea_id,
                    COUNT(*) AS expected_questions
                FROM assessment_administrations AS ad
                JOIN core_questions AS cq
                    ON cq.assessment_id = ad.assessment_id
                JOIN standards AS s
                    ON s.standard_id = cq.standard_id
                WHERE ad.status = 'Submitted'
                  AND s.subject = ?
                  AND s.grade_level = ?
                GROUP BY ad.administration_id, cq.core_idea_id
            ),
            events AS (
                SELECT
                    sc.student_id,
                    cq.core_idea_id,
                    cq.core_idea_name,
                    cq.standard_id,
                    ad.administration_id,
                    ad.administered_on,
                    a.name AS assessment_name,
                    SUM(sc.points_earned) AS earned_points,
                    SUM(cq.max_points) AS possible_points,
                    COUNT(DISTINCT sc.question_id) AS answered_questions,
                    e.expected_questions
                FROM student_item_scores AS sc
                JOIN assessment_administrations AS ad
                    ON ad.administration_id = sc.administration_id
                JOIN assessments AS a
                    ON a.assessment_id = ad.assessment_id
                JOIN core_questions AS cq
                    ON cq.question_id = sc.question_id
                JOIN expected AS e
                    ON e.administration_id = ad.administration_id
                   AND e.core_idea_id = cq.core_idea_id
                WHERE ad.status = 'Submitted'
                  AND sc.points_earned IS NOT NULL
                  AND sc.student_id IN ({placeholders})
                GROUP BY
                    sc.student_id,
                    cq.core_idea_id,
                    cq.core_idea_name,
                    cq.standard_id,
                    ad.administration_id,
                    ad.administered_on,
                    a.name,
                    e.expected_questions
                HAVING COUNT(DISTINCT sc.question_id) = e.expected_questions
            ),
            ranked AS (
                SELECT
                    events.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY student_id, core_idea_id
                        ORDER BY administered_on DESC, administration_id DESC
                    ) AS rn
                FROM events
            )
            SELECT
                r.student_id,
                r.core_idea_id,
                r.core_idea_name,
                r.standard_id,
                s.code AS standard_code,
                s.description AS standard_description,
                r.administered_on,
                r.assessment_name,
                r.earned_points,
                r.possible_points
            FROM ranked AS r
            JOIN standards AS s
                ON s.standard_id = r.standard_id
            WHERE r.rn = 1
              AND s.subject = ?
              AND s.grade_level = ?
            ORDER BY r.student_id, s.code, r.core_idea_name
            """,
            [subject, grade_level, *student_ids, subject, grade_level],
        ).fetchall()

    output = []
    for row in rows:
        item = dict(row)
        possible = float(item["possible_points"])
        percent = float(item["earned_points"]) / possible * 100 if possible else None
        output.append(
            {
                **item,
                "percent": percent,
                "status": classify_score(percent) if percent is not None else "No Evidence",
            }
        )
    return output


def _learning_pattern(statuses: list[str]) -> str:
    """Create a temporary evidence pattern instead of a fixed student label."""
    assessed = [status for status in statuses if status != "No Evidence"]
    if not assessed:
        return "Building a Baseline"

    counts = {status: assessed.count(status) for status in MASTERY_STATUSES}
    total = len(assessed)

    if counts["Mastered"] / total >= 0.60:
        return "Ready to Extend"
    if counts["Intensive"] / total >= 0.35:
        return "Building Foundations"
    if (counts["Intensive"] + counts["Developing"]) / total >= 0.50:
        return "Building Consistency"
    if (counts["Mastered"] + counts["Approaching"]) / total >= 0.65:
        return "Near Mastery"
    return "Building Transfer"


def _backpack_summary(core_results: list[dict[str, Any]], limit: int = 3) -> str:
    """Summarize Core Ideas currently packed or being built."""
    if not core_results:
        return "No Core Idea evidence yet"

    candidates = sorted(
        core_results,
        key=lambda row: (
            0 if row["status"] == "Mastered" else 1 if row["status"] == "Approaching" else 2,
            -(row["percent"] or 0),
            row["core_idea_name"],
        ),
    )

    labels = []
    for item in candidates:
        if item["status"] == "Mastered":
            marker = "✓"
        elif item["status"] == "Approaching":
            marker = "↗"
        else:
            continue
        labels.append(f"{item['core_idea_name']} {marker}")
        if len(labels) == limit:
            break

    return " · ".join(labels) if labels else "Skills still developing"


def get_mastery_heatmap(
    current_user: dict | None,
    *,
    subject: str,
    grade_level: str,
    section_id: int | None = None,
    mode: str = "Current Course",
) -> dict[str, Any]:
    """Build the student × standard heatmap plus learner context."""
    student_ids = _scoped_student_ids(
        current_user,
        subject=subject,
        grade_level=grade_level,
        section_id=section_id,
    )
    if not student_ids:
        return {"students": [], "standards": [], "cells": [], "core_ideas": []}

    placeholders = ",".join("?" for _ in student_ids)

    with connect() as connection:
        student_rows = connection.execute(
            f"""
            SELECT
                st.student_id,
                st.student_number,
                st.first_name,
                st.last_name,
                st.last_name || ', ' || st.first_name AS student_name,
                st.grade_level,
                sch.school_name
            FROM students AS st
            JOIN schools AS sch
                ON sch.school_id = st.school_id
            WHERE st.student_id IN ({placeholders})
            ORDER BY st.last_name, st.first_name, st.student_number
            """,
            student_ids,
        ).fetchall()

        standard_rows = connection.execute(
            """
            SELECT standard_id, code, description, subject, grade_level
            FROM standards
            WHERE subject = ? AND grade_level = ?
            ORDER BY code
            """,
            (subject, grade_level),
        ).fetchall()

    standards = [dict(row) for row in standard_rows]
    standard_results = _latest_standard_results(student_ids, subject=subject, grade_level=grade_level)
    core_results = _latest_core_idea_results(student_ids, subject=subject, grade_level=grade_level)

    result_by_key = {
        (int(row["student_id"]), int(row["standard_id"])): row
        for row in standard_results
    }
    core_by_student: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in core_results:
        core_by_student[int(row["student_id"])].append(row)

    cells = []
    students = []

    for student_row in student_rows:
        student = dict(student_row)
        student_id = int(student["student_id"])
        statuses = []

        for standard in standards:
            evidence = result_by_key.get((student_id, int(standard["standard_id"])))
            status = evidence["status"] if evidence else "No Evidence"
            statuses.append(status)
            cells.append(
                {
                    "student_id": student_id,
                    "standard_id": int(standard["standard_id"]),
                    "standard_code": standard["code"],
                    "status": status,
                    "percent": evidence["percent"] if evidence else None,
                    "administered_on": evidence["administered_on"] if evidence else None,
                    "assessment_name": evidence["assessment_name"] if evidence else None,
                }
            )

        students.append(
            {
                **student,
                "archetype": _learning_pattern(statuses),
                "backpack": _backpack_summary(core_by_student[student_id]),
                "mastered_count": statuses.count("Mastered"),
                "approaching_count": statuses.count("Approaching"),
                "developing_count": statuses.count("Developing"),
                "intensive_count": statuses.count("Intensive"),
                "no_evidence_count": statuses.count("No Evidence"),
            }
        )

    if mode == "RISE Prep":
        class_rank = {}
        for standard in standards:
            standard_id = int(standard["standard_id"])
            scores = [
                row["percent"]
                for row in cells
                if row["standard_id"] == standard_id and row["percent"] is not None
            ]
            class_rank[standard_id] = sum(scores) / len(scores) if scores else 101
        standards.sort(key=lambda row: (class_rank[int(row["standard_id"])], row["code"]))

    return {
        "students": students,
        "standards": standards,
        "cells": cells,
        "core_ideas": core_results,
    }


def get_student_mastery_profile(
    student_id: int,
    *,
    subject: str,
    grade_level: str,
) -> dict[str, Any] | None:
    """Return actionable Study Hall / RISE priorities for one student."""
    with connect() as connection:
        student = connection.execute(
            """
            SELECT
                st.student_id,
                st.student_number,
                st.first_name,
                st.last_name,
                st.last_name || ', ' || st.first_name AS student_name,
                st.grade_level,
                sch.school_name
            FROM students AS st
            JOIN schools AS sch
                ON sch.school_id = st.school_id
            WHERE st.student_id = ?
            """,
            (student_id,),
        ).fetchone()

        standard_rows = connection.execute(
            """
            SELECT standard_id, code, description, subject, grade_level
            FROM standards
            WHERE subject = ? AND grade_level = ?
            ORDER BY code
            """,
            (subject, grade_level),
        ).fetchall()

    if student is None:
        return None

    standards = [dict(row) for row in standard_rows]
    results = _latest_standard_results([student_id], subject=subject, grade_level=grade_level)
    core_results = _latest_core_idea_results([student_id], subject=subject, grade_level=grade_level)

    result_by_standard = {int(row["standard_id"]): row for row in results}
    core_by_standard: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in core_results:
        core_by_standard[int(row["standard_id"])].append(row)

    priorities = []
    statuses = []

    for standard in standards:
        standard_id = int(standard["standard_id"])
        evidence = result_by_standard.get(standard_id)
        status = evidence["status"] if evidence else "No Evidence"
        statuses.append(status)

        core_items = core_by_standard.get(standard_id, [])
        weakest_core = (
            min(
                core_items,
                key=lambda row: (
                    row["percent"] is None,
                    row["percent"] if row["percent"] is not None else 101,
                ),
            )
            if core_items
            else None
        )

        priorities.append(
            {
                **standard,
                "status": status,
                "percent": evidence["percent"] if evidence else None,
                "assessment_name": evidence["assessment_name"] if evidence else None,
                "administered_on": evidence["administered_on"] if evidence else None,
                "focus_core_idea": weakest_core["core_idea_name"] if weakest_core else None,
                "focus_core_percent": weakest_core["percent"] if weakest_core else None,
            }
        )

    priorities.sort(
        key=lambda row: (
            STATUS_PRIORITY[row["status"]],
            row["percent"] if row["percent"] is not None else 101,
            row["code"],
        )
    )

    backpack = [
        {
            **row,
            "backpack_status": "Packed" if row["status"] == "Mastered" else "Building",
        }
        for row in sorted(
            core_results,
            key=lambda item: (
                0 if item["status"] == "Mastered" else 1 if item["status"] == "Approaching" else 2,
                -(item["percent"] or 0),
            ),
        )
        if row["status"] in {"Mastered", "Approaching"}
    ]

    return {
        "student": dict(student),
        "archetype": _learning_pattern(statuses),
        "priorities": priorities,
        "backpack": backpack,
        "core_ideas": sorted(
            core_results,
            key=lambda row: (
                STATUS_PRIORITY.get(row["status"], 99),
                row["standard_code"],
                row["core_idea_name"],
            ),
        ),
        "counts": {
            status: statuses.count(status)
            for status in (*MASTERY_STATUSES, "No Evidence")
        },
    }


# Compatibility API retained for other existing pages/tests.
def list_standards(
    *,
    subject: str | None = None,
    grade_level: str | None = None,
) -> list[dict[str, Any]]:
    clauses = [
        "ad.status = 'Submitted'",
        "COALESCE(ci.standard_id, q.standard_id) = s.standard_id",
    ]
    params: list[Any] = []

    if subject:
        clauses.append("s.subject = ?")
        params.append(subject)
    if grade_level:
        clauses.append("s.grade_level = ?")
        params.append(grade_level)

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT
                s.standard_id,
                s.code,
                s.description,
                s.subject,
                s.grade_level
            FROM standards AS s
            JOIN assessment_questions AS q ON 1 = 1
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            JOIN assessment_administrations AS ad
                ON ad.assessment_id = q.assessment_id
            WHERE {" AND ".join(clauses)}
            ORDER BY s.subject, s.grade_level, s.code
            """,
            params,
        ).fetchall()

    return [dict(row) for row in rows]


def get_standard_workspace(standard_id: int) -> dict[str, Any] | None:
    """Retain the old single-standard longitudinal workspace contract."""
    with connect() as connection:
        standard = connection.execute(
            """
            SELECT standard_id, code, description, subject, grade_level
            FROM standards
            WHERE standard_id = ?
            """,
            (standard_id,),
        ).fetchone()

        if standard is None:
            return None

        administrations = connection.execute(
            """
            SELECT DISTINCT
                ad.administration_id,
                ad.assessment_id,
                ad.administration_type,
                ad.administered_on,
                a.name AS assessment_name,
                c.cycle_id,
                c.name AS cycle_name
            FROM assessment_administrations AS ad
            JOIN assessments AS a
                ON a.assessment_id = ad.assessment_id
            JOIN assessment_questions AS q
                ON q.assessment_id = a.assessment_id
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            LEFT JOIN plc_cycle_assessments AS pca
                ON pca.cycle_assessment_id = ad.cycle_assessment_id
            LEFT JOIN plc_cycles AS c
                ON c.cycle_id = pca.cycle_id
            WHERE COALESCE(ci.standard_id, q.standard_id) = ?
              AND ad.status = 'Submitted'
            ORDER BY ad.administered_on, ad.administration_id
            """,
            (standard_id,),
        ).fetchall()

    administration_ids = [int(row["administration_id"]) for row in administrations]
    if not administration_ids:
        return {
            "standard": dict(standard),
            "history": [],
            "matrix": [],
            "counts": {status: 0 for status in MASTERY_STATUSES},
            "latest_average": None,
            "growth_points": None,
        }

    placeholders = ",".join("?" for _ in administration_ids)

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                sc.administration_id,
                st.student_id,
                st.student_number,
                st.last_name || ', ' || st.first_name AS student_name,
                SUM(sc.points_earned) AS earned_points,
                SUM(q.max_points) AS possible_points
            FROM student_item_scores AS sc
            JOIN students AS st
                ON st.student_id = sc.student_id
            JOIN assessment_questions AS q
                ON q.question_id = sc.question_id
            LEFT JOIN standard_core_ideas AS ci
                ON ci.core_idea_id = q.core_idea_id
            WHERE sc.administration_id IN ({placeholders})
              AND COALESCE(ci.standard_id, q.standard_id) = ?
              AND sc.points_earned IS NOT NULL
            GROUP BY
                sc.administration_id,
                st.student_id,
                st.student_number,
                st.last_name,
                st.first_name
            ORDER BY st.last_name, st.first_name
            """,
            [*administration_ids, standard_id],
        ).fetchall()

    by_admin: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        earned = float(row["earned_points"])
        possible = float(row["possible_points"])
        score = earned / possible * 100 if possible else None
        by_admin[int(row["administration_id"])].append(
            {
                "student_id": int(row["student_id"]),
                "student_number": row["student_number"],
                "student_name": row["student_name"],
                "earned": earned,
                "possible": possible,
                "percent": score,
                "status": classify_score(score),
            }
        )

    history = []
    student_history: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for administration_row in administrations:
        administration = dict(administration_row)
        results = by_admin.get(int(administration["administration_id"]), [])
        average = sum(row["percent"] for row in results) / len(results) if results else None
        history.append({**administration, "average": average, "students_assessed": len(results)})
        for result in results:
            student_history[result["student_id"]].append({**result, **administration})

    matrix = []
    for entries in student_history.values():
        entries.sort(key=lambda row: (row["administered_on"], row["administration_id"]))
        latest = entries[-1]
        previous = entries[-2] if len(entries) > 1 else None
        matrix.append(
            {
                **latest,
                "prior_percent": previous["percent"] if previous else None,
                "growth_points": percentage_point_growth(
                    previous["percent"] if previous else None,
                    latest["percent"],
                ),
            }
        )

    matrix.sort(key=lambda row: row["student_name"])
    counts = {status: 0 for status in MASTERY_STATUSES}
    for row in matrix:
        counts[row["status"]] += 1

    latest_average = history[-1]["average"] if history else None
    prior_average = history[-2]["average"] if len(history) > 1 else None

    return {
        "standard": dict(standard),
        "history": history,
        "matrix": matrix,
        "counts": counts,
        "latest_average": latest_average,
        "growth_points": percentage_point_growth(prior_average, latest_average),
    }
