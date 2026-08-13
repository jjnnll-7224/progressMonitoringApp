from __future__ import annotations

import pandas as pd
import numpy as np
import streamlit as st

from components.styles import page_header
from repositories.assessments import (
    create_assessment,
    get_assessment,
    get_assessments,
    get_cycles,
    get_sections_for_user,
    get_standards,
    get_standards_for_cycle,
    set_assessment_sections,
)

ASSESSMENT_TYPES = [
    "District CFA",
    "PLC CFA",
    "Teacher Created CFA",
    # "RISE Benchmark Alignment",
]
ASSESSMENT_STATUSES = ["Draft", "Ready", "Results Entered", "Archived"]
QUESTION_TYPES = ["Multiple Choice", "Open Response", "Short Answer", "Performance Task"]

def reset_creation_panel() -> None:
    """Close the panel and clear its widgets before the next assessment."""
    st.session_state.show_assessment_form = False
    for key in (
        "new_assessment_name",
        "new_assessment_standard",
        "new_assessment_cycle",
        "new_assessment_cycle_label",
        "new_assessment_type",
        "new_assessment_questions",
        "new_assessment_sections",
    ):
        st.session_state.pop(key, None)


def questions_for_database(
    edited_frame: pd.DataFrame,
    standard_id_by_label: dict[str, int],
) -> list[dict]:
    """Convert the editable grid into clean question records for the repository."""
    questions = []

    for row in edited_frame.to_dict("records"):
        # A completely blank dynamic row should be ignored, not treated as an error.
        if not any(pd.notna(value) and str(value).strip() for value in row.values()):
            continue

        standard_label = row.get("Standard")
        if pd.isna(standard_label) or not str(standard_label).strip():
            raise ValueError("Every question must be mapped to a standard.")

        if standard_label not in standard_id_by_label:
            raise ValueError(
                f"The standard selected for a question is no longer available: {standard_label}"
            )

        question_type = row.get("Question Type")
        max_points = row.get("Max Points")

        if pd.isna(question_type) or not str(question_type).strip():
            raise ValueError("Every question must have a question type.")

        if pd.isna(max_points):
            raise ValueError("Every question must have a maximum point value.")

        questions.append(
            {
                "question_type": str(question_type),
                "max_points": float(max_points),
                "subskill": (
                    ""
                    if pd.isna(row.get("Subskill"))
                    else str(row.get("Subskill")).strip()
                ),
                "standard_id": standard_id_by_label[standard_label],
            }
        )

    if not questions:
        raise ValueError("Add at least one question before saving the assessment.")

    return questions

@st.dialog("Assessment Details", width="large")
def show_assessment_dialog(selected):
    st.subheader(selected["name"])

    st.caption(
        f"{selected['standard_code']} · Grade {selected['grade_level']} "
        f"{selected['subject']} · {selected['assessment_type']}"
    )

    st.write(selected["standard_description"])

    detail_1, detail_2, detail_3, detail_4 = st.columns(4)
    detail_1.metric("Status", selected["status"])
    detail_2.metric("Questions", len(selected["questions"]))
    detail_3.metric("Possible Points", f"{selected['possible_points']:g}")
    detail_4.metric("Administrations", selected["administration_count"])

    question_frame = pd.DataFrame(selected["questions"]).rename(
        columns={
            "question_number": "Question",
            "question_type": "Type",
            "max_points": "Points",
            "standard": "Standard",
        }
    )

    st.dataframe(question_frame, hide_index=True, width="stretch")

    if selected["cycle_name"]:
        st.caption(f"PLC cycle: {selected['cycle_name']}")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Enter Results", type="primary", width="stretch"):
            st.session_state.selected_assessment_id = selected["assessment_id"]
            st.switch_page("views/cfa_data_entry.py")

    with col2:
        if st.button("Close", width="stretch"):
            st.session_state.selected_assessment_id = None
            st.rerun()
       

# Session state remembers UI choices when Streamlit reruns after a button click.
if "show_assessment_form" not in st.session_state:
    st.session_state.show_assessment_form = False
if "selected_assessment_id" not in st.session_state:
    st.session_state.selected_assessment_id = None


page_header(
    "CFA management",
    "Assessments",
    "Create, organize, and prepare Common Formative Assessments for score entry.",
)


# Load once per rerun. The repository owns SQL; this page only decides presentation.
all_assessments = get_assessments()

# Summary cards give the teacher a quick picture before they use the filters.
metric_1, metric_2, metric_3, metric_4 = st.columns(4)
metric_1.metric("Total Assessments", len(all_assessments))
metric_2.metric("Drafts", sum(item["status"] == "Draft" for item in all_assessments))
metric_3.metric("Ready", sum(item["status"] == "Ready" for item in all_assessments))
average_completion = (
    sum(item["completion_rate"] for item in all_assessments) / len(all_assessments)
    if all_assessments
    else 0
)
metric_4.metric("Average Completion", f"{average_completion:.0f}%")

st.write("")


# Search and filters live above the list because teachers will use them frequently.
search_col, status_col, type_col, create_col = st.columns([2.2, 1.2, 1.5, 1.1])
with search_col:
    search_text = st.text_input(
        "Search assessments",
        placeholder="Assessment name or standard",
        label_visibility="collapsed",
    )
with status_col:
    status_filter = st.selectbox(
        "Status",
        ["All statuses", *ASSESSMENT_STATUSES],
        label_visibility="collapsed",
    )
with type_col:
    type_filter = st.selectbox(
        "Assessment type",
        ["All types", *ASSESSMENT_TYPES],
        label_visibility="collapsed",
    )
with create_col:
    if st.button("Create Assessment", type="primary", width="stretch"):
        st.session_state.show_assessment_form = True
        st.session_state.selected_assessment_id = None


# The creation panel stays open through widget reruns because its state is stored above.
if st.session_state.show_assessment_form:
    with st.container(border=True):
        heading_col, cancel_col = st.columns([5, 1])
        with heading_col:
            st.subheader("Create an assessment")
            st.caption("Define the assessment first. Student results are entered after it is saved.")
        with cancel_col:
            if st.button("Cancel", width="stretch"):
                reset_creation_panel()
                st.rerun()

        standards = get_standards()
        if not standards:
            st.error("No standards are available. Add standards before creating an assessment.")
            st.stop()

        # Labels remain readable while the mapping preserves the numeric database ID.
        standard_by_label = {
            f"{item['code']} · Grade {item['grade_level']} {item['subject']}": item
            for item in standards
        }

        field_1, field_2 = st.columns(2)
        with field_1:
            assessment_name = st.text_input(
                "Assessment Name *",
                placeholder="Example: Central Idea CFA",
                key="new_assessment_name",
            )
        with field_2:
            assessment_type = st.selectbox(
                "Assessment Type *",
                ASSESSMENT_TYPES,
                index=1,
                key="new_assessment_type",
            )

        # Dashboard → Create CFA sets this one-time handoff.
        prefill = st.session_state.pop("assessment_prefill", None)
        if prefill:
            prefill_standard = next(
                (
                    label
                    for label, item in standard_by_label.items()
                    if item["standard_id"] == int(prefill["standard_id"])
                ),
                None,
            )
            if prefill_standard:
                st.session_state["new_assessment_standard"] = prefill_standard
                st.session_state["new_assessment_cycle"] = int(prefill["cycle_id"])

        standard_label = st.selectbox(
            "Standard *",
            list(standard_by_label),
            key="new_assessment_standard",
        )
        selected_standard = standard_by_label[standard_label]

        # Subject and grade come from the standard, which prevents contradictory data.
        subject_col, grade_col = st.columns(2)
        subject_col.text_input("Subject Area", value=selected_standard["subject"], disabled=True)
        grade_col.text_input("Grade Level", value=selected_standard["grade_level"], disabled=True)
        st.caption(selected_standard["description"])

        matching_cycles = get_cycles(selected_standard["standard_id"])
        cycle_by_label = {
            f"{cycle['name']} · {cycle['team_name']}": cycle["cycle_id"]
            for cycle in matching_cycles
        }

        if not cycle_by_label:
            st.warning(
                "There is no active PLC cycle for this standard yet. "
                "Create one from Dashboard → Upcoming pacing first."
            )
            selected_cycle_label = None
        else:
            cycle_labels = list(cycle_by_label)
            prefilled_cycle_id = st.session_state.get("new_assessment_cycle")
            default_cycle_index = next(
                (
                    index
                    for index, cycle_id in enumerate(cycle_by_label.values())
                    if cycle_id == prefilled_cycle_id
                ),
                0,
            )
            selected_cycle_label = st.selectbox(
                "PLC Cycle *",
                cycle_labels,
                index=default_cycle_index,
                help="This CFA will provide evidence for the selected PLC cycle.",
                key="new_assessment_cycle_label",
            )

        current_user = st.session_state.get("current_user")
        sections = get_sections_for_user(
            current_user["user_id"] if current_user else None,
            standard_id=selected_standard["standard_id"],
        )
        section_by_label = {
            f"{item['section_name']} · {item['term_name'] or 'Current term'} · "
            f"{item['student_count']} students": item["section_id"]
            for item in sections
        }
        selected_section_labels = st.multiselect(
            "Class sections *",
            list(section_by_label),
            help="Only students enrolled in these sections will appear in CFA Data Entry.",
            key="new_assessment_sections",
        )

        if not current_user:
            st.info("Enter your email in the sidebar to see your assigned class sections.")
        elif not sections:
            st.warning("No matching sections are assigned to this user yet.")

        st.markdown("#### Questions")
        st.caption(
            "Add or delete rows. Question numbers are assigned automatically when you save. "
            "Map every question to one of the standards in the selected PLC cycle."
        )

        selected_cycle_id = (
            cycle_by_label[selected_cycle_label]
            if selected_cycle_label
            else None
        )

        if selected_cycle_id is not None:
            question_standards = get_standards_for_cycle(selected_cycle_id)
        else:
            # Non-PLC assessments, or assessments without a cycle, can still use
            # the assessment's selected primary standard.
            question_standards = [selected_standard]

        # Defensive fallback: the assessment's primary standard should always be
        # available in the question grid, even if older cycle data is incomplete.
        if not any(
            item["standard_id"] == selected_standard["standard_id"]
            for item in question_standards
        ):
            question_standards = [selected_standard, *question_standards]

        question_standard_by_label = {
            f"{item['code']} · {item['description']}": item["standard_id"]
            for item in question_standards
        }
        question_standard_labels = list(question_standard_by_label)

        if not question_standard_labels:
            st.error(
                "No standards are attached to this PLC cycle. "
                "Add standards to the cycle before creating the CFA."
            )
            st.stop()

        default_standard_label = next(
            (
                label
                for label, standard_id in question_standard_by_label.items()
                if standard_id == selected_standard["standard_id"]
            ),
            question_standard_labels[0],
        )

        # st.data_editor provides the spreadsheet-like question builder from the spec.
        default_questions = pd.DataFrame(
            [
                {
                    "Question Type": "Multiple Choice",
                    "Max Points": 1.0,
                    "Subskill": "",
                    "Standard": default_standard_label,
                },
                {
                    "Question Type": "Multiple Choice",
                    "Max Points": 1.0,
                    "Subskill": "",
                    "Standard": default_standard_label,
                },
                {
                    "Question Type": "Short Answer",
                    "Max Points": 2.0,
                    "Subskill": "",
                    "Standard": default_standard_label,
                },
            ]
        )

        edited_questions = st.data_editor(
            default_questions,
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            key="new_assessment_questions",
            column_config={
                "Question Type": st.column_config.SelectboxColumn(
                    options=QUESTION_TYPES,
                    required=True,
                ),
                "Max Points": st.column_config.NumberColumn(
                    min_value=0.5,
                    step=0.5,
                    required=True,
                ),
                "Subskill": st.column_config.TextColumn(
                    help="Optional skill detail, such as 'Supporting details'."
                ),
                "Standard": st.column_config.SelectboxColumn(
                    options=question_standard_labels,
                    required=True,
                    help=(
                        "Select the PLC-cycle standard measured by this question. "
                        "This mapping is used for standard-level mastery analysis."
                    ),
                ),
            },
            column_order=[
                "Question Type",
                "Max Points",
                "Subskill",
                "Standard",
            ],
        )

        total_points = pd.to_numeric(edited_questions["Max Points"], errors="coerce").sum()
        st.caption(f"{len(edited_questions)} questions · {total_points:g} possible points")

        save_col, ready_col, spacer_col = st.columns([1.2, 1.3, 3])
        save_draft = save_col.button("Save Draft", width="stretch")
        save_ready = ready_col.button("Save as Ready", type="primary", width="stretch")

        # Both buttons use the same transaction; only the saved status differs.
        if save_draft or save_ready:
            try:
                if assessment_type == "PLC CFA" and selected_cycle_label is None:
                    raise ValueError(
                        "Select an active PLC cycle before creating a PLC CFA."
                    )
                new_id = create_assessment(
                    name=assessment_name,
                    standard_id=selected_standard["standard_id"],
                    assessment_type=assessment_type,
                    status="Ready" if save_ready else "Draft",
                    cycle_id=cycle_by_label[selected_cycle_label] if selected_cycle_label else None,
                    section_ids=[section_by_label[label] for label in selected_section_labels],
                    questions=questions_for_database(
                        edited_questions,
                        question_standard_by_label,
                    ),
                )
            except ValueError as error:
                st.error(str(error))
            except Exception as error:
                # Keep the unexpected error visible during prototype development.
                st.error(f"The assessment could not be saved: {error}")
            else:
                reset_creation_panel()
                st.session_state.selected_assessment_id = new_id
                st.session_state.assessment_flash = f"{assessment_name.strip()} was saved."
                st.rerun()


# A one-rerun flash message confirms that the database write succeeded.
if message := st.session_state.pop("assessment_flash", None):
    st.success(message)


# Reload with the selected filters so newly created assessments appear immediately.
assessments = get_assessments(search_text, status_filter, type_filter)
st.subheader("Assessment library")
st.caption(f"Showing {len(assessments)} assessment{'s' if len(assessments) != 1 else ''}")

if not assessments:
    st.info("No assessments match these filters. Try clearing a filter or create a new assessment.")
else:
    # Column headings and repeated rows create a compact, scannable SaaS-style list.
    header = st.columns([2.4, 1, 1.3, 1, 1.1, 0.7])
    for column, label in zip(
        header,
        ["Assessment", "Standard", "Type", "Date", "Completion", ""],
    ):
        column.markdown(f"**{label}**")

    for assessment in assessments:
        row = st.columns([2.4, 1, 1.3, 1, 1.1, 0.7])
        row[0].markdown(f"**{assessment['name']}**  \n{assessment['status']}")
        row[1].write(assessment["standard_code"])
        row[2].write(assessment["assessment_type"])
        row[3].write(assessment["latest_date"] or "—")
        row[4].progress(
            assessment["completion_rate"] / 100,
            text=f"{assessment['completion_rate']:.0f}%",
        )
        if row[5].button("Open", key=f"open_assessment_{assessment['assessment_id']}"):
            st.session_state.selected_assessment_id = assessment["assessment_id"]
            st.session_state.show_assessment_form = False
            st.rerun()
        st.divider()


# Selecting Open reveals details without navigating away from the library page.
if st.session_state.selected_assessment_id is not None:
    selected = get_assessment(st.session_state.selected_assessment_id)
    if selected is None:
        st.warning("That assessment no longer exists.")
        st.session_state.selected_assessment_id = None
    else:
        show_assessment_dialog(selected)
        with st.container(border=True):
            title_col, close_col = st.columns([5, 1])
            title_col.subheader(selected["name"])
            if close_col.button("Close", width="stretch"):
                st.session_state.selected_assessment_id = None
                st.rerun()

            st.caption(
                f"{selected['standard_code']} · Grade {selected['grade_level']} "
                f"{selected['subject']} · {selected['assessment_type']}"
            )
            st.write(selected["standard_description"])

            detail_1, detail_2, detail_3, detail_4 = st.columns(4)
            detail_1.metric("Status", selected["status"])
            detail_2.metric("Questions", len(selected["questions"]))
            detail_3.metric("Possible Points", f"{selected['possible_points']:g}")
            detail_4.metric("Administrations", selected["administration_count"])

            assigned_names = [
                f"{item['section_name']} ({item['student_count']} students)"
                for item in selected["sections"]
            ]
            st.caption("Assigned classes: " + (", ".join(assigned_names) if assigned_names else "None"))

            current_user = st.session_state.get("current_user")
            editable_sections = get_sections_for_user(
                current_user["user_id"] if current_user else None
            )
            if editable_sections and selected["status"] != "Results Entered":
                editable_by_label = {
                    f"{item['section_name']} · {item['term_name'] or 'Current term'}": item["section_id"]
                    for item in editable_sections
                }
                currently_assigned = {
                    item["section_id"] for item in selected["sections"]
                }
                new_section_labels = st.multiselect(
                    "Assigned class sections",
                    list(editable_by_label),
                    default=[
                        label for label, section_id in editable_by_label.items()
                        if section_id in currently_assigned
                    ],
                    key=f"assignment_sections_{selected['assessment_id']}",
                )
                if st.button("Save Class Assignments", key=f"save_assignments_{selected['assessment_id']}"):
                    try:
                        set_assessment_sections(
                            selected["assessment_id"],
                            [editable_by_label[label] for label in new_section_labels],
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("Class assignments saved.")
                        st.rerun()

            question_frame = pd.DataFrame(selected["questions"]).rename(
                columns={
                    "question_number": "Question",
                    "question_type": "Type",
                    "max_points": "Points",
                    "subskill": "Subskill",
                    "standard_code": "Standard",
                }
            )

            visible_question_columns = [
                column
                for column in ["Question", "Type", "Points", "Subskill", "Standard"]
                if column in question_frame.columns
            ]
            st.dataframe(
                question_frame[visible_question_columns],
                hide_index=True,
                width="stretch",
            )

            if selected["cycle_name"]:
                st.caption(f"PLC cycle: {selected['cycle_name']}")

            # Keep the selected ID in session state so the hidden score-entry
            # page knows which assessment the teacher opened.
            if st.button("Enter Results", type="primary"):
                st.session_state.cfa_assessment_id = selected["assessment_id"]
                st.session_state.pop("cfa_administration_id", None)
                st.switch_page("views/cfa_data_entry.py")