from __future__ import annotations

import sqlite3
from pathlib import Path

from services.standards_repository import StandardsRepository


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "plc_demo.db"
SCHEMA_PATH = ROOT / "data" / "schema.sql"
SEED_PATH = ROOT / "data" / "seed.sql"

# Small additive schemas stay separate during prototype development.  Applying
# them every startup (all use CREATE TABLE IF NOT EXISTS) makes local and
# Streamlit Community Cloud databases converge automatically.
SCHEMA_EXTENSION_PATHS = (
    ROOT / "data" / "weekly_plc_schema.sql",
    ROOT / "data" / "plc_instructional_response_schema.sql",
)

SEED_EXTENSION_PATHS = (
    ROOT / "data" / "assessment_compatibility_backfill.sql",
    ROOT / "data" / "term1_2026_27_seed.sql",
)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _run_sql_file(connection: sqlite3.Connection, path: Path) -> None:
    if path.exists():
        connection.executescript(path.read_text(encoding="utf-8"))


def initialize_demo_database() -> None:
    """Create/upgrade the prototype database and seed demo data when needed."""
    with connect() as connection:
        _run_sql_file(connection, SCHEMA_PATH)

        for extension_path in SCHEMA_EXTENSION_PATHS:
            _run_sql_file(connection, extension_path)

        has_data = connection.execute(
            "SELECT 1 FROM plc_cycles LIMIT 1"
        ).fetchone()

        if not has_data:
            _run_sql_file(connection, SEED_PATH)

        # Extension seeds use INSERT OR IGNORE, so they are safe for an existing
        # local DB and a brand-new ephemeral Community Cloud DB.
        for extension_path in SEED_EXTENSION_PATHS:
            _run_sql_file(connection, extension_path)


def get_standards_repository() -> StandardsRepository:
    """District SQL Server source for Utah standards."""
    return StandardsRepository()


def pacing_guide_filter_options() -> dict:
    return get_standards_repository().get_filter_options()


def pacing_guide_standards(
    *,
    subject: str | None = None,
    course_code: str | None = None,
    grade_level: str | None = None,
    strand_code: str | None = None,
    search_text: str | None = None,
) -> list[dict]:
    return get_standards_repository().get_standards(
        subject=subject,
        course_code=course_code,
        grade_level=grade_level,
        strand_code=strand_code,
        search_text=search_text,
    )


def get_or_create_user(email: str) -> dict:
    """Return an app user for an email, creating a basic prototype user if needed."""
    normalized_email = email.strip().lower()
    if not normalized_email:
        raise ValueError("Enter an email address.")

    with connect() as connection:
        user = connection.execute(
            """
            SELECT user_id, email, display_name, role, school_id
            FROM app_users
            WHERE email = ?
            """,
            (normalized_email,),
        ).fetchone()

        if user is None:
            display_name = (
                normalized_email.split("@", 1)[0]
                .replace(".", " ")
                .title()
            )
            cursor = connection.execute(
                """
                INSERT INTO app_users (
                    email,
                    display_name,
                    role,
                    school_id
                )
                VALUES (?, ?, 'Teacher', NULL)
                """,
                (normalized_email, display_name),
            )
            user = connection.execute(
                """
                SELECT user_id, email, display_name, role, school_id
                FROM app_users
                WHERE user_id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()

    return dict(user)


def dashboard_snapshot() -> dict:
    with connect() as connection:
        assessed = connection.execute(
            "SELECT COUNT(DISTINCT student_id) FROM student_item_scores"
        ).fetchone()[0]
        cycles = connection.execute(
            "SELECT COUNT(*) FROM plc_cycles WHERE status != 'Complete'"
        ).fetchone()[0]
        interventions = connection.execute(
            "SELECT COUNT(*) FROM interventions WHERE status = 'Active'"
        ).fetchone()[0]
        students = connection.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]
        return {
            "students_assessed": assessed,
            "active_cycles": cycles,
            "active_interventions": interventions,
            "students": students,
        }
