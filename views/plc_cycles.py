"""Term calendar of expandable weekly guided PLC workspaces."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.cycles import (
    get_cycle_analysis,
    get_cycle_standard_mastery,
    get_team_members,
)
from repositories.term_planning import (
    MEETING_STEPS,
    assign_cycle_to_week,
    clear_week_assignment,
    create_week_cycle,
    current_week_id,
    get_term_weeks,
    list_cycles_for_team,
    list_team_standards,
    list_terms,
    list_visible_teams,
    list_week_notes,
    save_week_note,
    set_week_progress,
)


PROFICIENCY_COLORS = {
    "Mastered": "#1f77b4",
    "Approaching": "#eadc19",
    "Developing": "#ff7f0e",
    "Intensive": "#d62728",
    None: "#9CA3AF",
}

STEP_PROMPTS = {
    0: [
        "What is the essential learning for this week?",
        "Which standard and Core Ideas should every student leave with?",
        "What prerequisite knowledge might prevent access?",
    ],
    1: [
        "What evidence will tell us whether each student learned it?",
        "Which CFA questions map to the Core Ideas we care about?",
        "Do we have common success criteria across classrooms?",
    ],
    2: [
        "What do students understand, and where are the misconceptions?",
        "Which Core Ideas are weakest across the team?",
        "Are the same patterns appearing in every class period?",
    ],
    3: [
        "Who needs prerequisite support, reteaching, or enrichment?",
        "What will we do differently instructionally?",
        "Which practice from a stronger classroom should the team replicate?",
    ],
    4: [
        "When will we check learning again?",
        "What evidence will show the response worked?",
        "What should carry into next week's PLC conversation?",
    ],
    5: [
        "This week's guided PLC cycle is complete.",
        "Review the notes and evidence before moving into the next week.",
    ],
}


def pct(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "—"


def date_label(start: str, end: str) -> str:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)

    if start_date.month == end_date.month:
        return f"{start_date.strftime('%b')} {start_date.day}–{end_date.day}"
    return (
        f"{start_date.strftime('%b')} {start_date.day}–"
        f"{end_date.strftime('%b')} {end_date.day}"
    )


def color_bar(status: str | None) -> None:
    color = PROFICIENCY_COLORS.get(status, "#9CA3AF")
    st.markdown(
        f"<div style='height:6px;border-radius:6px;background:{color};margin-bottom:8px;'></div>",
        unsafe_allow_html=True,
    )


page_header(
    "Weekly PLC calendar",
    "PLC Cycles",
    "Plan the term week by week. Expand only the week you are working in, "
    "connect it to a PLC cycle, and use the guided conversation inside.",
)

current_user = st.session_state.get("current_user")
teams = list_visible_teams(current_user)
terms = list_terms()

if not teams:
    st.info("No PLC teams are visible for this user.")
    st.stop()

if not terms:
    st.error(
        "No school terms exist yet. Run data/term1_2026_27_seed.sql after "
        "adding the weekly PLC schema."
    )
    st.stop()

team_by_label = {
    (
        f"{team['name']} · {team['grade_level']} {team['subject']} · "
        f"{team['school_name']}"
    ): team
    for team in teams
}

saved_team_id = st.session_state.get("plc_calendar_team_id")
team_labels = list(team_by_label)
default_team_index = next(
    (
        index
        for index, label in enumerate(team_labels)
        if int(team_by_label[label]["team_id"]) == int(saved_team_id)
    ),
    0,
) if saved_team_id is not None else 0

filter_left, filter_right = st.columns([3.4, 1.4])

with filter_left:
    selected_team_label = st.selectbox(
        "PLC Team",
        team_labels,
        index=default_team_index,
    )

selected_team = team_by_label[selected_team_label]
team_id = int(selected_team["team_id"])
st.session_state.plc_calendar_team_id = team_id

term_by_label = {
    f"{term['term_name']} · {term['school_year']}": term
    for term in terms
}

with filter_right:
    selected_term_label = st.selectbox(
        "Term",
        list(term_by_label),
    )

selected_term = term_by_label[selected_term_label]
term_id = int(selected_term["term_id"])

weeks = get_term_weeks(term_id, team_id)
current_id = current_week_id(term_id)

assigned_count = sum(
    bool(week["week_assignment_id"])
    for week in weeks
)
complete_count = sum(
    int(week["completed_steps"] or 0) == len(MEETING_STEPS)
    for week in weeks
)
pacing_weeks = sum(
    bool(week["pacing_standards"])
    for week in weeks
)

with st.container(border=True):
    team_col, metrics_col = st.columns([2.7, 2.3])

    with team_col:
        st.subheader(selected_team["name"])
        st.caption(
            f"{selected_team['school_name']} · "
            f"{selected_team['grade_level']} {selected_team['subject']}"
        )
        st.markdown(
            f"**{selected_term['term_name']}** · "
            f"{selected_term['start_date']} → {selected_term['end_date']}"
        )

    with metrics_col:
        metric_cols = st.columns(3)
        metric_cols[0].metric("Weeks", len(weeks))
        metric_cols[1].metric("PLC Assigned", assigned_count)
        metric_cols[2].metric("Completed", complete_count)

if pacing_weeks:
    st.info(
        f"District pacing guidance is available for {pacing_weeks} week(s). "
        "Those standards appear inside the corresponding week."
    )
else:
    st.caption(
        "No district pacing guide is loaded for this team. "
        "Assign an existing PLC cycle or create a weekly cycle directly from each week."
    )

cycles = list_cycles_for_team(team_id)
standards = list_team_standards(team_id)

cycle_by_label = {
    (
        f"{cycle['name']} · {cycle['standard']} · "
        f"{cycle['start_date']} → {cycle['end_date']}"
    ): cycle
    for cycle in cycles
}

standard_by_label = {
    f"{standard['code']} · {standard['description']}": standard
    for standard in standards
}

if message := st.session_state.pop("weekly_plc_flash", None):
    st.success(message)

for week in weeks:
    week_id = int(week["week_id"])
    completed_steps = int(week["completed_steps"] or 0)
    assignment = bool(week["week_assignment_id"])
    is_current = current_id == week_id

    if assignment:
        cycle_text = (
            f"{week['cycle_standard']} · {week['cycle_name']}"
            if week["cycle_name"]
            else "Cycle removed"
        )
        progress_text = f"{completed_steps}/5"
    elif week["pacing_standards"]:
        cycle_text = f"District pacing: {week['pacing_standards']}"
        progress_text = "Not started"
    else:
        cycle_text = "No PLC assigned"
        progress_text = "Not started"

    prefix = "CURRENT · " if is_current else ""
    expander_label = (
        f"{prefix}{week['label']} · "
        f"{date_label(week['week_start_date'], week['week_end_date'])} · "
        f"{cycle_text} · {progress_text}"
    )

    with st.expander(
        expander_label,
        expanded=is_current or (current_id is None and week["week_number"] == 1),
    ):
        week_header, week_status = st.columns([4, 1.2])

        week_header.markdown(
            f"### {week['label']} · "
            f"{date_label(week['week_start_date'], week['week_end_date'])}"
        )

        if week["pacing_standards"]:
            week_header.info(
                f"District pacing: **{week['pacing_standards']}**"
                + (
                    f" — {week['pacing_focus']}"
                    if week["pacing_focus"]
                    else ""
                )
            )

        if not assignment or not week["cycle_id"]:
            week_status.caption("Weekly PLC workspace")
            week_status.markdown("**Unassigned**")

            assign_tab, create_tab = st.tabs(
                ["Assign existing PLC", "Create weekly PLC"]
            )

            with assign_tab:
                if not cycle_by_label:
                    st.caption(
                        "This team does not have an existing PLC cycle yet."
                    )
                else:
                    existing_label = st.selectbox(
                        "PLC cycle",
                        list(cycle_by_label),
                        key=f"existing_cycle_{week_id}",
                    )
                    existing_cycle = cycle_by_label[existing_label]

                    if st.button(
                        "Assign to this week",
                        type="primary",
                        key=f"assign_cycle_{week_id}",
                    ):
                        assign_cycle_to_week(
                            team_id=team_id,
                            week_id=week_id,
                            cycle_id=int(existing_cycle["cycle_id"]),
                            assignment_source=(
                                "District Pacing"
                                if week["pacing_standards"]
                                else "Team Assigned"
                            ),
                        )
                        st.session_state.weekly_plc_flash = (
                            f"{existing_cycle['name']} assigned to {week['label']}."
                        )
                        st.rerun()

            with create_tab:
                if not standard_by_label:
                    st.warning(
                        "No standards match this team's grade and subject."
                    )
                else:
                    default_name = (
                        f"{week['label']} PLC · "
                        f"{selected_team['subject']}"
                    )

                    with st.form(f"create_week_cycle_{week_id}"):
                        standard_label = st.selectbox(
                            "Standard",
                            list(standard_by_label),
                        )
                        cycle_name = st.text_input(
                            "PLC cycle name",
                            value=default_name,
                        )
                        create = st.form_submit_button(
                            "Create and assign",
                            type="primary",
                        )

                    if create:
                        standard = standard_by_label[standard_label]
                        create_week_cycle(
                            team_id=team_id,
                            week_id=week_id,
                            standard_id=int(standard["standard_id"]),
                            cycle_name=cycle_name,
                            assignment_source=(
                                "District Pacing"
                                if week["pacing_standards"]
                                else "Manual"
                            ),
                        )
                        st.session_state.weekly_plc_flash = (
                            f"Created a PLC cycle for {week['label']}."
                        )
                        st.rerun()

            continue

        cycle_id = int(week["cycle_id"])
        cycle = get_cycle_analysis(cycle_id)

        if cycle is None:
            st.warning(
                "This week points to a PLC cycle that no longer exists. "
                "Clear the assignment and select another cycle."
            )
            if st.button(
                "Clear week assignment",
                key=f"clear_missing_{week_id}",
            ):
                clear_week_assignment(team_id, week_id)
                st.rerun()
            continue

        week_status.caption(week["assignment_source"])
        week_status.markdown(
            f"**{completed_steps} of {len(MEETING_STEPS)} complete**"
        )

        st.progress(
            completed_steps / len(MEETING_STEPS),
            text=(
                "Weekly PLC complete"
                if completed_steps == len(MEETING_STEPS)
                else f"Current focus: {MEETING_STEPS[completed_steps]}"
            ),
        )

        step_cols = st.columns(len(MEETING_STEPS))
        for index, (column, label) in enumerate(
            zip(step_cols, MEETING_STEPS),
            start=1,
        ):
            if index <= completed_steps:
                marker = "✓"
            elif index == completed_steps + 1:
                marker = "●"
            else:
                marker = "○"
            column.caption(f"{marker} {label}")

        latest = cycle["latest"]
        members = get_team_members(cycle_id)

        with st.container(border=True):
            cycle_title, cycle_date = st.columns([3.6, 1.4])
            cycle_title.markdown(f"#### {cycle['name']}")
            cycle_title.caption(
                f"{cycle['plc']} · "
                + ", ".join(
                    standard["code"]
                    for standard in cycle["standards"]
                )
            )
            cycle_date.caption(
                f"{cycle['start_date']} → {cycle['end_date']}"
            )
            if members:
                cycle_title.caption(
                    "Team: "
                    + ", ".join(
                        member["display_name"]
                        for member in members
                    )
                )

        evidence_col, discussion_col = st.columns(
            [1.35, 1],
            gap="large",
        )

        with evidence_col:
            st.markdown("#### Evidence snapshot")

            if latest is None:
                st.caption(
                    "No submitted CFA evidence yet. "
                    "Use this week's Evidence step to decide what will be collected."
                )
            else:
                students_assessed = int(latest["completed"])
                mastered = int(latest["counts"]["Mastered"])
                mastery_rate = (
                    mastered / students_assessed * 100
                    if students_assessed
                    else None
                )

                metric_cols = st.columns(3)
                metric_cols[0].metric(
                    "Students Assessed",
                    students_assessed,
                )
                metric_cols[1].metric(
                    "Mastery",
                    pct(mastery_rate),
                )
                metric_cols[2].metric(
                    "Growth",
                    (
                        f"{cycle['growth_points']:+.1f} pts"
                        if cycle["growth_points"] is not None
                        else "—"
                    ),
                )

                core_ideas = [
                    row
                    for row in latest["core_idea_performance"]
                    if row["percent"] is not None
                ]

                if core_ideas:
                    weakest = min(
                        core_ideas,
                        key=lambda row: row["percent"],
                    )
                    strongest = max(
                        core_ideas,
                        key=lambda row: row["percent"],
                    )

                    signal_cols = st.columns(2)
                    signal_cols[0].metric(
                        "Needs Attention",
                        weakest["core_idea"],
                        pct(weakest["percent"]),
                    )
                    signal_cols[1].metric(
                        "Strongest Core Idea",
                        strongest["core_idea"],
                        pct(strongest["percent"]),
                    )

                standard_mastery = get_cycle_standard_mastery(
                    cycle_id,
                    latest["administration_id"],
                )

                if standard_mastery:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Standard": row["code"],
                                    "Students Mastered": (
                                        f"{row['students_mastered']} / "
                                        f"{row['students_assessed']}"
                                    ),
                                    "Mastery": row["mastery_rate"],
                                }
                                for row in standard_mastery
                            ]
                        ),
                        hide_index=True,
                        width="stretch",
                        column_config={
                            "Mastery": st.column_config.ProgressColumn(
                                "Mastery",
                                min_value=0,
                                max_value=100,
                                format="%.0f%%",
                            ),
                        },
                    )

        with discussion_col:
            step_index = min(
                completed_steps,
                len(MEETING_STEPS),
            )
            st.markdown(
                "#### "
                + (
                    "Close the week"
                    if step_index == len(MEETING_STEPS)
                    else MEETING_STEPS[step_index]
                )
            )

            for prompt in STEP_PROMPTS[step_index]:
                st.markdown(f"- {prompt}")

            back_col, complete_col = st.columns(2)

            if back_col.button(
                "Back",
                key=f"week_back_{week_id}",
                disabled=completed_steps == 0,
                width="stretch",
            ):
                set_week_progress(
                    int(week["week_assignment_id"]),
                    completed_steps - 1,
                )
                st.rerun()

            if complete_col.button(
                "Complete step",
                key=f"week_complete_{week_id}",
                type="primary",
                disabled=completed_steps == len(MEETING_STEPS),
                width="stretch",
            ):
                set_week_progress(
                    int(week["week_assignment_id"]),
                    completed_steps + 1,
                )
                st.rerun()

        notes_col, links_col = st.columns(
            [1.5, 1],
            gap="large",
        )

        with notes_col:
            st.markdown("#### Weekly PLC notes")
            note_key = f"weekly_note_{week_id}"
            note = st.text_area(
                "Meeting note",
                key=note_key,
                height=110,
                placeholder=(
                    "What did the team notice, decide, or commit to for this week?"
                ),
                label_visibility="collapsed",
            )

            if st.button(
                "Save note",
                key=f"save_week_note_{week_id}",
            ):
                try:
                    save_week_note(
                        week_assignment_id=int(
                            week["week_assignment_id"]
                        ),
                        user_id=(
                            int(current_user["user_id"])
                            if current_user
                            else None
                        ),
                        note_text=note,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state[note_key] = ""
                    st.session_state.weekly_plc_flash = (
                        f"Note saved for {week['label']}."
                    )
                    st.rerun()

            notes = list_week_notes(
                int(week["week_assignment_id"])
            )

            for saved_note in notes[:3]:
                with st.container(border=True):
                    st.caption(
                        f"{saved_note['author_name']} · "
                        f"{saved_note['created_at']}"
                    )
                    st.write(saved_note["note_text"])

        with links_col:
            st.markdown("#### Continue the work")

            if st.button(
                "Find / Assign CFA",
                key=f"weekly_cfa_{week_id}",
                type="primary",
                width="stretch",
            ):
                st.session_state.selected_cycle_id = cycle_id
                st.switch_page("views/assessments.py")

            if st.button(
                "Review CFA Results",
                key=f"weekly_results_{week_id}",
                width="stretch",
            ):
                st.switch_page("views/cfa_results.py")

            if st.button(
                "Open Student Groups",
                key=f"weekly_groups_{week_id}",
                width="stretch",
            ):
                st.session_state.selected_cycle_id = cycle_id
                st.switch_page("views/student_groups.py")

            if st.button(
                "Standards Map",
                key=f"weekly_standards_{week_id}",
                width="stretch",
            ):
                st.switch_page("views/standards.py")

            st.write("")
            if st.button(
                "Clear this week's PLC assignment",
                key=f"clear_week_{week_id}",
                width="stretch",
            ):
                clear_week_assignment(team_id, week_id)
                st.session_state.weekly_plc_flash = (
                    f"Cleared {week['label']} assignment."
                )
                st.rerun()
