"""PLC working dashboard: evidence, discussion, notes, and next actions."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.cycles import (
    create_commitment,
    create_cycle_note,
    get_cycle_analysis,
    get_cycle_assessment_evidence,
    get_cycle_standard_mastery,
    get_teacher_mastery,
    get_team_members,
    list_active_cycles,
    list_commitments,
    list_cycle_notes,
    set_commitment_status,
)


PROFICIENCY_COLORS = {
    "Mastered": "#1f77b4",
    "Approaching": "#eadc19",
    "Developing": "#ff7f0e",
    "Intensive": "#d62728",
    None: "#9CA3AF",
}

DISCUSSION_PROMPTS = [
    "What do students appear to understand well?",
    "Which Core Ideas are producing the greatest difficulty?",
    "What patterns are consistent across classrooms, and where do results differ?",
    "Which students need prerequisite support, targeted reteaching, or enrichment?",
    "What instructional approaches appear to have worked?",
    "What will we reteach differently, and what evidence will show that it worked?",
]


def percent_text(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "—"


def mastery_color(status: str | None) -> str:
    return PROFICIENCY_COLORS.get(status, "#9CA3AF")


def color_strip(status: str | None) -> None:
    st.markdown(
        (
            "<div style='height:6px;border-radius:6px;"
            f"background:{mastery_color(status)};margin-bottom:10px;'></div>"
        ),
        unsafe_allow_html=True,
    )


@st.dialog("Add teacher commitment", width="large")
def commitment_dialog(
    cycle_id: int,
    members: list[dict],
) -> None:
    member_labels = ["Unassigned"] + [
        f"{member['display_name']} · {member['role']}"
        for member in members
    ]
    member_id_by_label = {
        f"{member['display_name']} · {member['role']}": member["user_id"]
        for member in members
    }

    with st.form(f"commitment_form_{cycle_id}"):
        name = st.text_input(
            "Commitment Name",
            placeholder="Example: Model evidence selection",
        )
        action_step = st.text_area(
            "Action Step",
            placeholder="Describe exactly what the teacher will do.",
        )
        evidence = st.text_area(
            "Evidence to Collect",
            placeholder="Example: Student annotations and exit tickets",
        )

        due_col, owner_col = st.columns(2)
        due_date = due_col.date_input(
            "Due Date",
            value=date.today() + timedelta(days=7),
        )
        assigned_label = owner_col.selectbox(
            "Assigned Teacher",
            member_labels,
        )

        notes = st.text_area("Notes (optional)")

        cancel_col, save_col = st.columns(2)
        cancel = cancel_col.form_submit_button(
            "Cancel",
            width="stretch",
        )
        save = save_col.form_submit_button(
            "Save Commitment",
            type="primary",
            width="stretch",
        )

    if cancel:
        st.rerun()

    if save:
        try:
            create_commitment(
                cycle_id=cycle_id,
                name=name,
                action_step=action_step,
                evidence=evidence,
                due_date=due_date.isoformat(),
                assigned_user_id=member_id_by_label.get(
                    assigned_label
                ),
                notes=notes,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state.plc_flash = (
                "Teacher commitment saved."
            )
            st.rerun()


page_header(
    "PLC working dashboard",
    "PLC Cycles",
    "See what students know, focus the team discussion, "
    "and record instructional decisions.",
)


cycles = list_active_cycles()

if not cycles:
    st.info(
        "There are no active PLC cycles. "
        "Create a cycle before opening the PLC workspace."
    )
    st.stop()


cycle_by_label = {
    (
        f"{item['plc']} · {item['name']} · "
        f"{item['standard']}"
    ): item
    for item in cycles
}

selected_label = st.selectbox(
    "PLC cycle",
    list(cycle_by_label),
)

selected_cycle = cycle_by_label[selected_label]
cycle_id = int(selected_cycle["cycle_id"])

cycle = get_cycle_analysis(cycle_id)

if cycle is None:
    st.error("The selected PLC cycle could not be found.")
    st.stop()


if message := st.session_state.pop("plc_flash", None):
    st.success(message)


latest = cycle["latest"]
members = get_team_members(cycle_id)
member_names = ", ".join(
    member["display_name"]
    for member in members
)

overall_mastery_rate = None
students_assessed = 0

if latest:
    students_assessed = int(latest["completed"])
    overall_mastery_rate = (
        latest["counts"]["Mastered"]
        / students_assessed
        * 100
        if students_assessed
        else None
    )


# ---------------------------------------------------------------------
# Cycle header
# ---------------------------------------------------------------------

with st.container(border=True):
    title_col, date_col = st.columns([3.5, 1.5])

    with title_col:
        st.subheader(cycle["plc"])
        st.markdown(f"**{cycle['name']}**")

        standard_codes = ", ".join(
            standard["code"]
            for standard in cycle["standards"]
        )
        st.caption(
            f"Standards: {standard_codes or cycle['standard']}"
        )

        if member_names:
            st.caption(f"Team: {member_names}")

    with date_col:
        st.markdown(
            f"**{cycle['start_date']} → {cycle['end_date']}**"
        )
        st.caption(
            f"{cycle['status']} · System stage: {cycle['stage']}"
        )

    metrics = st.columns(4)

    metrics[0].metric(
        "Students Assessed",
        students_assessed if latest else "—",
    )

    metrics[1].metric(
        "Overall Mastery",
        percent_text(overall_mastery_rate),
    )

    metrics[2].metric(
        "Latest CFA",
        latest["assessment_name"] if latest else "—",
        help=(
            f"{latest['administration_type']} · "
            f"{latest['administered_on']}"
            if latest
            else None
        ),
    )

    metrics[3].metric(
        "Change vs. Prior",
        (
            f"{cycle['growth_points']:+.1f} pts"
            if cycle["growth_points"] is not None
            else "—"
        ),
    )


# ---------------------------------------------------------------------
# Student mastery distribution
# ---------------------------------------------------------------------

st.subheader("Student mastery")

if latest is None:
    st.warning(
        "This cycle does not have submitted CFA evidence yet. "
        "Assign a CFA and submit results to populate this workspace."
    )
else:
    distribution_columns = st.columns(4)

    for column, status in zip(
        distribution_columns,
        (
            "Mastered",
            "Approaching",
            "Developing",
            "Intensive",
        ),
    ):
        count = int(latest["counts"][status])
        share = (
            count / students_assessed * 100
            if students_assessed
            else 0
        )

        with column.container(border=True):
            color_strip(status)
            st.markdown(f"**{status}**")
            st.markdown(f"### {share:.0f}%")
            st.caption(
                f"{count} student{'s' if count != 1 else ''}"
            )


# ---------------------------------------------------------------------
# Standards mastery cards
# ---------------------------------------------------------------------

st.subheader("Standards & mastery")
st.caption(
    "Mastery is the percent of assessed students classified "
    "Mastered using only questions mapped to each standard."
)

standard_mastery = get_cycle_standard_mastery(
    cycle_id,
    latest["administration_id"] if latest else None,
)

if not standard_mastery:
    st.caption(
        "No standards are attached to this cycle yet."
    )
else:
    for start in range(0, len(standard_mastery), 3):
        card_columns = st.columns(
            min(3, len(standard_mastery) - start)
        )

        for column, standard in zip(
            card_columns,
            standard_mastery[start:start + 3],
        ):
            with column.container(border=True):
                color_strip(standard["status"])

                st.markdown(
                    f"#### {standard['code']}"
                )
                st.caption(
                    standard["description"]
                )

                st.markdown(
                    f"## {percent_text(standard['mastery_rate'])}"
                )

                if standard["students_assessed"]:
                    st.caption(
                        f"{standard['students_mastered']} of "
                        f"{standard['students_assessed']} students Mastered · "
                        f"{percent_text(standard['average_score'])} "
                        "average score"
                    )
                else:
                    st.caption("No submitted evidence yet.")


# ---------------------------------------------------------------------
# Team / classroom comparison
# ---------------------------------------------------------------------

st.subheader("Team classrooms")
st.caption(
    "This is a conversation signal, not a teacher ranking. "
    "Differences can help the team identify practices and student needs worth discussing."
)

teacher_mastery = get_teacher_mastery(
    cycle_id,
    latest["administration_id"] if latest else None,
)

if not teacher_mastery:
    st.caption(
        "Teacher-level results will appear after sections are assigned "
        "to the CFA and scores are submitted."
    )
else:
    teacher_columns = st.columns(
        min(4, len(teacher_mastery))
    )

    for index, teacher in enumerate(teacher_mastery):
        column = teacher_columns[
            index % len(teacher_columns)
        ]

        with column.container(border=True):
            color_strip(teacher["status"])
            st.markdown(
                f"**{teacher['teacher_name']}**"
            )
            st.markdown(
                f"### {percent_text(teacher['mastery_rate'])}"
            )
            st.caption(
                f"{teacher['students_mastered']} Mastered · "
                f"{teacher['students_assessed']} assessed · "
                f"{teacher['roster_students']} rostered"
            )


# ---------------------------------------------------------------------
# Core Idea diagnostics
# ---------------------------------------------------------------------

st.subheader("What the data is telling us")

if latest is None:
    st.caption(
        "Core Idea diagnostics will appear after CFA results are submitted."
    )
else:
    core_ideas = [
        row
        for row in latest["core_idea_performance"]
        if row["percent"] is not None
    ]

    if not core_ideas:
        st.caption(
            "Map CFA questions to Core Ideas to populate this analysis."
        )
    else:
        weakest = min(
            core_ideas,
            key=lambda row: row["percent"],
        )
        strongest = max(
            core_ideas,
            key=lambda row: row["percent"],
        )

        signal_columns = st.columns(3)

        signal_columns[0].metric(
            "Strongest Core Idea",
            strongest["core_idea"],
            percent_text(strongest["percent"]),
        )

        signal_columns[1].metric(
            "Needs Attention",
            weakest["core_idea"],
            percent_text(weakest["percent"]),
        )

        signal_columns[2].metric(
            "Students Not Yet Mastered",
            (
                latest["counts"]["Approaching"]
                + latest["counts"]["Developing"]
                + latest["counts"]["Intensive"]
            ),
        )

        core_frame = pd.DataFrame(
            [
                {
                    "Core Idea": row["core_idea"],
                    "Performance": row["percent"],
                }
                for row in sorted(
                    core_ideas,
                    key=lambda item: item["percent"],
                )
            ]
        )

        st.dataframe(
            core_frame,
            hide_index=True,
            width="stretch",
            column_config={
                "Performance": st.column_config.ProgressColumn(
                    "Performance",
                    min_value=0,
                    max_value=100,
                    format="%.1f%%",
                ),
            },
        )

        with st.expander(
            "Question-level evidence"
        ):
            question_frame = pd.DataFrame(
                [
                    {
                        "Question": row["question"],
                        "Standard": row["standard"],
                        "Core Idea": row["core_idea"],
                        "Students Answered": row[
                            "students_answered"
                        ],
                        "Performance": row["percent"],
                    }
                    for row in sorted(
                        latest["question_performance"],
                        key=lambda item: (
                            item["percent"]
                            if item["percent"] is not None
                            else 101
                        ),
                    )
                ]
            )

            st.dataframe(
                question_frame,
                hide_index=True,
                width="stretch",
                column_config={
                    "Performance": (
                        st.column_config.ProgressColumn(
                            "Performance",
                            min_value=0,
                            max_value=100,
                            format="%.1f%%",
                        )
                    ),
                },
            )

        with st.expander(
            "Review students by mastery status"
        ):
            for status in (
                "Mastered",
                "Approaching",
                "Developing",
                "Intensive",
            ):
                names = [
                    row["student_name"]
                    for row in latest["student_results"]
                    if row["status"] == status
                ]

                st.markdown(
                    f"**{status} ({len(names)}):** "
                    f"{', '.join(names) or 'None'}"
                )


# ---------------------------------------------------------------------
# Discussion + notes
# ---------------------------------------------------------------------

discussion_col, notes_col = st.columns(
    [1.15, 1.5],
    gap="large",
)

with discussion_col:
    st.subheader("Discussion prompts")
    st.caption(
        "Prompts are intentionally not a checklist. "
        "Use the ones that help this meeting."
    )

    for prompt in DISCUSSION_PROMPTS:
        st.markdown(f"- {prompt}")

    if latest and latest["core_idea_performance"]:
        weakest_core = min(
            latest["core_idea_performance"],
            key=lambda row: row["percent"],
        )
        st.info(
            (
                f"Start with **{weakest_core['core_idea']}**. "
                f"Students earned {weakest_core['percent']:.1f}% "
                "of available points on questions mapped to this Core Idea."
            )
        )


with notes_col:
    st.subheader("PLC notes")
    st.caption(
        "Save meeting observations and decisions to the shared cycle history."
    )

    note_key = f"plc_note_{cycle_id}"
    if st.session_state.pop("clear_plc_note_cycle", None) == cycle_id:
        st.session_state.pop(note_key, None)

    note_text = st.text_area(
        "Meeting note",
        height=150,
        placeholder=(
            "What did the team notice? What will change instructionally? "
            "What evidence should we bring back?"
        ),
        key=note_key,
        label_visibility="collapsed",
    )

    current_user = st.session_state.get(
        "current_user"
    )

    if st.button(
        "Save Note",
        type="primary",
        key=f"save_note_{cycle_id}",
    ):
        try:
            create_cycle_note(
                cycle_id=cycle_id,
                note_text=note_text,
                user_id=(
                    int(current_user["user_id"])
                    if current_user
                    else None
                ),
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state.clear_plc_note_cycle = cycle_id
            st.session_state.plc_flash = (
                "PLC note saved."
            )
            st.rerun()

    notes = list_cycle_notes(
        cycle_id,
        limit=8,
    )

    if not notes:
        st.caption(
            "No PLC notes have been saved for this cycle yet."
        )
    else:
        st.markdown("##### Recent notes")

        for note in notes:
            with st.container(border=True):
                st.caption(
                    f"{note['author_name']} · "
                    f"{note['created_at']}"
                )
                st.write(note["note_text"])


# ---------------------------------------------------------------------
# Assessment evidence
# ---------------------------------------------------------------------

st.subheader("Assessment evidence")

evidence = get_cycle_assessment_evidence(
    cycle_id
)

if not evidence:
    st.info(
        "No CFA has been assigned to this PLC cycle yet."
    )
else:
    evidence_frame = pd.DataFrame(
        [
            {
                "CFA": row["assessment_name"],
                "Administration": (
                    row["administration_type"] or "Not yet administered"
                ),
                "Date": row["administered_on"] or "—",
                "Status": (
                    row["administration_status"]
                    or row["assignment_status"]
                ),
                "Standards": row["standards"] or "—",
                "Sections": row["section_count"],
            }
            for row in evidence
        ]
    )

    st.dataframe(
        evidence_frame,
        hide_index=True,
        width="stretch",
    )

action_columns = st.columns(4)

if action_columns[0].button(
    "Find / Assign CFA",
    type="primary",
    width="stretch",
):
    st.session_state.selected_cycle_id = cycle_id
    st.switch_page("views/assessments.py")

if action_columns[1].button(
    "Review CFA Results",
    width="stretch",
):
    st.switch_page("views/cfa_results.py")

if action_columns[2].button(
    "Open Student Groups",
    width="stretch",
):
    st.switch_page("views/student_groups.py")

if action_columns[3].button(
    "Manage Interventions",
    width="stretch",
):
    st.switch_page("views/interventions.py")


# ---------------------------------------------------------------------
# Commitments remain available, but no longer drive the PLC workflow.
# ---------------------------------------------------------------------

st.subheader("Next commitments")

commitment_header, commitment_button = st.columns(
    [4, 1.2]
)

commitment_header.caption(
    "Capture specific ownership after the team has made an instructional decision."
)

if commitment_button.button(
    "+ Add Commitment",
    width="stretch",
):
    commitment_dialog(
        cycle_id,
        members,
    )


commitments = list_commitments(
    cycle_id
)

if not commitments:
    st.caption(
        "No commitments have been recorded for this cycle."
    )
else:
    for commitment in commitments:
        with st.container(border=True):
            text_col, action_col = st.columns(
                [5, 1]
            )

            text_col.markdown(
                f"**{commitment['name']}** · "
                f"{commitment['status']}"
            )
            text_col.write(
                commitment["action_step"]
            )
            text_col.caption(
                f"Owner: {commitment['assigned_teacher']} · "
                f"Due: {commitment['due_date']}"
            )

            if commitment["evidence"]:
                text_col.caption(
                    f"Evidence: {commitment['evidence']}"
                )

            target_status = (
                "Complete"
                if commitment["status"] == "Open"
                else "Open"
            )
            button_label = (
                "Mark complete"
                if target_status == "Complete"
                else "Reopen"
            )

            if action_col.button(
                button_label,
                key=(
                    "commitment_status_"
                    f"{commitment['commitment_id']}"
                ),
                width="stretch",
            ):
                set_commitment_status(
                    commitment["commitment_id"],
                    target_status,
                )
                st.rerun()
