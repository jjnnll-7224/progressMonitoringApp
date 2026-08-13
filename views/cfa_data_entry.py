"""Fast, spreadsheet-style Common Formative Assessment score entry."""

from __future__ import annotations

from datetime import date
from io import StringIO

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.cfa_results import (
    create_administration,
    get_administrations,
    get_entry_context,
    get_saved_scores,
    save_scores,
)
from services.score_import import merge_imported_scores
from services.scoring import calculate_student_result, summarize_results


def build_roster_grid(context: dict, administration_id: int) -> pd.DataFrame:
    """Turn database roster/scores into the editable wide spreadsheet."""
    saved = {
        (row["student_id"], row["question_id"]): row["points_earned"]
        for row in get_saved_scores(administration_id)
    }
    rows = []
    for student in context["students"]:
        row = {
            "Student Number": student["student_number"],
            "Student": f"{student['last_name']}, {student['first_name']}",
        }
        for question in context["questions"]:
            row[f"Q{question['question_number']}"] = saved.get(
                (student["student_id"], question["question_id"])
            )
        rows.append(row)
    return pd.DataFrame(rows)


def calculate_grid(grid: pd.DataFrame, context: dict) -> tuple[pd.DataFrame, dict, list[str]]:
    """Calculate live totals/statuses and collect readable validation errors."""
    possible = {
        f"Q{question['question_number']}": float(question["max_points"])
        for question in context["questions"]
    }
    preview_rows, raw_results, errors = [], [], []

    for _, row in grid.iterrows():
        scores = {
            question: None if pd.isna(row[question]) else float(row[question])
            for question in possible
        }
        try:
            result = calculate_student_result(scores, possible)
        except ValueError as error:
            errors.append(f"{row['Student']}: {error}")
            result = {"earned": None, "possible": sum(possible.values()), "percent": None, "status": "Invalid"}

        raw_results.append(result)
        preview_rows.append(
            {
                "Student": row["Student"],
                "Total": (
                    f"{result['earned']:g}/{result['possible']:g}"
                    if result["earned"] is not None
                    else "—"
                ),
                "Percent": (
                    f"{result['percent']:.1f}%" if result["percent"] is not None else "—"
                ),
                "Status": result["status"],
            }
        )
    return pd.DataFrame(preview_rows), summarize_results(raw_results), errors


def most_missed_question(grid: pd.DataFrame, context: dict) -> str:
    """Return the question with the lowest average percent of available points."""
    performance = []
    for question in context["questions"]:
        column = f"Q{question['question_number']}"
        answered = pd.to_numeric(grid[column], errors="coerce").dropna()
        if not answered.empty:
            performance.append((answered.mean() / question["max_points"] * 100, column))
    return min(performance)[1] if performance else "—"


def scores_for_database(grid: pd.DataFrame, context: dict) -> list[dict]:
    """Convert the wide UI grid back to normalized item-level score records."""
    student_id_by_number = {
        student["student_number"]: student["student_id"] for student in context["students"]
    }
    records = []
    for _, row in grid.iterrows():
        for question in context["questions"]:
            value = row[f"Q{question['question_number']}"]
            records.append(
                {
                    "student_id": student_id_by_number[str(row["Student Number"])],
                    "question_id": question["question_id"],
                    "points_earned": None if pd.isna(value) else float(value),
                }
            )
    return records


# The Assessments page sets this ID before navigating here. Falling back to
# selected_assessment_id also makes development refreshes less frustrating.
assessment_id = st.session_state.get("cfa_assessment_id") or st.session_state.get(
    "selected_assessment_id"
)
if assessment_id is None:
    st.error("Open an assessment before entering CFA results.")
    if st.button("Back to Assessments"):
        st.switch_page("views/assessments.py")
    st.stop()

initial_context = get_entry_context(int(assessment_id))
if initial_context is None:
    st.error("That assessment could not be found.")
    st.stop()
    
initial_context = initial_context or {}
initial_context.setdefault("sections", [])

if not initial_context["sections"]:
    st.warning(
        "No sections are available. Verify the teacher, school year, "
        "term, and database section data."
    )
    st.stop()

section_by_label = {
    f"{item['section_name']} · {item['course_name']} · {item['teacher_name']}": item["section_id"]
    for item in initial_context["sections"]
}
saved_section_id = st.session_state.get("cfa_section_id")
default_section_label = next(
    (label for label, section_id in section_by_label.items() if section_id == saved_section_id),
    next(iter(section_by_label)),
)
selected_section_label = st.selectbox(
    "Class section",
    list(section_by_label),
    index=list(section_by_label).index(default_section_label),
    help="Scores are entered and saved one class at a time.",
)
selected_section_id = section_by_label[selected_section_label]
st.session_state.cfa_section_id = selected_section_id
context = get_entry_context(int(assessment_id), selected_section_id)
if context is None:
    st.error("That assessment could not be found.")
    st.stop()
if not context["questions"]:
    st.error("Add at least one question to this assessment before entering results.")
    st.stop()

back_col, _ = st.columns([1, 5])
if back_col.button("← Assessments", width="stretch"):
    st.switch_page("views/assessments.py")

page_header(
    "CFA data entry",
    context["name"],
    f"{context['standard_code']} · {context['standard_description']}",
)


# An administration represents one time the CFA was given (PRE or POST).
administrations = get_administrations(context["assessment_id"])
administration_by_label = {
    f"{item['administration_type']} · {item['administered_on']} · {item['status']}": item
    for item in administrations
}
administration_labels = [*administration_by_label, "+ Create new administration"]

default_label = administration_labels[0]
saved_administration_id = st.session_state.get("cfa_administration_id")
for label, item in administration_by_label.items():
    if item["administration_id"] == saved_administration_id:
        default_label = label
        break

selected_label = st.selectbox(
    "Assessment administration",
    administration_labels,
    index=administration_labels.index(default_label),
)

if selected_label == "+ Create new administration":
    with st.container(border=True):
        st.subheader("New administration")
        type_col, date_col, button_col = st.columns([1, 1.3, 1])
        administration_type = type_col.selectbox("Type", ["PRE", "POST"])
        administered_on = date_col.date_input("Date", value=date.today())
        button_col.write("")
        if button_col.button("Create", type="primary", width="stretch"):
            try:
                new_id = create_administration(
                    context["assessment_id"], administration_type, administered_on.isoformat()
                )
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state.cfa_administration_id = new_id
                st.session_state.cfa_flash = "Administration created."
                st.rerun()
    st.stop()

selected_administration = administration_by_label[selected_label]
administration_id = selected_administration["administration_id"]
st.session_state.cfa_administration_id = administration_id


# Each administration gets independent working data in session state. Database
# writes occur only when Save Draft or Submit Results is clicked.
# Keep every class's in-progress grid separate.  An administration can cover
# multiple sections, and switching the class selector must never display or
# overwrite a colleague's roster and draft values.
grid_key = f"cfa_grid_{administration_id}_{selected_section_id}"
revision_key = f"cfa_grid_revision_{administration_id}_{selected_section_id}"
if grid_key not in st.session_state:
    st.session_state[grid_key] = build_roster_grid(context, administration_id)
if revision_key not in st.session_state:
    st.session_state[revision_key] = 0

if message := st.session_state.pop("cfa_flash", None):
    st.success(message)

question_columns = [f"Q{item['question_number']}" for item in context["questions"]]

main_col, summary_col = st.columns([4, 1.25], gap="large")
with main_col:
    manual_tab, paste_tab, csv_tab = st.tabs(["Manual entry", "Paste spreadsheet", "CSV upload"])

    with manual_tab:
        st.caption("Enter points earned. Blank cells remain incomplete; zero is a valid score.")
        column_config = {
            "Student Number": st.column_config.TextColumn(disabled=True),
            "Student": st.column_config.TextColumn(disabled=True),
        }
        for question in context["questions"]:
            label = f"Q{question['question_number']}"
            column_config[label] = st.column_config.NumberColumn(
                label,
                min_value=0.0,
                max_value=float(question["max_points"]),
                step=0.5,
                help=f"Maximum {question['max_points']:g} points",
            )

        edited_grid = st.data_editor(
            st.session_state[grid_key],
            hide_index=True,
            width="stretch",
            disabled=["Student Number", "Student"],
            column_config=column_config,
            key=f"cfa_editor_{administration_id}_{st.session_state[revision_key]}",
        )
        st.session_state[grid_key] = edited_grid

    with paste_tab:
        st.caption("Paste rows copied from the downloaded template, including the header row.")
        pasted_text = st.text_area(
            "Spreadsheet rows",
            height=160,
            placeholder="Student Number\tStudent\tQ1\tQ2\tQ3",
        )
        if st.button("Apply pasted scores"):
            try:
                pasted_frame = pd.read_csv(StringIO(pasted_text), sep="\t", dtype=object)
                st.session_state[grid_key] = merge_imported_scores(
                    st.session_state[grid_key], pasted_frame, question_columns
                )
            except Exception as error:
                st.error(f"The pasted scores could not be applied: {error}")
            else:
                st.session_state[revision_key] += 1
                st.rerun()

    with csv_tab:
        uploaded_file = st.file_uploader("Upload completed score template", type="csv")
        if st.button("Apply CSV scores", disabled=uploaded_file is None):
            try:
                uploaded_frame = pd.read_csv(uploaded_file, dtype=object)
                st.session_state[grid_key] = merge_imported_scores(
                    st.session_state[grid_key], uploaded_frame, question_columns
                )
            except Exception as error:
                st.error(f"The CSV scores could not be applied: {error}")
            else:
                st.session_state[revision_key] += 1
                st.rerun()

    # Calculations are shown separately because only raw question scores should
    # ever be editable by a teacher.
    preview, summary, validation_errors = calculate_grid(st.session_state[grid_key], context)
    st.markdown("#### Live calculations")
    st.dataframe(preview, hide_index=True, width="stretch")

with summary_col:
    st.markdown("#### Summary")
    st.metric("Completed", f"{summary['completed']} / {summary['students']}")
    st.metric("Students Mastered", summary["counts"]["Mastered"])
    st.metric(
        "Not Yet Mastered",
        summary["completed"] - summary["counts"]["Mastered"],
    )
    st.metric("Average Score", f"{summary['average']:.1f}%" if summary["average"] is not None else "—")
    st.metric("Most Missed", most_missed_question(st.session_state[grid_key], context))

    st.markdown("##### Mastery breakdown")
    for status in ("Mastered", "Approaching", "Developing", "Intensive", "Incomplete"):
        count = summary["counts"][status]
        st.progress(count / summary["students"] if summary["students"] else 0, text=f"{status}: {count}")


# Teachers can download the exact headers expected by both import methods.
template = st.session_state[grid_key].copy()
template[question_columns] = None
download_col, validate_col, save_col, submit_col = st.columns([1.4, 1, 1, 1.2])
download_col.download_button(
    "Download Template",
    template.to_csv(index=False).encode("utf-8"),
    file_name=f"{context['name'].replace(' ', '_')}_scores.csv",
    mime="text/csv",
    width="stretch",
)
validate_clicked = validate_col.button("Validate Scores", width="stretch")
save_clicked = save_col.button("Save Draft", width="stretch")
submit_clicked = submit_col.button("Submit Results", type="primary", width="stretch")

if validate_clicked:
    if validation_errors:
        for error in validation_errors:
            st.error(error)
    elif summary["completed"] < summary["students"]:
        st.warning(
            f"Scores are valid, but {summary['students'] - summary['completed']} student(s) are incomplete."
        )
    else:
        st.success("All student scores are complete and valid.")

if save_clicked or submit_clicked:
    if validation_errors:
        for error in validation_errors:
            st.error(error)
    elif submit_clicked and summary["completed"] < summary["students"]:
        st.error("Complete every student score before submitting results.")
    else:
        try:
            save_scores(
                administration_id,
                scores_for_database(st.session_state[grid_key], context),
                submit=submit_clicked,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state.cfa_flash = (
                "Results submitted successfully." if submit_clicked else "Draft scores saved."
            )
            st.rerun()