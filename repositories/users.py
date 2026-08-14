"""User, school, and coach-assignment repository for PLC Intelligence."""

from __future__ import annotations

from typing import Any

from services.database import connect


# Must match the CHECK constraint currently defined on app_users.role.
ROLES = (
    "Teacher",
    "Coach",
    "Principal",
    "District Administrator",
)


def list_schools() -> list[dict[str, Any]]:
    """Return schools available for user assignment."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                school_id,
                school_code,
                school_name
            FROM schools
            ORDER BY school_name
            """
        ).fetchall()

    return [dict(row) for row in rows]


def list_users() -> list[dict[str, Any]]:
    """Return users with a readable list of their assigned schools."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                u.user_id,
                u.email,
                u.display_name,
                u.role,
                u.school_id,
                COALESCE(
                    (
                        SELECT GROUP_CONCAT(school_name, ', ')
                        FROM (
                            SELECT DISTINCT s.school_name AS school_name
                            FROM schools AS s
                            WHERE s.school_id IN (
                                SELECT usa.school_id
                                FROM user_school_assignments AS usa
                                WHERE usa.user_id = u.user_id

                                UNION

                                SELECT u.school_id
                                WHERE u.school_id IS NOT NULL
                            )
                            ORDER BY s.school_name
                        )
                    ),
                    'District-wide / Unassigned'
                ) AS schools
            FROM app_users AS u
            ORDER BY
                CASE u.role
                    WHEN 'Teacher' THEN 1
                    WHEN 'Coach' THEN 2
                    WHEN 'Principal' THEN 3
                    WHEN 'District Administrator' THEN 4
                    ELSE 5
                END,
                u.display_name
            """
        ).fetchall()

    return [dict(row) for row in rows]


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()

    if not normalized:
        raise ValueError("Email is required.")

    if "@" not in normalized:
        raise ValueError("Enter a valid email address.")

    return normalized


def _validate_school_ids(
    connection,
    school_ids: list[int] | tuple[int, ...],
) -> list[int]:
    normalized_ids = sorted(
        {
            int(school_id)
            for school_id in school_ids
        }
    )

    if not normalized_ids:
        return []

    placeholders = ",".join("?" for _ in normalized_ids)

    found_rows = connection.execute(
        f"""
        SELECT school_id
        FROM schools
        WHERE school_id IN ({placeholders})
        """,
        normalized_ids,
    ).fetchall()

    found_ids = {
        int(row["school_id"])
        for row in found_rows
    }

    missing = [
        school_id
        for school_id in normalized_ids
        if school_id not in found_ids
    ]

    if missing:
        raise ValueError(
            "One or more selected schools no longer exist."
        )

    return normalized_ids


def save_user(
    email: str,
    display_name: str,
    role: str,
    school_ids: list[int] | tuple[int, ...],
) -> dict[str, Any]:
    """Create or update one app user and synchronize school assignments."""
    normalized_email = _normalize_email(email)
    clean_name = display_name.strip()

    if not clean_name:
        raise ValueError("Display name is required.")

    if role not in ROLES:
        raise ValueError(
            f"Role must be one of: {', '.join(ROLES)}."
        )

    with connect() as connection:
        normalized_school_ids = _validate_school_ids(
            connection,
            school_ids,
        )

        # District Administrators are intentionally district-wide. Do not
        # attach a primary school or school-assignment rows.
        if role == "District Administrator":
            normalized_school_ids = []

        primary_school_id = (
            normalized_school_ids[0]
            if normalized_school_ids
            else None
        )

        existing = connection.execute(
            """
            SELECT user_id
            FROM app_users
            WHERE email = ?
            """,
            (normalized_email,),
        ).fetchone()

        if existing is None:
            cursor = connection.execute(
                """
                INSERT INTO app_users (
                    email,
                    display_name,
                    role,
                    school_id
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    normalized_email,
                    clean_name,
                    role,
                    primary_school_id,
                ),
            )
            user_id = int(cursor.lastrowid)
        else:
            user_id = int(existing["user_id"])

            connection.execute(
                """
                UPDATE app_users
                SET
                    display_name = ?,
                    role = ?,
                    school_id = ?
                WHERE user_id = ?
                """,
                (
                    clean_name,
                    role,
                    primary_school_id,
                    user_id,
                ),
            )

        connection.execute(
            """
            DELETE FROM user_school_assignments
            WHERE user_id = ?
            """,
            (user_id,),
        )

        for school_id in normalized_school_ids:
            connection.execute(
                """
                INSERT INTO user_school_assignments (
                    user_id,
                    school_id
                )
                VALUES (?, ?)
                """,
                (user_id, school_id),
            )

        # If a user is no longer a Coach, remove obsolete coach assignments.
        if role != "Coach":
            connection.execute(
                """
                DELETE FROM coach_teacher_assignments
                WHERE coach_user_id = ?
                """,
                (user_id,),
            )

        # If a user is no longer a Teacher, remove them from any coach's roster.
        if role != "Teacher":
            connection.execute(
                """
                DELETE FROM coach_teacher_assignments
                WHERE teacher_user_id = ?
                """,
                (user_id,),
            )

        saved = connection.execute(
            """
            SELECT
                user_id,
                email,
                display_name,
                role,
                school_id
            FROM app_users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    return dict(saved)


def get_coach_assignments(
    coach_user_id: int,
) -> list[int]:
    """Return teacher user IDs currently assigned to one coach."""
    with connect() as connection:
        coach = connection.execute(
            """
            SELECT role
            FROM app_users
            WHERE user_id = ?
            """,
            (coach_user_id,),
        ).fetchone()

        if coach is None:
            raise ValueError("Coach could not be found.")

        if coach["role"] != "Coach":
            raise ValueError(
                "Teacher assignments can only be read for a Coach user."
            )

        rows = connection.execute(
            """
            SELECT teacher_user_id
            FROM coach_teacher_assignments
            WHERE coach_user_id = ?
            ORDER BY teacher_user_id
            """,
            (coach_user_id,),
        ).fetchall()

    return [
        int(row["teacher_user_id"])
        for row in rows
    ]


def save_coach_assignments(
    coach_user_id: int,
    teacher_user_ids: list[int] | tuple[int, ...],
) -> None:
    """Replace one coach's teacher assignments."""
    teacher_ids = sorted(
        {
            int(user_id)
            for user_id in teacher_user_ids
        }
    )

    with connect() as connection:
        coach = connection.execute(
            """
            SELECT user_id, role
            FROM app_users
            WHERE user_id = ?
            """,
            (coach_user_id,),
        ).fetchone()

        if coach is None:
            raise ValueError("Coach could not be found.")

        if coach["role"] != "Coach":
            raise ValueError(
                "Assignments can only be saved for a Coach user."
            )

        if teacher_ids:
            placeholders = ",".join("?" for _ in teacher_ids)

            teacher_rows = connection.execute(
                f"""
                SELECT user_id
                FROM app_users
                WHERE role = 'Teacher'
                  AND user_id IN ({placeholders})
                """,
                teacher_ids,
            ).fetchall()

            valid_teacher_ids = {
                int(row["user_id"])
                for row in teacher_rows
            }

            invalid_ids = [
                user_id
                for user_id in teacher_ids
                if user_id not in valid_teacher_ids
            ]

            if invalid_ids:
                raise ValueError(
                    "One or more selected users are not Teacher accounts."
                )

        connection.execute(
            """
            DELETE FROM coach_teacher_assignments
            WHERE coach_user_id = ?
            """,
            (coach_user_id,),
        )

        for teacher_user_id in teacher_ids:
            connection.execute(
                """
                INSERT INTO coach_teacher_assignments (
                    coach_user_id,
                    teacher_user_id
                )
                VALUES (?, ?)
                """,
                (
                    coach_user_id,
                    teacher_user_id,
                ),
            )