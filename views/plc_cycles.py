"""Weekly PLC workspace: plan, assess, analyze, respond, and reassess in one place."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.plc_instruction import (
    MASTERY_STATUSES,
    RESPONSE_DEFAULTS,
    RESPONSE_TYPES,
    assign_cfa_to_cycle,
    create_or_get_post_reassessment,
    get_cycle_instruction_workspace,
    list_compatible_cfas,
    list_visible_cycle_sections,
    save_instructional_responses,
)
from repositories.term_planning import (
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
)


PROFICIENCY_COLORS = {
    "Mastered": "#1f77b4",
    "Approaching": "#eadc19",
    "Developing": "#ff7f0e",
    "Intensive": "#d62728",
}

GROUP_BACKGROUNDS = {
    "Mastered": "rgba(31,119,180,0.12)",
    "Approaching": "rgba(234,220,25,0.18)",
    "Developing": "rgba(255,127,14,0.14)",
    "Intensive": "rgba(214,39,40,0.12)",
}


# ---------- Formatting ----------

def pct(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "—"


def date_label(start: str, end: str) -> str:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if start_date.month == end_date.month:
        return f"{start_date.strftime('%b')} {start_date.day}–{end_date.day}"
    return (
        f"{start_date.strftime('%b')} {start_date.day}–"
        f"{end_date.strftime('%b')} {end_date.day}"
    )


def _pill(text: str, background: str = "#F3F4F6", color: str = "#374151") -> str:
    return (
        "<span style='display:inline-block;padding:4px 9px;margin-left:5px;"
        f"border-radius:999px;background:{background};color:{color};"
        "font-size:0.78rem;font-weight:600;white-space:nowrap;'>"
        f"{escape(text)}</span>"
    )


def week_pills(snapshot: dict | None) -> str:
    if snapshot is None:
        return _pill("No PLC assigned")

    assignments = snapshot["assignments"]
    latest = snapshot["latest"]
    previous = snapshot["previous"]
    growth = snapshot["growth"]

    if not assignments:
        return _pill("CFA not assigned", "#FEF3C7", "#92400E")

    if latest is None:
        return _pill("CFA evidence pending", "#E0F2FE", "#075985")

    if previous and growth["previous_mastery_rate"] is not None:
        mastery_text = (
            f"Mastery {pct(growth['previous_mastery_rate'])} → "
            f"{pct(growth['latest_mastery_rate'])}"
        )
        count_change = growth["mastery_count_change"] or 0
        count_text = (
            f"{count_change:+d} student"
            f"{'s' if abs(count_change) != 1 else ''} mastered"
        )
        return (
            _pill(mastery_text, "#DBEAFE", "#1E3A8A")
            + _pill(
                count_text,
                "#DCFCE7" if count_change >= 0 else "#FEE2E2",
                "#166534" if count_change >= 0 else "#991B1B",
            )
        )

    return _pill(
        f"Mastery {pct(latest['mastery_rate'])} · "
        f"{latest['mastered']}/{latest['completed']} students",
        "#DBEAFE",
        "#1E3A8A",
    )


def render_group_card(group: dict, cycle_id: int, administration_id: int) -> None:
    status = group["status"]
    count = int(group["count"])
    color = PROFICIENCY_COLORS[status]
    background = GROUP_BACKGROUNDS[status]
    weakest = group["weakest_core_idea"] or "No evidence"
    weakest_percent = (
        f" · {pct(group['weakest_percent'])}"
        if group["weakest_percent"] is not None
        else ""
    )

    st.markdown(
        f"""
        <div style="border:1px solid #E5E7EB;border-top:5px solid {color};
                    background:{background};border-radius:10px;padding:13px 14px;
                    min-height:170px;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <strong style="font-size:1.02rem;">{escape(status)}</strong>
            <span style="font-size:1.25rem;font-weight:700;">{count}</span>
          </div>
          <div style="margin-top:10px;font-size:0.82rem;color:#4B5563;">Recommended</div>
          <div style="font-weight:650;">{escape(group['recommended_response'])}</div>
          <div style="margin-top:10px;font-size:0.82rem;color:#4B5563;">Instructional signal</div>
          <div>{escape(weakest)}{escape(weakest_percent)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    accept_col, customize_col = st.columns(2)
    type_key = f"response_type_{cycle_id}_{administration_id}_{status}"
    strategy_key = f"response_strategy_{cycle_id}_{administration_id}_{status}"
    custom_key = f"response_custom_{cycle_id}_{administration_id}_{status}"

    if accept_col.button(
        "Accept",
        key=f"accept_{cycle_id}_{administration_id}_{status}",
        disabled=count == 0,
        width="stretch",
    ):
        st.session_state[type_key] = group["recommended_response"]
        st.session_state[strategy_key] = group["recommended_strategy"]
        st.session_state[custom_key] = False
        st.rerun()

    if customize_col.button(
        "Customize",
        key=f"customize_{cycle_id}_{administration_id}_{status}",
        disabled=count == 0,
        width="stretch",
    ):
        st.session_state[custom_key] = True
        st.rerun()


# ---------- Dialogs ----------

@st.dialog("Review Students", width="large")
def review_students_dialog(latest: dict) -> None:
    st.caption(
        f"{latest['assessment_name']} · {latest['administration_type']} · "
        f"{latest['administered_on']}"
    )
    tabs = st.tabs(list(MASTERY_STATUSES))
    group_by_status = {group["status"]: group for group in latest["groups"]}

    for tab, status in zip(tabs, MASTERY_STATUSES):
        with tab:
            group = group_by_status[status]
            students = group["students"]
            if not students:
                st.caption("No students are currently in this mastery band.")
                continue
            frame = pd.DataFrame(
                [
                    {
                        "Student": student["student_name"],
                        "Score": student["percent"],
                        "Status": student["status"],
                    }
                    for student in students
                ]
            )
            st.dataframe(
                frame,
                hide_index=True,
                width="stretch",
                column_config={
                    "Score": st.column_config.ProgressColumn(
                        "Score",
                        min_value=0,
                        max_value=100,
                        format="%.0f%%",
                    )
                },
            )


@st.dialog("Find / Assign CFA", width="large")
def assign_cfa_dialog(
    cycle_id: int,
    current_user: dict | None,
    preselected_assessment_id: int | None = None,
) -> None:
    st.caption(
        "Choose a reusable CFA that measures at least one standard in this PLC cycle. "
        "The CFA is linked here; you do not need to leave the weekly workspace."
    )

    search = st.text_input(
        "Search CFA library",
        placeholder="Assessment name, type, or standard",
        key=f"cfa_search_{cycle_id}",
    )
    cfas = list_compatible_cfas(cycle_id, current_user, search)

    if not cfas:
        st.info("No compatible CFAs match this search.")
        if st.button("Create a new CFA", type="primary", width="stretch"):
            st.session_state.assessment_target_cycle_id = cycle_id
            st.session_state.show_assessment_form = True
            st.session_state.selected_assessment_id = None
            st.switch_page("views/assessments.py")
        return

    cfa_by_label = {
        (
            f"{row['name']} · {row['overlapping_standards'] or row['standards']} · "
            f"{row['question_count']} questions"
        ): row
        for row in cfas
    }
    labels = list(cfa_by_label)
    default_index = 0
    if preselected_assessment_id is not None:
        default_index = next(
            (
                index
                for index, label in enumerate(labels)
                if int(cfa_by_label[label]["assessment_id"])
                == int(preselected_assessment_id)
            ),
            0,
        )

    selected_label = st.selectbox(
        "CFA",
        labels,
        index=default_index,
        key=f"cfa_choice_{cycle_id}",
    )
    selected = cfa_by_label[selected_label]

    info_cols = st.columns(4)
    info_cols[0].metric("Status", selected["status"])
    info_cols[1].metric("Questions", selected["question_count"])
    info_cols[2].metric("Points", f"{float(selected['possible_points']):g}")
    info_cols[3].metric("Standards", selected["overlapping_standards"] or "—")

    if selected["already_assigned"]:
        st.info("This CFA is already linked to this PLC cycle. Saving below will update its class sections.")

    sections = list_visible_cycle_sections(cycle_id, current_user)
    section_by_label = {
        (
            f"{row['teacher_name']} · {row['section_name']} · "
            f"{row['term_name'] or 'Current term'} · {row['student_count']} students"
        ): row
        for row in sections
    }

    selected_section_labels = st.multiselect(
        "Class sections",
        list(section_by_label),
        key=f"cfa_sections_{cycle_id}_{selected['assessment_id']}",
    )

    if not sections:
        st.warning(
            "No visible class sections match this PLC team's grade and subject. "
            "Section assignments must be loaded before score entry can begin."
        )

    assign_col, create_col = st.columns(2)
    if assign_col.button(
        "Assign CFA to PLC",
        type="primary",
        width="stretch",
        disabled=not selected_section_labels,
    ):
        try:
            cycle_assessment_id = assign_cfa_to_cycle(
                current_user=current_user,
                cycle_id=cycle_id,
                assessment_id=int(selected["assessment_id"]),
                section_ids=[
                    int(section_by_label[label]["section_id"])
                    for label in selected_section_labels
                ],
            )
        except (ValueError, PermissionError) as error:
            st.error(str(error))
        else:
            st.session_state.cfa_cycle_assessment_id = cycle_assessment_id
            st.session_state.weekly_plc_flash = (
                f"{selected['name']} is now assigned to this PLC cycle."
            )
            st.session_state.pop("plc_assign_assessment_id", None)
            st.rerun()

    if create_col.button("Create a new CFA", width="stretch"):
        st.session_state.assessment_target_cycle_id = cycle_id
        st.session_state.show_assessment_form = True
        st.session_state.selected_assessment_id = None
        st.switch_page("views/assessments.py")


# ---------- Page ----------

page_header(
    "Weekly instructional execution",
    "PLC Cycles",
    "Keep the PLC conversation in one place: connect the learning, review CFA evidence, "
    "decide the instructional response, and plan the reassessment.",
)

current_user = st.session_state.get("current_user")
teams = list_visible_teams(current_user)
terms = list_terms()

if not teams:
    st.info("No PLC teams are visible for this user.")
    st.stop()
if not terms:
    st.error("No school terms exist yet. Load the weekly PLC calendar schema and term seed.")
    st.stop()

team_by_label = {
    f"{team['name']} · {team['grade_level']} {team['subject']} · {team['school_name']}": team
    for team in teams
}
team_labels = list(team_by_label)
saved_team_id = st.session_state.get("plc_calendar_team_id")
default_team_index = next(
    (
        index
        for index, label in enumerate(team_labels)
        if saved_team_id is not None
        and int(team_by_label[label]["team_id"]) == int(saved_team_id)
    ),
    0,
)

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
    selected_term_label = st.selectbox("Term", list(term_by_label))
selected_term = term_by_label[selected_term_label]
term_id = int(selected_term["term_id"])

weeks = get_term_weeks(term_id, team_id)
current_id = current_week_id(term_id)
cycles = list_cycles_for_team(team_id)
standards = list_team_standards(team_id)

cycle_by_label = {
    f"{cycle['name']} · {cycle['standard']} · {cycle['start_date']} → {cycle['end_date']}": cycle
    for cycle in cycles
}
standard_by_label = {
    f"{standard['code']} · {standard['description']}": standard
    for standard in standards
}

# One evidence snapshot per unique cycle keeps the collapsed weekly rows informative.
snapshot_cache: dict[int, dict] = {}
for week in weeks:
    if week["cycle_id"] is not None:
        cycle_id = int(week["cycle_id"])
        if cycle_id not in snapshot_cache:
            try:
                snapshot_cache[cycle_id] = get_cycle_instruction_workspace(
                    cycle_id,
                    current_user,
                )
            except (ValueError, PermissionError):
                snapshot_cache[cycle_id] = None

assigned_count = sum(bool(week["cycle_id"]) for week in weeks)
evidence_ready = sum(
    bool(snapshot and snapshot["latest"])
    for snapshot in snapshot_cache.values()
)
responses_saved = sum(
    bool(snapshot and snapshot["saved_responses"])
    for snapshot in snapshot_cache.values()
)

with st.container(border=True):
    title_col, metrics_col = st.columns([2.8, 2.2])
    with title_col:
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
        cols = st.columns(3)
        cols[0].metric("PLC Weeks", assigned_count)
        cols[1].metric("Evidence Ready", evidence_ready)
        cols[2].metric("Responses Saved", responses_saved)

if message := st.session_state.pop("weekly_plc_flash", None):
    st.success(message)

# A newly created CFA can hand back to the correct cycle and reopen assignment here.
returned_assessment_id = st.session_state.get("plc_assign_assessment_id")
returned_cycle_id = st.session_state.get("assessment_target_cycle_id")
if returned_assessment_id is not None and returned_cycle_id is not None:
    # Clear the target before opening so navigation back never loops.
    st.session_state.pop("assessment_target_cycle_id", None)
    assign_cfa_dialog(
        int(returned_cycle_id),
        current_user,
        int(returned_assessment_id),
    )

for week in weeks:
    week_id = int(week["week_id"])
    cycle_id = int(week["cycle_id"]) if week["cycle_id"] is not None else None
    snapshot = snapshot_cache.get(cycle_id) if cycle_id is not None else None
    is_current = current_id == week_id
    open_key = f"plc_week_open_{week_id}"

    if open_key not in st.session_state:
        st.session_state[open_key] = bool(
            is_current or (current_id is None and int(week["week_number"]) == 1)
        )

    with st.container(border=True):
        left, right = st.columns([4.7, 2.3], vertical_alignment="center")
        cycle_name = week["cycle_name"] or "No PLC assigned"
        prefix = "CURRENT · " if is_current else ""
        arrow = "▾" if st.session_state[open_key] else "▸"
        if left.button(
            f"{arrow} {prefix}{week['label']} · "
            f"{date_label(week['week_start_date'], week['week_end_date'])} · {cycle_name}",
            key=f"toggle_week_{week_id}",
            width="stretch",
        ):
            st.session_state[open_key] = not st.session_state[open_key]
            st.rerun()

        right.markdown(
            f"<div style='text-align:right;'>{week_pills(snapshot)}</div>",
            unsafe_allow_html=True,
        )

        if not st.session_state[open_key]:
            continue

        st.divider()

        if cycle_id is None:
            if week["pacing_standards"]:
                st.info(
                    f"District pacing: **{week['pacing_standards']}**"
                    + (f" — {week['pacing_focus']}" if week["pacing_focus"] else "")
                )

            assign_tab, create_tab = st.tabs(["Assign existing PLC", "Create weekly PLC"])
            with assign_tab:
                if not cycle_by_label:
                    st.caption("This team does not have an existing PLC cycle yet.")
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
                    st.warning("No standards match this team's grade and subject.")
                else:
                    with st.form(f"create_week_cycle_{week_id}"):
                        standard_label = st.selectbox("Standard", list(standard_by_label))
                        cycle_name_input = st.text_input(
                            "PLC cycle name",
                            value=f"{week['label']} PLC · {selected_team['subject']}",
                        )
                        create_clicked = st.form_submit_button(
                            "Create and assign",
                            type="primary",
                        )
                    if create_clicked:
                        standard = standard_by_label[standard_label]
                        create_week_cycle(
                            team_id=team_id,
                            week_id=week_id,
                            standard_id=int(standard["standard_id"]),
                            cycle_name=cycle_name_input,
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

        if snapshot is None:
            st.warning("This PLC cycle could not be loaded for the signed-in user.")
            continue

        cycle = snapshot["cycle"]
        latest = snapshot["latest"]
        assignments = snapshot["assignments"]
        growth = snapshot["growth"]

        header_left, header_right = st.columns([3.6, 1.4])
        with header_left:
            st.markdown(f"### {cycle['name']}")
            st.caption(
                f"{cycle['team_name']} · {cycle['primary_standard']} · "
                f"{cycle['grade_level']} {cycle['subject']}"
            )
        with header_right:
            if assignments:
                st.caption("Assigned CFA")
                st.markdown(f"**{assignments[0]['assessment_name']}**")
            else:
                st.caption("Evidence source")
                st.markdown("**CFA needed**")

        if week["pacing_standards"]:
            st.caption(
                f"District pacing this week: {week['pacing_standards']}"
                + (f" · {week['pacing_focus']}" if week["pacing_focus"] else "")
            )

        evidence_col, groups_col = st.columns([1.05, 1.45], gap="large")

        with evidence_col:
            st.markdown("#### CFA Evidence")

            if not assignments:
                st.info(
                    "Connect a CFA to this cycle before collecting common evidence. "
                    "You can assign an existing CFA without leaving this page."
                )
                action_1, action_2 = st.columns(2)
                if action_1.button(
                    "Find / Assign CFA",
                    type="primary",
                    key=f"find_cfa_{week_id}",
                    width="stretch",
                ):
                    assign_cfa_dialog(cycle_id, current_user)
                if action_2.button(
                    "Create CFA",
                    key=f"create_cfa_{week_id}",
                    width="stretch",
                ):
                    st.session_state.assessment_target_cycle_id = cycle_id
                    st.session_state.show_assessment_form = True
                    st.session_state.selected_assessment_id = None
                    st.switch_page("views/assessments.py")

            elif latest is None:
                assignment = assignments[0]
                st.markdown(f"**{assignment['assessment_name']}**")
                st.caption(
                    f"{assignment['standards']} · {assignment['section_count']} section(s) assigned"
                )
                st.info("The CFA is assigned. Enter and submit results to populate the mastery groups.")
                enter_col, change_col = st.columns(2)
                if enter_col.button(
                    "Enter CFA Results",
                    type="primary",
                    key=f"enter_cfa_{week_id}",
                    width="stretch",
                ):
                    st.session_state.cfa_cycle_assessment_id = int(
                        assignment["cycle_assessment_id"]
                    )
                    st.session_state.pop("cfa_administration_id", None)
                    st.switch_page("views/cfa_data_entry.py")
                if change_col.button(
                    "Change CFA / Sections",
                    key=f"change_cfa_{week_id}",
                    width="stretch",
                ):
                    assign_cfa_dialog(cycle_id, current_user)

            else:
                evidence_rows = [
                    ("Mastered", latest["counts"]["Mastered"]),
                    ("Approaching", latest["counts"]["Approaching"]),
                    ("Developing", latest["counts"]["Developing"]),
                    ("Intensive", latest["counts"]["Intensive"]),
                ]
                for status, count in evidence_rows:
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;"
                        f"border-left:5px solid {PROFICIENCY_COLORS[status]};"
                        "padding:7px 10px;margin:4px 0;background:#FAFAFA;'>"
                        f"<strong>{status}</strong><strong>{count}</strong></div>",
                        unsafe_allow_html=True,
                    )

                weakest = latest["weakest_core_idea"]
                st.write("")
                st.caption("Weakest Core Idea")
                if weakest:
                    st.markdown(
                        f"**{weakest['core_idea']} — {pct(weakest['percent'])}**"
                    )
                else:
                    st.markdown("**No Core Idea evidence available**")

                if snapshot["previous"]:
                    st.caption("Mastery growth")
                    change = growth["mastery_count_change"] or 0
                    st.markdown(
                        f"**{pct(growth['previous_mastery_rate'])} → "
                        f"{pct(growth['latest_mastery_rate'])} · "
                        f"{change:+d} students at Mastered**"
                    )
                    if growth["newly_mastered_count"] is not None:
                        st.caption(
                            f"{growth['newly_mastered_count']} students moved into Mastered "
                            "from a lower mastery band among students with comparable evidence."
                        )
                else:
                    st.caption(
                        "Growth appears after a second submitted CFA administration. "
                        "It will show the change in students reaching Mastered."
                    )

                action_1, action_2 = st.columns(2)
                if action_1.button(
                    "Review Students",
                    key=f"review_students_{week_id}",
                    width="stretch",
                ):
                    review_students_dialog(latest)
                if action_2.button(
                    "CFA Results",
                    key=f"cfa_results_{week_id}",
                    width="stretch",
                ):
                    st.session_state.selected_cycle_id = cycle_id
                    st.switch_page("views/cfa_results.py")

        with groups_col:
            st.markdown("#### Mastery Groups")
            st.caption(
                "These groups are generated from the latest submitted CFA. "
                "There is nothing separate to save here."
            )

            if latest is None:
                st.caption("Submit CFA evidence to generate the 2×2 instructional grouping map.")
            else:
                group_by_status = {group["status"]: group for group in latest["groups"]}
                grid_rows = [
                    ("Mastered", "Approaching"),
                    ("Developing", "Intensive"),
                ]
                for left_status, right_status in grid_rows:
                    left_card, right_card = st.columns(2)
                    with left_card:
                        render_group_card(
                            group_by_status[left_status],
                            cycle_id,
                            int(latest["administration_id"]),
                        )
                    with right_card:
                        render_group_card(
                            group_by_status[right_status],
                            cycle_id,
                            int(latest["administration_id"]),
                        )

        if latest is not None:
            st.divider()
            st.markdown("#### Instructional Response")
            st.caption(
                "Use the recommendation or customize it. This is the one place the team records "
                "what it will do next; there is no separate Interventions workflow required."
            )

            saved_by_status = {
                row["mastery_status"]: row
                for row in snapshot["saved_responses"]
            }
            response_payloads = []
            group_by_status = {group["status"]: group for group in latest["groups"]}

            for status in MASTERY_STATUSES:
                group = group_by_status[status]
                if int(group["count"]) == 0:
                    continue

                saved = saved_by_status.get(status)
                type_key = f"response_type_{cycle_id}_{latest['administration_id']}_{status}"
                strategy_key = f"response_strategy_{cycle_id}_{latest['administration_id']}_{status}"
                custom_key = f"response_custom_{cycle_id}_{latest['administration_id']}_{status}"

                if type_key not in st.session_state:
                    st.session_state[type_key] = (
                        saved["response_type"] if saved else group["recommended_response"]
                    )
                if strategy_key not in st.session_state:
                    st.session_state[strategy_key] = (
                        saved["strategy"]
                        if saved and saved["strategy"]
                        else group["recommended_strategy"]
                    )
                if custom_key not in st.session_state:
                    st.session_state[custom_key] = bool(
                        saved
                        and (
                            saved["response_type"] != group["recommended_response"]
                            or saved["strategy"] != group["recommended_strategy"]
                        )
                    )

                with st.container(border=True):
                    status_col, response_col = st.columns([1.2, 2.8])
                    with status_col:
                        st.markdown(
                            f"**{status} — {group['count']} student"
                            f"{'s' if group['count'] != 1 else ''}**"
                        )
                        if group["weakest_core_idea"]:
                            st.caption(
                                f"Focus: {group['weakest_core_idea']} · "
                                f"{pct(group['weakest_percent'])}"
                            )
                    with response_col:
                        if st.session_state[custom_key]:
                            response_type = st.selectbox(
                                "Response",
                                RESPONSE_TYPES,
                                key=type_key,
                                label_visibility="collapsed",
                            )
                            strategy = st.text_area(
                                "Strategy",
                                key=strategy_key,
                                height=85,
                                placeholder="What will the team do differently for this group?",
                            )
                        else:
                            response_type = st.session_state[type_key]
                            strategy = st.session_state[strategy_key]
                            st.markdown(f"**{response_type}**")
                            st.caption(strategy)

                response_payloads.append(
                    {
                        "mastery_status": status,
                        "response_type": response_type,
                        "strategy": strategy,
                        "focus_core_idea_id": group["weakest_core_idea_id"],
                        "focus_text": group["weakest_core_idea"],
                        "student_ids": [
                            int(student["student_id"])
                            for student in group["students"]
                        ],
                    }
                )

            saved_reassess = next(
                (
                    row["reassess_date"]
                    for row in snapshot["saved_responses"]
                    if row["reassess_date"]
                ),
                None,
            )
            default_reassess = (
                date.fromisoformat(saved_reassess)
                if saved_reassess
                else max(date.today() + timedelta(days=7), date.fromisoformat(week["week_end_date"]))
            )

            save_left, save_middle, save_right = st.columns([1.2, 1.4, 2.4])
            reassess_date = save_left.date_input(
                "Reassess",
                value=default_reassess,
                key=f"reassess_date_{cycle_id}_{latest['administration_id']}",
            )

            if save_middle.button(
                "Save Response",
                type="primary",
                key=f"save_response_{week_id}",
                width="stretch",
            ):
                try:
                    save_instructional_responses(
                        current_user=current_user,
                        cycle_id=cycle_id,
                        source_administration_id=int(latest["administration_id"]),
                        reassess_date=reassess_date.isoformat(),
                        responses=response_payloads,
                    )
                except (ValueError, PermissionError) as error:
                    st.error(str(error))
                else:
                    st.session_state.weekly_plc_flash = (
                        f"Instructional response saved for {week['label']}."
                    )
                    st.rerun()

            with save_right:
                if snapshot["saved_responses"]:
                    st.caption(
                        "Response saved · "
                        + ", ".join(
                            f"{row['mastery_status']}: {row['response_type']}"
                            for row in snapshot["saved_responses"]
                        )
                    )
                    if st.button(
                        "Create / Open POST CFA",
                        key=f"post_cfa_{week_id}",
                        width="stretch",
                    ):
                        try:
                            administration_id = create_or_get_post_reassessment(
                                current_user=current_user,
                                cycle_id=cycle_id,
                                source_administration_id=int(latest["administration_id"]),
                                administered_on=reassess_date.isoformat(),
                            )
                        except (ValueError, PermissionError) as error:
                            st.error(str(error))
                        else:
                            st.session_state.cfa_cycle_assessment_id = int(
                                latest["cycle_assessment_id"]
                            )
                            st.session_state.cfa_administration_id = administration_id
                            st.switch_page("views/cfa_data_entry.py")
                else:
                    st.caption(
                        "Save the instructional response first. The planned reassessment "
                        "will stay attached to this evidence cycle."
                    )

        st.divider()
        notes_col, settings_col = st.columns([1.7, 1], gap="large")
        with notes_col:
            st.markdown("#### Weekly PLC Notes")
            note_key = f"weekly_note_{week_id}"
            note = st.text_area(
                "Meeting note",
                key=note_key,
                height=100,
                placeholder="What did the team notice, decide, or commit to?",
                label_visibility="collapsed",
            )
            if st.button("Save Note", key=f"save_week_note_{week_id}"):
                try:
                    save_week_note(
                        week_assignment_id=int(week["week_assignment_id"]),
                        user_id=(int(current_user["user_id"]) if current_user else None),
                        note_text=note,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state[note_key] = ""
                    st.session_state.weekly_plc_flash = f"Note saved for {week['label']}."
                    st.rerun()

            for saved_note in list_week_notes(int(week["week_assignment_id"]))[:3]:
                with st.container(border=True):
                    st.caption(f"{saved_note['author_name']} · {saved_note['created_at']}")
                    st.write(saved_note["note_text"])

        with settings_col:
            st.markdown("#### Week Settings")
            st.caption(
                "The workflow no longer uses manual step completion. Progress is visible "
                "from the CFA evidence and saved instructional response."
            )
            if st.button(
                "Clear this week's PLC assignment",
                key=f"clear_week_{week_id}",
                width="stretch",
            ):
                clear_week_assignment(team_id, week_id)
                st.session_state.weekly_plc_flash = f"Cleared {week['label']} assignment."
                st.rerun()
