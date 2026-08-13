"""Helpers for matching pasted or uploaded score rows to the roster grid."""

from __future__ import annotations

import pandas as pd


def merge_imported_scores(
    roster_grid: pd.DataFrame,
    imported: pd.DataFrame,
    question_columns: list[str],
) -> pd.DataFrame:
    """Merge imported question scores using Student Number or Student name."""
    incoming = imported.copy()
    incoming.columns = [str(column).strip() for column in incoming.columns]

    missing_questions = [column for column in question_columns if column not in incoming.columns]
    if missing_questions:
        raise ValueError(f"Missing score column(s): {', '.join(missing_questions)}")

    identity_column = next(
        (column for column in ("Student Number", "Student") if column in incoming.columns),
        None,
    )
    if identity_column is None:
        raise ValueError("Include either a Student Number or Student column.")

    # Normalize identifiers as strings so a numeric CSV ID can still match.
    def normalized(value: object) -> str:
        text = str(value).strip()
        return text[:-2] if text.endswith(".0") else text

    result = roster_grid.copy()
    roster_lookup = {
        normalized(value): index for index, value in result[identity_column].items()
    }
    seen: set[str] = set()

    for _, row in incoming.iterrows():
        identity = normalized(row[identity_column])
        if not identity or identity.lower() == "nan":
            continue
        if identity in seen:
            raise ValueError(f"{identity_column} {identity} appears more than once.")
        if identity not in roster_lookup:
            raise ValueError(f"{identity_column} {identity} is not in this roster.")
        seen.add(identity)

        target_index = roster_lookup[identity]
        for question in question_columns:
            value = row[question]
            if pd.isna(value) or str(value).strip() == "":
                result.at[target_index, question] = None
                continue
            try:
                result.at[target_index, question] = float(value)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"{identity_column} {identity} has a nonnumeric {question} score."
                ) from error

    return result