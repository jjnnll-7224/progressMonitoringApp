"""Database helpers for building and saving PLC intervention groups."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from repositories.cycles import get_cycle_analysis, list_active_cycles
from services.database import connect


# These defaults make the grouping rule visible and easy to revise later.
GROUP_DEFAULTS = {
    "Mastered": {"name": "Enrichment", "focus": "Extension and application", "color": "#22C55E"},
    "Approaching": {"name": "Brief Reteaching", "focus": "Clarify the target skill", "color": "#EAB308"},
    "Developing": {"name": "Small-Group Instruction", "focus": "Explicit practice with the standard", "color": "#F97316"},
    "Intensive": {"name": "Prerequisite Support", "focus": "Rebuild prerequisite understanding", "color": "#EF4444"},
}


def list_groupable_cycles() -> list[dict[str, Any]]:
    """Return active cycles that have submitted CFA evidence."""
    cycles = []
    for cycle in list_active_cycles():
        analysis = get_cycle_analysis(int(cycle["cycle_id"]))
        if analysis and analysis["latest"]:
            cycles.append(cycle)
    return cycles


def get_group_workspace(cycle_id: int) -> dict[str, Any] | None:
    """Return the latest CFA results plus any groups already saved for this cycle."""
    analysis = get_cycle_analysis(cycle_id)
    if analysis is None or analysis["latest"] is None:
        return None

    latest = analysis["latest"]
    saved_groups = list_saved_groups(cycle_id)
    assigned_group_by_student = {
        member["student_id"]: group["name"]
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
                    result["student_id"], defaults["name"]
                ),
            }
        )
    return {**analysis, "administration_id": latest["administration_id"], "students": students, "saved_groups": saved_groups}


def list_saved_groups(cycle_id: int) -> list[dict[str, Any]]:
    """Return saved groups with their students in teacher-friendly order."""
    with connect() as connection:
        group_rows = connection.execute(
            """
            SELECT group_id, cycle_id, administration_id, name, focus, group_type
            FROM student_groups
            WHERE cycle_id = ?
            ORDER BY group_id
            """,
            (cycle_id,),
        ).fetchall()
        member_rows = connection.execute(
            """
            SELECT gm.group_id, st.student_id,
                   st.last_name || ', ' || st.first_name AS student_name
            FROM student_group_members AS gm
            JOIN students AS st ON st.student_id = gm.student_id
            JOIN student_groups AS sg ON sg.group_id = gm.group_id
            WHERE sg.cycle_id = ?
            ORDER BY st.last_name, st.first_name
            """,
            (cycle_id,),
        ).fetchall()

    members_by_group: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in member_rows:
        members_by_group[int(row["group_id"])].append(dict(row))
    return [{**dict(row), "members": members_by_group[int(row["group_id"])]} for row in group_rows]


def save_groups(*, cycle_id: int, administration_id: int, groups: list[dict[str, Any]]) -> None:
    """Replace one cycle's saved grouping in a single transaction.

    The view validates that every assessed student is assigned once before this
    function runs. Keeping the write transactional prevents partial group sets.
    """
    clean_groups = []
    seen_students: set[int] = set()
    for group in groups:
        name = str(group.get("name", "")).strip()
        focus = str(group.get("focus", "")).strip()
        students = [int(student_id) for student_id in group.get("student_ids", [])]
        if not name:
            raise ValueError("Every group needs a name.")
        if not students:
            continue  # Empty draft groups are not stored.
        duplicates = seen_students.intersection(students)
        if duplicates:
            raise ValueError("A student cannot be assigned to more than one group.")
        seen_students.update(students)
        clean_groups.append({"name": name, "focus": focus or None, "student_ids": students})
    if not clean_groups:
        raise ValueError("Assign at least one student before saving groups.")

    with connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM assessment_administrations WHERE administration_id = ?",
            (administration_id,),
        ).fetchone()
        if exists is None:
            raise ValueError("The selected CFA administration no longer exists.")

        # Delete only this cycle's saved grouping, then recreate it atomically.
        connection.execute(
            "DELETE FROM student_group_members WHERE group_id IN (SELECT group_id FROM student_groups WHERE cycle_id = ?)",
            (cycle_id,),
        )
        connection.execute("DELETE FROM student_groups WHERE cycle_id = ?", (cycle_id,))
        for group in clean_groups:
            cursor = connection.execute(
                """
                INSERT INTO student_groups (cycle_id, administration_id, name, focus)
                VALUES (?, ?, ?, ?)
                """,
                (cycle_id, administration_id, group["name"], group["focus"]),
            )
            group_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO student_group_members (group_id, student_id) VALUES (?, ?)",
                [(group_id, student_id) for student_id in group["student_ids"]],
            )