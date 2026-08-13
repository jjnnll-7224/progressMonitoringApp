"""Authentication helpers for the PLC Intelligence demo."""

from __future__ import annotations

from typing import Any

from services.database import connect


def list_demo_users() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                u.user_id,
                u.email,
                u.display_name,
                u.role,
                u.school_id,
                s.school_name
            FROM app_users AS u
            LEFT JOIN schools AS s
                ON s.school_id = u.school_id
            ORDER BY
                CASE u.role
                    WHEN 'Teacher' THEN 1
                    WHEN 'Coach' THEN 2
                    WHEN 'Principal' THEN 3
                    WHEN 'School Administrator' THEN 3
                    WHEN 'District Administrator' THEN 4
                    ELSE 5
                END,
                u.display_name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_demo_user(user_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                u.user_id,
                u.email,
                u.display_name,
                u.role,
                u.school_id,
                s.school_name
            FROM app_users AS u
            LEFT JOIN schools AS s
                ON s.school_id = u.school_id
            WHERE u.user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None
