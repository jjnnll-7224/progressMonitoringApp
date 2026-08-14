"""Role-based data scoping for PLC Intelligence.

Authentication answers "who is signed in?"
This module answers "what data is that user allowed to see?"

The rest of the app should consume DataScope instead of duplicating role logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.database import connect


DISTRICT_ROLES = {"District Administrator"}
SCHOOL_ADMIN_ROLES = {"Principal", "School Administrator"}
COACH_ROLES = {"Coach"}
TEACHER_ROLES = {"Teacher"}


@dataclass(frozen=True)
class DataScope:
    """Resolved row-level visibility for the signed-in user.

    `None` means unrestricted within that dimension (district-wide).
    An empty tuple means no access.
    """

    role: str
    label: str
    team_ids: tuple[int, ...] | None
    visible_user_ids: tuple[int, ...] | None
    school_ids: tuple[int, ...] | None


def display_role(role: str | None) -> str:
    """Return a clean role label for the UI."""
    if not role:
        return "Unknown"
    if role == "School Administrator":
        return "Principal"
    return role


def _unique_ids(rows, column: str) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                int(row[column])
                for row in rows
                if row[column] is not None
            }
        )
    )


def _school_ids_for_user(
    connection,
    user_id: int,
    fallback_school_id: int | None,
) -> tuple[int, ...]:
    """Resolve explicit school assignments plus legacy app_users.school_id."""
    rows = connection.execute(
        """
        SELECT school_id
        FROM user_school_assignments
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchall()

    school_ids = {
        int(row["school_id"])
        for row in rows
        if row["school_id"] is not None
    }

    if fallback_school_id is not None:
        school_ids.add(int(fallback_school_id))

    return tuple(sorted(school_ids))


def _team_ids_for_schools(connection, school_ids: tuple[int, ...]) -> tuple[int, ...]:
    if not school_ids:
        return ()

    placeholders = ",".join("?" for _ in school_ids)
    rows = connection.execute(
        f"""
        SELECT team_id
        FROM plc_teams
        WHERE school_id IN ({placeholders})
        ORDER BY team_id
        """,
        school_ids,
    ).fetchall()
    return _unique_ids(rows, "team_id")


def _visible_users_for_schools(connection, school_ids: tuple[int, ...]) -> tuple[int, ...]:
    if not school_ids:
        return ()

    placeholders = ",".join("?" for _ in school_ids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT u.user_id
        FROM app_users AS u
        LEFT JOIN user_school_assignments AS usa
            ON usa.user_id = u.user_id
        WHERE usa.school_id IN ({placeholders})
           OR u.school_id IN ({placeholders})
        ORDER BY u.user_id
        """,
        (*school_ids, *school_ids),
    ).fetchall()
    return _unique_ids(rows, "user_id")


def _school_label(connection, school_ids: tuple[int, ...]) -> str:
    if not school_ids:
        return "No school assignment"

    placeholders = ",".join("?" for _ in school_ids)
    rows = connection.execute(
        f"""
        SELECT school_name
        FROM schools
        WHERE school_id IN ({placeholders})
        ORDER BY school_name
        """,
        school_ids,
    ).fetchall()

    names = [str(row["school_name"]) for row in rows if row["school_name"]]
    return ", ".join(names) if names else "Assigned school(s)"


def _teacher_scope(
    connection,
    *,
    user_id: int,
    fallback_school_id: int | None,
) -> DataScope:
    team_rows = connection.execute(
        """
        SELECT team_id
        FROM plc_team_members
        WHERE user_id = ?
        ORDER BY team_id
        """,
        (user_id,),
    ).fetchall()

    team_ids = _unique_ids(team_rows, "team_id")
    school_ids = _school_ids_for_user(connection, user_id, fallback_school_id)

    return DataScope(
        role="Teacher",
        label=(
            f"My PLC teams · {_school_label(connection, school_ids)}"
            if school_ids
            else "My PLC teams"
        ),
        team_ids=team_ids,
        visible_user_ids=(user_id,),
        school_ids=school_ids,
    )


def _coach_scope(
    connection,
    *,
    user_id: int,
    fallback_school_id: int | None,
) -> DataScope:
    teacher_rows = connection.execute(
        """
        SELECT teacher_user_id
        FROM coach_teacher_assignments
        WHERE coach_user_id = ?
        ORDER BY teacher_user_id
        """,
        (user_id,),
    ).fetchall()
    teacher_ids = _unique_ids(teacher_rows, "teacher_user_id")

    team_ids: tuple[int, ...] = ()
    if teacher_ids:
        placeholders = ",".join("?" for _ in teacher_ids)
        team_rows = connection.execute(
            f"""
            SELECT DISTINCT team_id
            FROM plc_team_members
            WHERE user_id IN ({placeholders})
            ORDER BY team_id
            """,
            teacher_ids,
        ).fetchall()
        team_ids = _unique_ids(team_rows, "team_id")

    coach_team_rows = connection.execute(
        """
        SELECT team_id
        FROM plc_team_members
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchall()
    team_ids = tuple(sorted(set(team_ids) | set(_unique_ids(coach_team_rows, "team_id"))))

    school_ids = _school_ids_for_user(connection, user_id, fallback_school_id)

    if not school_ids and team_ids:
        placeholders = ",".join("?" for _ in team_ids)
        school_rows = connection.execute(
            f"""
            SELECT DISTINCT school_id
            FROM plc_teams
            WHERE team_id IN ({placeholders})
            ORDER BY school_id
            """,
            team_ids,
        ).fetchall()
        school_ids = _unique_ids(school_rows, "school_id")

    label = f"{len(teacher_ids)} assigned teacher{'' if len(teacher_ids) == 1 else 's'}"
    if school_ids:
        label += f" · {_school_label(connection, school_ids)}"

    return DataScope(
        role="Coach",
        label=label,
        team_ids=team_ids,
        visible_user_ids=teacher_ids,
        school_ids=school_ids,
    )


def _school_admin_scope(
    connection,
    *,
    user_id: int,
    role: str,
    fallback_school_id: int | None,
) -> DataScope:
    school_ids = _school_ids_for_user(connection, user_id, fallback_school_id)
    team_ids = _team_ids_for_schools(connection, school_ids)
    visible_user_ids = _visible_users_for_schools(connection, school_ids)

    return DataScope(
        role=role,
        label=_school_label(connection, school_ids),
        team_ids=team_ids,
        visible_user_ids=visible_user_ids,
        school_ids=school_ids,
    )


def get_data_scope(current_user: dict[str, Any] | None) -> DataScope:
    """Resolve row-level application scope from the signed-in app user.

    Teacher -> own PLC teams.
    Coach -> teams containing assigned teachers (plus explicit coach memberships).
    Principal/School Administrator -> all teams/users in assigned school(s).
    District Administrator -> district-wide.
    Missing/unknown user -> fail closed with no access.
    """
    if not current_user:
        return DataScope(
            role="Unauthenticated",
            label="No user selected",
            team_ids=(),
            visible_user_ids=(),
            school_ids=(),
        )

    try:
        user_id = int(current_user["user_id"])
    except (KeyError, TypeError, ValueError):
        return DataScope(
            role="Unauthenticated",
            label="Invalid user",
            team_ids=(),
            visible_user_ids=(),
            school_ids=(),
        )

    fallback_school_id = current_user.get("school_id")
    if fallback_school_id is not None:
        try:
            fallback_school_id = int(fallback_school_id)
        except (TypeError, ValueError):
            fallback_school_id = None

    with connect() as connection:
        db_user = connection.execute(
            """
            SELECT user_id, role, school_id
            FROM app_users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        if db_user is None:
            return DataScope(
                role="Unauthenticated",
                label="User no longer exists",
                team_ids=(),
                visible_user_ids=(),
                school_ids=(),
            )

        role = str(db_user["role"])
        if db_user["school_id"] is not None:
            fallback_school_id = int(db_user["school_id"])

        if role in DISTRICT_ROLES:
            return DataScope(
                role=role,
                label="District-wide",
                team_ids=None,
                visible_user_ids=None,
                school_ids=None,
            )

        if role in SCHOOL_ADMIN_ROLES:
            return _school_admin_scope(
                connection,
                user_id=user_id,
                role=role,
                fallback_school_id=fallback_school_id,
            )

        if role in COACH_ROLES:
            return _coach_scope(
                connection,
                user_id=user_id,
                fallback_school_id=fallback_school_id,
            )

        if role in TEACHER_ROLES:
            return _teacher_scope(
                connection,
                user_id=user_id,
                fallback_school_id=fallback_school_id,
            )

    return DataScope(
        role=role or "Unknown",
        label="No access configured",
        team_ids=(),
        visible_user_ids=(),
        school_ids=(),
    )


def can_access_team(current_user: dict[str, Any] | None, team_id: int) -> bool:
    """Return True when the current user may view/mutate the PLC team."""
    scope = get_data_scope(current_user)
    if scope.team_ids is None:
        return True
    return int(team_id) in scope.team_ids


def require_team_access(current_user: dict[str, Any] | None, team_id: int) -> None:
    """Raise a clear error for out-of-scope team mutations."""
    if not can_access_team(current_user, team_id):
        raise PermissionError("You do not have access to that PLC team.")