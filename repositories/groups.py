"""Role-aware database helpers for PLC instructional groups."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from repositories.cycles import get_cycle_analysis, list_active_cycles
from services.access_control import get_data_scope, require_team_access
from services.database import connect


GROUP_DEFAULTS = {
    "Mastered": {
        "name": "Enrichment",
        "focus": "Extension and application",
        "color": "#1f77b4",
    },
    "Approaching": {
        "name": "Brief Reteaching",
        "focus": "Clarify the target skill",
        "color": "#eadc19",
    },
    "Developing": {
        "name": "Small-Group Instruction",
        "focus": "Explicit practice with the standard",
        "color": "#ff7f0e",
    },
    "Intensive": {
        "name": "Prerequisite Support",
        "focus": "Rebuild prerequisite understanding",
        "color": "#d62728",
    },
}


def _require_cycle_access(
    current_user: dict[str, Any] | None,
    cycle_id: int,
) -> int:
    """Return the cycle's team_id after enforcing row-level access."""
    with connect() as connection:
        row = connection.execute(
            """
            SELECT team_id
            FROM plc_cycles
            WHERE cycle_id = ?
            """,
            (int(cycle_id),),
        ).fetchone()

    if row is None:
        raise ValueError("That PLC cycle no longer exists.")

    team_id = int(row["team_id"])
    require_team_access(current_user, team_id)
    return team_id


def list_groupable_cycles(
    current_user: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return visible active cycles that have submitted CFA evidence."""
    scope = get_data_scope(current_user)
    cycles: list[dict[str, Any]] = []

    for cycle in list_active_cycles():
        team_id = int(cycle["team_id"])

        if (
            scope.team_ids is not None
            and team_id not in scope.team_ids
        ):
            continue

        analysis = get_cycle_analysis(int(cycle["cycle_id"]))
        if analysis and analysis["latest"]:
            cycles.append(cycle)

    return cycles


def get_group_workspace(
    cycle_id: int,
    current_user: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return latest CFA results and saved groups for an authorized cycle."""
    _require_cycle_access(current_user, cycle_id)

    analysis = get_cycle_analysis(int(cycle_id))
    if analysis is None or analysis["latest"] is None:
        return None

    latest = analysis["latest"]
    saved_groups = list_saved_groups(
        int(cycle_id),
        current_user,
    )

    assigned_group_by_student = {
        int(member["student_id"]): group["name"]
        for group in saved_groups
        for member in group["members"]
    }

    students = []
    for result in latest["student_results"]:
        defaults = GROUP_DEFAULTS[result["status"]]
        students.append(
            {
                **result,
                "suggested_group": defaults["name"],
                "assigned_group": assigned_group_by_student.get(
                    int(result["student_id"]),
                    defaults["name"],
                ),
            }
        )

    return {
        **analysis,
        "administration_id": latest["administration_id"],
        "students": students,
        "saved_groups": saved_groups,
    }


def list_saved_groups(
    cycle_id: int,
    current_user: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return saved groups only after validating cycle access."""
    _require_cycle_access(current_user, cycle_id)

    with connect() as connection:
        group_rows = connection.execute(
            """
            SELECT
                group_id,
                cycle_id,
                administration_id,
                name,
                focus,
                group_type
            FROM student_groups
            WHERE cycle_id = ?
            ORDER BY group_id
            """,
            (int(cycle_id),),
        ).fetchall()

        member_rows = connection.execute(
            """
            SELECT
                gm.group_id,
                st.student_id,
                st.last_name || ', ' || st.first_name
                    AS student_name
            FROM student_group_members AS gm
            JOIN students AS st
                ON st.student_id = gm.student_id
            JOIN student_groups AS sg
                ON sg.group_id = gm.group_id
            WHERE sg.cycle_id = ?
            ORDER BY st.last_name, st.first_name
            """,
            (int(cycle_id),),
        ).fetchall()

    members_by_group: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(list)

    for row in member_rows:
        members_by_group[
            int(row["group_id"])
        ].append(dict(row))

    return [
        {
            **dict(row),
            "members": members_by_group[
                int(row["group_id"])
            ],
        }
        for row in group_rows
    ]


def save_groups(
    *,
    current_user: dict[str, Any] | None,
    cycle_id: int,
    administration_id: int,
    groups: list[dict[str, Any]],
) -> None:
    """Replace one authorized cycle's grouping in one transaction."""
    _require_cycle_access(current_user, cycle_id)

    clean_groups = []
    seen_students: set[int] = set()

    for group in groups:
        name = str(group.get("name", "")).strip()
        focus = str(group.get("focus", "")).strip()
        students = [
            int(student_id)
            for student_id in group.get(
                "student_ids",
                [],
            )
        ]

        if not name:
            raise ValueError("Every group needs a name.")

        if not students:
            continue

        duplicates = seen_students.intersection(students)
        if duplicates:
            raise ValueError(
                "A student cannot be assigned to more than one group."
            )

        seen_students.update(students)
        clean_groups.append(
            {
                "name": name,
                "focus": focus or None,
                "student_ids": students,
            }
        )

    if not clean_groups:
        raise ValueError(
            "Assign at least one student before saving groups."
        )

    with connect() as connection:
        administration = connection.execute(
            """
            SELECT ad.administration_id
            FROM assessment_administrations AS ad
            JOIN plc_cycle_assessments AS pca
                ON pca.cycle_assessment_id =
                   ad.cycle_assessment_id
            WHERE ad.administration_id = ?
              AND pca.cycle_id = ?
            """,
            (
                int(administration_id),
                int(cycle_id),
            ),
        ).fetchone()

        if administration is None:
            raise ValueError(
                "That CFA administration does not belong to this PLC cycle."
            )

        # Every saved student must actually have evidence in this
        # administration. This prevents cross-roster ID injection.
        if seen_students:
            placeholders = ",".join(
                "?"
                for _ in seen_students
            )
            valid_rows = connection.execute(
                f"""
                SELECT DISTINCT student_id
                FROM student_item_scores
                WHERE administration_id = ?
                  AND student_id IN ({placeholders})
                """,
                (
                    int(administration_id),
                    *sorted(seen_students),
                ),
            ).fetchall()

            valid_students = {
                int(row["student_id"])
                for row in valid_rows
            }

            if valid_students != seen_students:
                raise ValueError(
                    "One or more students are not part of this CFA administration."
                )

        connection.execute(
            """
            DELETE FROM student_group_members
            WHERE group_id IN (
                SELECT group_id
                FROM student_groups
                WHERE cycle_id = ?
            )
            """,
            (int(cycle_id),),
        )

        connection.execute(
            """
            DELETE FROM student_groups
            WHERE cycle_id = ?
            """,
            (int(cycle_id),),
        )

        for group in clean_groups:
            cursor = connection.execute(
                """
                INSERT INTO student_groups (
                    cycle_id,
                    administration_id,
                    name,
                    focus
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    int(cycle_id),
                    int(administration_id),
                    group["name"],
                    group["focus"],
                ),
            )

            group_id = int(cursor.lastrowid)

            connection.executemany(
                """
                INSERT INTO student_group_members (
                    group_id,
                    student_id
                )
                VALUES (?, ?)
                """,
                [
                    (
                        group_id,
                        student_id,
                    )
                    for student_id
                    in group["student_ids"]
                ],
            )