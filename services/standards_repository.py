from __future__ import annotations

import os
import streamlit as st
from contextlib import closing

# import pyodbc


class StandardsRepository:
    """Read Utah standards and course mappings from the district SQL Server."""

    def __init__(self, connection_string: str | None = None) -> None:
        self.connection_string = (
            connection_string
            or st.secrets("connection_string")
        )

        if not self.connection_string:
            raise RuntimeError(
                "Missing DISTRICT_SQL_CONNECTION_STRING. "
                "Add the district SQL Server connection string to your environment or secrets."
            )

    def _connect(self) -> pyodbc.Connection:
        return pyodbc.connect(self.connection_string)

    def get_filter_options(self) -> dict:
        """Return dropdown options for the pacing-guide builder."""
        with closing(self._connect()) as connection:
            cursor = connection.cursor()

            subjects = cursor.execute(
                """
                SELECT DISTINCT subject
                FROM dbo.Standards
                ORDER BY subject
                """
            ).fetchall()

            courses = cursor.execute(
                """
                SELECT DISTINCT
                    cs.course_code,
                    cs.course_name
                FROM dbo.CourseStandards AS cs
                INNER JOIN dbo.Standards AS s
                    ON s.standard_code = cs.standard_code
                ORDER BY cs.course_name
                """
            ).fetchall()

        return {
            "subjects": [row.subject for row in subjects],
            "courses": [
                {
                    "course_code": row.course_code,
                    "course_name": row.course_name,
                }
                for row in courses
            ],
        }

    def get_standards(
        self,
        *,
        subject: str | None = None,
        course_code: str | None = None,
        grade_level: str | None = None,
        strand_code: str | None = None,
        search_text: str | None = None,
    ) -> list[dict]:
        """Return standards available to select for a pacing-guide unit."""
        filters = ["s.is_instructional = 1"]
        params: list[str] = []

        if subject:
            filters.append("s.subject = ?")
            params.append(subject)

        if course_code:
            filters.append("cs.course_code = ?")
            params.append(course_code)

        if grade_level:
            filters.append("s.grade_level = ?")
            params.append(grade_level)

        if strand_code:
            filters.append("s.strand_code = ?")
            params.append(strand_code)

        if search_text:
            filters.append(
                "(s.standard_code LIKE ? OR s.standard_text LIKE ?)"
            )
            search = f"%{search_text.strip()}%"
            params.extend([search, search])

        where_clause = " AND ".join(filters)

        query = f"""
            SELECT DISTINCT
                s.standard_code,
                s.subject,
                s.grade_level,
                s.grade_sort,
                s.strand_code,
                s.strand_name,
                s.standard_text,
                cs.course_code,
                cs.course_name
            FROM dbo.Standards AS s
            LEFT JOIN dbo.CourseStandards AS cs
                ON cs.standard_code = s.standard_code
            WHERE {where_clause}
            ORDER BY
                s.subject,
                s.grade_sort,
                cs.course_name,
                s.strand_code,
                s.standard_code;
        """

        with closing(self._connect()) as connection:
            cursor = connection.cursor()
            rows = cursor.execute(query, params).fetchall()

        return [dict(zip([column[0] for column in cursor.description], row)) for row in rows]