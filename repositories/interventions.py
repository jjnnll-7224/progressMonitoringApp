"""Role-aware intervention plans and reassessment setup."""

from __future__ import annotations

from datetime import date
from typing import Any

from repositories.cfa_results import create_administration
from repositories.cycles import (
    get_cycle_analysis,
    get_team_members,
    list_active_cycles,
)
from repositories.groups import list_saved_groups
from services.access_control import (
    get_data_scope,
    require_team_access,
)
from services.database import connect


INTERVENTION_TYPES = (
    "Small Group",
    "Peer Tutoring",
    "Targeted Practice",
    "Station Rotation",
    "Explicit Instruction",
)

INTERVENTION_STATUSES = (
    "Planned",
    "Active",
    "Complete",
    "Cancelled",
)


def _require_cycle_access(
    current_user: dict[str, Any] | None,
    cycle_id: int,
) -> int:
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
        raise ValueError(
            "That PLC cycle no longer exists."
        )

    team_id = int(row["team_id"])
    require_team_access(
        current_user,
        team_id,
    )
    return team_id


def list_intervention_cycles(
    current_user: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return visible active cycles with saved student groups."""
    scope = get_data_scope(current_user)
    output = []

    for cycle in list_active_cycles():
        team_id = int(cycle["team_id"])

        if (
            scope.team_ids is not None
            and team_id not in scope.team_ids
        ):
            continue

        if list_saved_groups(
            int(cycle["cycle_id"]),
            current_user,
        ):
            output.append(cycle)

    return output


def get_intervention_workspace(
    cycle_id: int,
    current_user: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return an intervention workspace only for an authorized cycle."""
    _require_cycle_access(
        current_user,
        cycle_id,
    )

    analysis = get_cycle_analysis(
        int(cycle_id)
    )

    if analysis is None:
        return None

    return {
        **analysis,
        "groups": list_saved_groups(
            int(cycle_id),
            current_user,
        ),
        "team_members": get_team_members(
            int(cycle_id)
        ),
        "interventions": list_interventions(
            int(cycle_id),
            current_user,
        ),
    }


def list_interventions(
    cycle_id: int,
    current_user: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return saved intervention plans after validating cycle access."""
    _require_cycle_access(
        current_user,
        cycle_id,
    )

    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                i.intervention_id,
                i.name,
                i.start_date,
                i.end_date,
                i.status,
                COALESCE(
                    u.display_name,
                    'Unassigned'
                ) AS owner_name,
                d.intervention_type,
                d.strategy,
                d.evidence_to_collect,
                d.success_criterion,
                d.notes,
                g.group_id,
                g.name AS group_name,
                g.focus AS group_focus,
                (
                    SELECT COUNT(*)
                    FROM student_group_members AS gm
                    WHERE gm.group_id = g.group_id
                ) AS student_count
            FROM interventions AS i
            LEFT JOIN intervention_details AS d
                ON d.intervention_id =
                   i.intervention_id
            LEFT JOIN app_users AS u
                ON u.user_id =
                   i.owner_user_id
            LEFT JOIN intervention_assignments AS ia
                ON ia.intervention_id =
                   i.intervention_id
            LEFT JOIN student_groups AS g
                ON g.group_id = ia.group_id
            WHERE i.cycle_id = ?
            ORDER BY
                CASE i.status
                    WHEN 'Active' THEN 0
                    WHEN 'Planned' THEN 1
                    ELSE 2
                END,
                i.start_date,
                i.intervention_id
            """,
            (int(cycle_id),),
        ).fetchall()

    return [dict(row) for row in rows]


def create_intervention(
    *,
    current_user: dict[str, Any] | None,
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
    """Create an intervention only inside an authorized PLC cycle."""
    _require_cycle_access(
        current_user,
        cycle_id,
    )

    clean_name = name.strip()

    if not clean_name:
        raise ValueError(
            "Intervention name is required."
        )

    if intervention_type not in INTERVENTION_TYPES:
        raise ValueError(
            "Choose a valid intervention type."
        )

    if status not in INTERVENTION_STATUSES:
        raise ValueError(
            "Choose a valid intervention status."
        )

    try:
        start = date.fromisoformat(start_date)
        end = (
            date.fromisoformat(end_date)
            if end_date
            else None
        )
    except ValueError as error:
        raise ValueError(
            "Start and end dates must be valid dates."
        ) from error

    if end and end < start:
        raise ValueError(
            "End date cannot be before the start date."
        )

    with connect() as connection:
        group = connection.execute(
            """
            SELECT 1
            FROM student_groups
            WHERE group_id = ?
              AND cycle_id = ?
            """,
            (
                int(group_id),
                int(cycle_id),
            ),
        ).fetchone()

        if group is None:
            raise ValueError(
                "Choose a saved student group from this PLC cycle."
            )

        if owner_user_id is not None:
            owner = connection.execute(
                """
                SELECT 1
                FROM plc_cycles AS c
                JOIN plc_team_members AS tm
                    ON tm.team_id = c.team_id
                WHERE c.cycle_id = ?
                  AND tm.user_id = ?
                """,
                (
                    int(cycle_id),
                    int(owner_user_id),
                ),
            ).fetchone()

            if owner is None:
                raise ValueError(
                    "The owner must belong to this PLC team."
                )

        cursor = connection.execute(
            """
            INSERT INTO interventions (
                cycle_id,
                name,
                owner_user_id,
                start_date,
                end_date,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(cycle_id),
                clean_name,
                owner_user_id,
                start_date,
                end_date or None,
                status,
            ),
        )

        intervention_id = int(
            cursor.lastrowid
        )

        connection.execute(
            """
            INSERT INTO intervention_details (
                intervention_id,
                intervention_type,
                strategy,
                evidence_to_collect,
                success_criterion,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                intervention_id,
                intervention_type,
                strategy.strip() or None,
                evidence_to_collect.strip()
                or None,
                success_criterion.strip()
                or None,
                notes.strip() or None,
            ),
        )

        connection.execute(
            """
            INSERT INTO intervention_assignments (
                intervention_id,
                group_id
            )
            VALUES (?, ?)
            """,
            (
                intervention_id,
                int(group_id),
            ),
        )

    return intervention_id


def set_intervention_status(
    intervention_id: int,
    status: str,
    current_user: dict[str, Any] | None,
) -> None:
    """Update status only after resolving the intervention's PLC team."""
    if status not in INTERVENTION_STATUSES:
        raise ValueError(
            "Choose a valid intervention status."
        )

    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                i.cycle_id,
                c.team_id
            FROM interventions AS i
            JOIN plc_cycles AS c
                ON c.cycle_id = i.cycle_id
            WHERE i.intervention_id = ?
            """,
            (int(intervention_id),),
        ).fetchone()

    if row is None:
        raise ValueError(
            "That intervention no longer exists."
        )

    require_team_access(
        current_user,
        int(row["team_id"]),
    )

    with connect() as connection:
        connection.execute(
            """
            UPDATE interventions
            SET status = ?
            WHERE intervention_id = ?
            """,
            (
                status,
                int(intervention_id),
            ),
        )


def create_post_reassessment(
    cycle_id: int,
    administered_on: str,
    current_user: dict[str, Any] | None,
) -> int:
    """Create a POST only for an authorized cycle."""
    _require_cycle_access(
        current_user,
        cycle_id,
    )

    try:
        date.fromisoformat(
            administered_on
        )
    except ValueError as error:
        raise ValueError(
            "Reassessment date must be a valid date."
        ) from error

    with connect() as connection:
        assignment = connection.execute(
            """
            SELECT
                pca.cycle_assessment_id,
                MAX(ad.administered_on)
                    AS latest_date
            FROM plc_cycle_assessments AS pca
            LEFT JOIN assessment_administrations AS ad
                ON ad.cycle_assessment_id =
                   pca.cycle_assessment_id
            WHERE pca.cycle_id = ?
            GROUP BY
                pca.cycle_assessment_id
            ORDER BY
                CASE
                    WHEN MAX(ad.administered_on)
                         IS NULL
                    THEN 1
                    ELSE 0
                END,
                MAX(ad.administered_on) DESC,
                pca.cycle_assessment_id
            LIMIT 1
            """,
            (int(cycle_id),),
        ).fetchone()

    if assignment is None:
        raise ValueError(
            "This cycle does not have an assigned CFA to reassess."
        )

    return create_administration(
        int(
            assignment[
                "cycle_assessment_id"
            ]
        ),
        "POST",
        administered_on,
    )