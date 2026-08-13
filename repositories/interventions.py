"""Database operations for intervention plans and reassessment setup."""

from __future__ import annotations

from datetime import date
from typing import Any

from repositories.cfa_results import create_administration
from repositories.cycles import get_cycle_analysis, get_team_members, list_active_cycles
from repositories.groups import list_saved_groups
from services.database import connect


INTERVENTION_TYPES = (
    "Small Group",
    "Peer Tutoring",
    "Targeted Practice",
    "Station Rotation",
    "Explicit Instruction",
)
INTERVENTION_STATUSES = ("Planned", "Active", "Complete", "Cancelled")


def list_intervention_cycles() -> list[dict[str, Any]]:
    """Return active cycles that have saved student groups."""
    return [cycle for cycle in list_active_cycles() if list_saved_groups(int(cycle["cycle_id"]))]


def get_intervention_workspace(cycle_id: int) -> dict[str, Any] | None:
    """Return the cycle, standard, available groups, and PLC team members."""
    analysis = get_cycle_analysis(cycle_id)
    if analysis is None:
        return None
    return {
        **analysis,
        "groups": list_saved_groups(cycle_id),
        "team_members": get_team_members(cycle_id),
        "interventions": list_interventions(cycle_id),
    }


def list_interventions(cycle_id: int) -> list[dict[str, Any]]:
    """Return saved intervention plans, including assigned-group context."""
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT i.intervention_id, i.name, i.start_date, i.end_date, i.status,
                   COALESCE(u.display_name, 'Unassigned') AS owner_name,
                   d.intervention_type, d.strategy, d.evidence_to_collect,
                   d.success_criterion, d.notes,
                   g.group_id, g.name AS group_name, g.focus AS group_focus,
                   (SELECT COUNT(*) FROM student_group_members gm
                    WHERE gm.group_id = g.group_id) AS student_count
            FROM interventions i
            LEFT JOIN intervention_details d ON d.intervention_id = i.intervention_id
            LEFT JOIN app_users u ON u.user_id = i.owner_user_id
            LEFT JOIN intervention_assignments ia ON ia.intervention_id = i.intervention_id
            LEFT JOIN student_groups g ON g.group_id = ia.group_id
            WHERE i.cycle_id = ?
            ORDER BY CASE i.status WHEN 'Active' THEN 0 WHEN 'Planned' THEN 1 ELSE 2 END,
                     i.start_date, i.intervention_id
            """,
            (cycle_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_intervention(
    *,
    cycle_id: int,
    group_id: int,
    name: str,
    intervention_type: str,
    owner_user_id: int | None,
    start_date: str,
    end_date: str | None,
    strategy: str = "",
    evidence_to_collect: str = "",
    success_criterion: str = "",
    notes: str = "",
    status: str = "Planned",
) -> int:
    """Create a plan and its group assignment in one database transaction."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Intervention name is required.")
    if intervention_type not in INTERVENTION_TYPES:
        raise ValueError("Choose a valid intervention type.")
    if status not in INTERVENTION_STATUSES:
        raise ValueError("Choose a valid intervention status.")
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date) if end_date else None
    except ValueError as error:
        raise ValueError("Start and end dates must be valid dates.") from error
    if end and end < start:
        raise ValueError("End date cannot be before the start date.")

    with connect() as connection:
        group = connection.execute(
            "SELECT 1 FROM student_groups WHERE group_id = ? AND cycle_id = ?",
            (group_id, cycle_id),
        ).fetchone()
        if group is None:
            raise ValueError("Choose a saved student group from this PLC cycle.")
        if owner_user_id is not None:
            owner = connection.execute(
                """SELECT 1 FROM plc_cycles c
                   JOIN plc_team_members tm ON tm.team_id = c.team_id
                   WHERE c.cycle_id = ? AND tm.user_id = ?""",
                (cycle_id, owner_user_id),
            ).fetchone()
            if owner is None:
                raise ValueError("The owner must belong to this PLC team.")

        cursor = connection.execute(
            """INSERT INTO interventions
               (cycle_id, name, owner_user_id, start_date, end_date, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cycle_id, clean_name, owner_user_id, start_date, end_date or None, status),
        )
        intervention_id = int(cursor.lastrowid)
        connection.execute(
            """INSERT INTO intervention_details
               (intervention_id, intervention_type, strategy, evidence_to_collect,
                success_criterion, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (intervention_id, intervention_type, strategy.strip() or None,
             evidence_to_collect.strip() or None, success_criterion.strip() or None,
             notes.strip() or None),
        )
        connection.execute(
            "INSERT INTO intervention_assignments (intervention_id, group_id) VALUES (?, ?)",
            (intervention_id, group_id),
        )
    return intervention_id


def set_intervention_status(intervention_id: int, status: str) -> None:
    """Update a plan's status without changing its instructional history."""
    if status not in INTERVENTION_STATUSES:
        raise ValueError("Choose a valid intervention status.")
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE interventions SET status = ? WHERE intervention_id = ?",
            (status, intervention_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("That intervention no longer exists.")


def create_post_reassessment(
    cycle_id: int,
    administered_on: str,
) -> int:
    """Create a POST for the cycle's most recently administered CFA assignment."""
    try:
        date.fromisoformat(administered_on)
    except ValueError as error:
        raise ValueError(
            "Reassessment date must be a valid date."
        ) from error

    with connect() as connection:
        assignment = connection.execute(
            """
            SELECT
                pca.cycle_assessment_id,
                MAX(ad.administered_on) AS latest_date
            FROM plc_cycle_assessments AS pca
            LEFT JOIN assessment_administrations AS ad
                ON ad.cycle_assessment_id = pca.cycle_assessment_id
            WHERE pca.cycle_id = ?
            GROUP BY pca.cycle_assessment_id
            ORDER BY
                CASE WHEN MAX(ad.administered_on) IS NULL THEN 1 ELSE 0 END,
                MAX(ad.administered_on) DESC,
                pca.cycle_assessment_id
            LIMIT 1
            """,
            (cycle_id,),
        ).fetchone()

    if assignment is None:
        raise ValueError(
            "This cycle does not have an assigned CFA to reassess."
        )

    return create_administration(
        int(assignment["cycle_assessment_id"]),
        "POST",
        administered_on,
    )