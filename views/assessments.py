from __future__ import annotations

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.assessments import (
    assign_assessment_to_cycle,
    create_assessment,
    create_core_idea,
    get_assessment,
    get_assessments,
    get_compatible_cycles,
    get_core_ideas,
    get_sections_for_cycle_user,
    get_standards,
)


ASSESSMENT_TYPES = [
    "District CFA",
    "PLC CFA",
    "Teacher Created CFA",
]
ASSESSMENT_STATUSES = [
    "Draft",
    "Ready",
    "Results Entered",
    "Archived",
    "Published",
]
QUESTION_TYPES = [
    "Multiple Choice",
    "Selected Response",
    "Open Response",
    "Short Answer",
    "Constructed Response",
    "Performance Task",
]


def reset_creation_panel() -> None:
    st.session_state.show_assessment_form = False
    for key in (
        "new_assessment_name",
        "new_assessment_type",
        "new_assessment_standards",
        "new_assessment_questions",
    ):
        st.session_state.pop(key, None)


def questions_for_database(
    edited_frame: pd.DataFrame,
    core_idea_id_by_label: dict[str, int],
) -> list[dict]:
    questions = []

    for row in edited_frame.to_dict("records"):
        if not any(
            pd.notna(value) and str(value).strip()
            for value in row.values()
        ):
            continue

        question_type = row.get("Question Type")
        max_points = row.get("Max Points")
        core_idea_label = row.get("Core Idea")

        if pd.isna(question_type) or not str(question_type).strip():
            raise ValueError("Every question must have a question type.")
        if pd.isna(max_points):
            raise ValueError("Every question must have maximum points.")
        if pd.isna(core_idea_label) or not str(core_idea_label).strip():
            raise ValueError("Every question must be mapped to a Core Idea.")
        if core_idea_label not in core_idea_id_by_label:
            raise ValueError(
                f"Core Idea is no longer available: {core_idea_label}"
            )

        questions.append(
            {
                "question_type": str(question_type),
                "max_points": float(max_points),
                "core_idea_id": core_idea_id_by_label[core_idea_label],
            }
        )

    if not questions:
        raise ValueError("Add at least one assessment question.")
    return questions


@st.dialog("Assessment Details", width="large")
def show_assessment_dialog(selected: dict) -> None:
    st.subheader(selected["name"])

    standard_codes = ", ".join(
        item["code"] for item in selected["standards"]
    )
    st.caption(
        f"{standard_codes or 'No standards'} · "
        f"{selected['assessment_type']} · {selected['status']}"
    )

    detail_1, detail_2, detail_3, detail_4 = st.columns(4)
    detail_1.metric("Questions", len(selected["questions"]))
    detail_2.metric("Possible Points", f"{selected['possible_points']:g}")
    detail_3.metric("PLC Uses", len(selected["assignments"]))
    detail_4.metric("Administrations", selected["administration_count"])

    st.markdown("#### Standards")
    standards_frame = pd.DataFrame(
        [
            {
                "Standard": item["code"],
                "Grade": item["grade_level"],
                "Subject": item["subject"],
                "Description": item["description"],
            }
            for item in selected["standards"]
        ]
    )
    st.dataframe(standards_frame, hide_index=True, width="stretch")

    st.markdown("#### Question map")
    question_frame = pd.DataFrame(
        [
            {
                "Question": row["question_number"],
                "Type": row["question_type"],
                "Points": row["max_points"],
                "Standard": row["standard_code"],
                "Core Idea": row["core_idea_name"],
            }
            for row in selected["questions"]
        ]
    )
    st.dataframe(question_frame, hide_index=True, width="stretch")

    st.markdown("#### PLC cycle assignments")
    if selected["assignments"]:
        for assignment in selected["assignments"]:
            with st.container(border=True):
                st.markdown(
                    f"**{assignment['cycle_name']}** · "
                    f"{assignment['team_name']}"
                )
                section_text = ", ".join(
                    f"{section['section_name']} "
                    f"({section['student_count']} students)"
                    for section in assignment["sections"]
                )
                st.caption(
                    "Sections: " + (section_text or "No sections assigned")
                )

                if st.button(
                    "Enter Results",
                    type="primary",
                    key=f"enter_results_{assignment['cycle_assessment_id']}",
                ):
                    st.session_state.cfa_cycle_assessment_id = (
                        assignment["cycle_assessment_id"]
                    )
                    st.session_state.cfa_return_page = "views/assessments.py"
                    st.session_state.cfa_assessment_id = selected["assessment_id"]
                    st.session_state.pop("cfa_administration_id", None)
                    st.switch_page("views/cfa_data_entry.py")
    else:
        st.caption(
            "This CFA is in the library but has not been assigned to a PLC cycle."
        )

    st.markdown("#### Assign this CFA to a PLC cycle")
    current_user = st.session_state.get("current_user")
    compatible_cycles = get_compatible_cycles(
        selected["assessment_id"],
        user_id=current_user["user_id"] if current_user else None,
    )

    if not current_user:
        st.info("Sign in as a teacher to assign the CFA to your PLC cycle.")
    elif not compatible_cycles:
        st.caption(
            "No active PLC cycles available to this user share a standard "
            "with this CFA."
        )
    else:
        cycle_by_label = {
            (
                f"{row['name']} · {row['team_name']} · "
                f"{row['overlapping_standards'] or 'matching standard'}"
            ): row
            for row in compatible_cycles
        }
        selected_cycle_label = st.selectbox(
            "PLC cycle",
            list(cycle_by_label),
            key=f"assign_cycle_{selected['assessment_id']}",
        )
        selected_cycle = cycle_by_label[selected_cycle_label]

        sections = get_sections_for_cycle_user(
            current_user["user_id"],
            selected_cycle["cycle_id"],
        )
        section_by_label = {
            (
                f"{row['section_name']} · "
                f"{row['term_name'] or 'Current term'} · "
                f"{row['student_count']} students"
            ): row["section_id"]
            for row in sections
        }

        selected_section_labels = st.multiselect(
            "Class sections",
            list(section_by_label),
            key=(
                f"assign_sections_{selected['assessment_id']}_"
                f"{selected_cycle['cycle_id']}"
            ),
        )

        if not sections:
            st.warning(
                "No sections for this user match the PLC cycle's subject and grade."
            )

        if st.button(
            "Assign CFA",
            type="primary",
            key=f"assign_cfa_{selected['assessment_id']}",
            disabled=not selected_section_labels,
        ):
            try:
                cycle_assessment_id = assign_assessment_to_cycle(
                    assessment_id=selected["assessment_id"],
                    cycle_id=selected_cycle["cycle_id"],
                    section_ids=[
                        section_by_label[label]
                        for label in selected_section_labels
                    ],
                )
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state.assessment_flash = (
                    f"{selected['name']} was assigned to "
                    f"{selected_cycle['name']}."
                )
                st.session_state.cfa_cycle_assessment_id = cycle_assessment_id
                st.rerun()


if "show_assessment_form" not in st.session_state:
    st.session_state.show_assessment_form = False
if "selected_assessment_id" not in st.session_state:
    st.session_state.selected_assessment_id = None


page_header(
    "CFA library",
    "Assessments",
    "Find reusable CFAs by standard, inspect Core Idea coverage, "
    "or assign an existing CFA to a PLC cycle.",
)


standards = get_standards()
standard_by_label = {
    f"{item['code']} · {item['grade_level']} · {item['subject']}": item
    for item in standards
}

search_col, standard_col, status_col, type_col, create_col = st.columns(
    [2.2, 2.0, 1.2, 1.4, 1.1]
)

with search_col:
    search_text = st.text_input(
        "Search",
        placeholder="Search CFA, standard, or Core Idea",
        label_visibility="collapsed",
    )

with standard_col:
    standard_filter_labels = st.multiselect(
        "Standards",
        list(standard_by_label),
        placeholder="Filter standards",
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
        "Type",
        ["All types", *ASSESSMENT_TYPES],
        label_visibility="collapsed",
    )

with create_col:
    if st.button("Create CFA", type="primary", width="stretch"):
        st.session_state.show_assessment_form = True
        st.session_state.selected_assessment_id = None


if st.session_state.show_assessment_form:
    with st.container(border=True):
        heading_col, cancel_col = st.columns([5, 1])

        with heading_col:
            st.subheader("Create a reusable CFA")
            st.caption(
                "Define the instructional asset here. "
                "PLC cycle and class assignments happen after the CFA is saved."
            )

        with cancel_col:
            if st.button("Cancel", width="stretch"):
                reset_creation_panel()
                st.rerun()

        field_1, field_2 = st.columns([2, 1])

        with field_1:
            assessment_name = st.text_input(
                "Assessment Name *",
                placeholder="Example: Grade 4 Main Idea CFA A",
                key="new_assessment_name",
            )

        with field_2:
            assessment_type = st.selectbox(
                "Assessment Type *",
                ASSESSMENT_TYPES,
                index=1,
                key="new_assessment_type",
            )

        selected_standard_labels = st.multiselect(
            "Standards *",
            list(standard_by_label),
            key="new_assessment_standards",
            help=(
                "Select every standard measured by at least one question "
                "on this CFA."
            ),
        )

        selected_standard_ids = [
            standard_by_label[label]["standard_id"]
            for label in selected_standard_labels
        ]

        if selected_standard_labels:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Standard": standard_by_label[label]["code"],
                            "Description": standard_by_label[label]["description"],
                        }
                        for label in selected_standard_labels
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

        core_ideas = get_core_ideas(selected_standard_ids)
        core_idea_by_label = {
            f"{row['standard_code']} · {row['name']}": row
            for row in core_ideas
        }

        if selected_standard_ids and not core_ideas:
            st.warning(
                "These standards do not have Core Ideas yet. "
                "Create at least one Core Idea before building questions."
            )

        if selected_standard_ids:
            with st.expander("Add a Core Idea"):
                core_standard_label = st.selectbox(
                    "Standard",
                    selected_standard_labels,
                    key="new_core_idea_standard",
                )
                new_core_idea_name = st.text_input(
                    "Core Idea",
                    placeholder="Example: Identify the main idea",
                    key="new_core_idea_name",
                )
                new_core_idea_description = st.text_area(
                    "Description",
                    key="new_core_idea_description",
                )

                if st.button("Add Core Idea", key="add_core_idea"):
                    try:
                        create_core_idea(
                            standard_id=standard_by_label[
                                core_standard_label
                            ]["standard_id"],
                            name=new_core_idea_name,
                            description=new_core_idea_description,
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.rerun()

        st.markdown("#### Questions")
        st.caption(
            "Each question maps to one Core Idea. "
            "The Core Idea determines the question's standard automatically."
        )

        core_idea_labels = list(core_idea_by_label)
        default_core_idea = core_idea_labels[0] if core_idea_labels else None

        default_questions = pd.DataFrame(
            [
                {
                    "Question Type": "Multiple Choice",
                    "Max Points": 1.0,
                    "Core Idea": default_core_idea,
                },
                {
                    "Question Type": "Multiple Choice",
                    "Max Points": 1.0,
                    "Core Idea": default_core_idea,
                },
                {
                    "Question Type": "Short Answer",
                    "Max Points": 2.0,
                    "Core Idea": default_core_idea,
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
                "Core Idea": st.column_config.SelectboxColumn(
                    options=core_idea_labels,
                    required=True,
                    help=(
                        "Choose the specific instructional idea measured "
                        "by this question."
                    ),
                ),
            },
            column_order=["Question Type", "Max Points", "Core Idea"],
            disabled=not core_idea_labels,
        )

        total_points = pd.to_numeric(
            edited_questions["Max Points"],
            errors="coerce",
        ).sum()

        st.caption(
            f"{len(edited_questions)} questions · "
            f"{total_points:g} possible points"
        )

        save_col, ready_col, _ = st.columns([1.2, 1.3, 3])

        save_draft = save_col.button(
            "Save Draft",
            width="stretch",
            disabled=not core_idea_labels,
        )
        save_ready = ready_col.button(
            "Save as Ready",
            type="primary",
            width="stretch",
            disabled=not core_idea_labels,
        )

        if save_draft or save_ready:
            try:
                new_id = create_assessment(
                    name=assessment_name,
                    standard_ids=selected_standard_ids,
                    assessment_type=assessment_type,
                    status="Ready" if save_ready else "Draft",
                    questions=questions_for_database(
                        edited_questions,
                        {
                            label: row["core_idea_id"]
                            for label, row in core_idea_by_label.items()
                        },
                    ),
                )
            except ValueError as error:
                st.error(str(error))
            except Exception as error:
                st.error(f"The assessment could not be saved: {error}")
            else:
                target_cycle_id = st.session_state.get("assessment_target_cycle_id")
                reset_creation_panel()
                st.session_state.selected_assessment_id = None
                st.session_state.assessment_flash = (
                    f"{assessment_name.strip()} was saved to the CFA library."
                )
                if target_cycle_id is not None:
                    # Return directly to the originating PLC cycle.  The PLC page
                    # reopens its assignment dialog with this new CFA preselected.
                    st.session_state.plc_assign_assessment_id = new_id
                    st.switch_page("views/plc_cycles.py")
                else:
                    st.rerun()


if message := st.session_state.pop("assessment_flash", None):
    st.success(message)


selected_standard_filter_ids = [
    standard_by_label[label]["standard_id"]
    for label in standard_filter_labels
]

assessments = get_assessments(
    search_text,
    status_filter,
    type_filter,
    selected_standard_filter_ids,
)

st.subheader("Assessment library")
st.caption(
    f"Showing {len(assessments)} "
    f"assessment{'s' if len(assessments) != 1 else ''}"
)

if not assessments:
    st.info(
        "No assessments match these filters. "
        "Try clearing a filter or create a reusable CFA."
    )
else:
    header = st.columns([2.4, 2.0, 1.0, 2.0, 0.8, 0.7])

    for column, label in zip(
        header,
        [
            "Assessment",
            "PLC Cycle",
            "Date",
            "Standards",
            "Questions",
            "",
        ],
    ):
        column.markdown(f"**{label}**")

    for assessment in assessments:
        row = st.columns([2.4, 2.0, 1.0, 2.0, 0.8, 0.7])

        row[0].markdown(
            f"**{assessment['name']}**  \n"
            f"{assessment['assessment_type']} · {assessment['status']}"
        )
        row[1].write(assessment["cycle_names"] or "Library only")
        row[2].write(assessment["latest_date"] or "—")
        row[3].write(assessment["standards"] or "—")
        row[4].write(assessment["question_count"])

        if row[5].button(
            "Open",
            key=f"open_assessment_{assessment['assessment_id']}",
        ):
            # Call the dialog only from the user's click.  Nothing in session
            # state automatically reopens it when the Assessment page loads.
            selected = get_assessment(int(assessment["assessment_id"]))
            if selected is None:
                st.warning("That assessment no longer exists.")
            else:
                st.session_state.show_assessment_form = False
                show_assessment_dialog(selected)

        st.divider()
