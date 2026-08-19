"""Guided, role-aware dashboard for the PLC workflow."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.dashboard import get_dashboard_workspace
from repositories.dashboard_team_filter import (
    get_team_filtered_workspace,
    list_dashboard_teams,
)


def percent(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "—"


def _date_label(value: str | None) -> str:
    if not value:
        return "No date"
    return date.fromisoformat(value).strftime("%b %-d")


@st.dialog("Start a PLC Cycle")
def start_cycle_dialog(teams: list[dict]) -> None:
    st.write(
        "Choose the team first. The Cycle Workspace will open to the weekly "
        "planning view, where you can select the learning standard and create "
        "the cycle."
    )
    if not teams:
        st.info("No PLC teams are available in your access scope.")
        return

    labels = {
        (
            f"{team['name']} · {team['grade_level']} {team['subject']} · "
            f"{team['school_name']}"
        ): team
        for team in teams
    }
    selected_label = st.selectbox("PLC team", list(labels), key="new_cycle_team")
    selected = labels[selected_label]
    st.caption(
        "Next: choose the instructional week, priority standard, and cycle name."
    )
    if st.button("Continue to Cycle Workspace", type="primary", width="stretch"):
        st.session_state.plc_calendar_team_id = int(selected["team_id"])
        st.switch_page("views/plc_cycles.py")


current_user = st.session_state.get("current_user")
teams = list_dashboard_teams(current_user)

header_col, action_col = st.columns([4.5, 1.5], vertical_alignment="bottom")
with header_col:
    page_header(
        "PLC Data Assistant",
        "Dashboard",
        "See what needs attention, continue the current PLC cycle, and monitor "
        "learning across the people and teams you support.",
    )
with action_col:
    if st.button("＋ Start New Cycle", type="primary", width="stretch"):
        start_cycle_dialog(teams)

team_by_label = {
    (
        f"{team['name']} · {team['grade_level']} {team['subject']} · "
        f"{team['school_name']}"
    ): team
    for team in teams
}

filter_col, scope_col = st.columns([2.5, 3.5], vertical_alignment="bottom")
with filter_col:
    team_label = st.selectbox(
        "Team view",
        ["All Teams", *team_by_label],
        key="dashboard_team_filter",
    )

if team_label == "All Teams":
    workspace = get_dashboard_workspace(current_user)
else:
    selected_team = team_by_label[team_label]
    workspace = get_team_filtered_workspace(current_user, int(selected_team["team_id"]))

with scope_col:
    st.caption(
        f"Viewing as **{workspace['scope']['role']}** · {workspace['scope']['label']}"
    )

kpis = workspace["kpis"]
role = workspace["scope"]["role"]
cycles = workspace["cycles"]
primary_cycle = cycles[0] if cycles else None
primary_action = workspace["next_actions"][0] if workspace["next_actions"] else None

# The dashboard begins with a decision, not a report.
with st.container(border=True):
    label_col, button_col = st.columns([4.6, 1.4], vertical_alignment="center")
    with label_col:
        st.markdown("<div class='plc-eyebrow'>YOUR NEXT PLC ACTION</div>", unsafe_allow_html=True)
        if primary_cycle:
            if primary_action:
                st.subheader(primary_action["Action"])
                st.write(primary_action["Context"])
            else:
                st.subheader(f"Continue {primary_cycle['cycle_name']}")
                st.write(
                    f"{primary_cycle['plc']} · {primary_cycle['standard']} · "
                    f"Current phase: {primary_cycle['stage']}"
                )
            st.caption(
                f"Cycle ends {_date_label(primary_cycle['end_date'])} · "
                f"Current mastery {percent(primary_cycle['mastery_rate'])}"
            )
        else:
            st.subheader("Start your first PLC cycle")
            st.write(
                "Choose the learning focus, define common evidence, and give the "
                "team one shared place to monitor the instructional response."
            )
    with button_col:
        if primary_cycle:
            if st.button("Continue Cycle", type="primary", width="stretch"):
                st.session_state.plc_calendar_team_id = int(primary_cycle["team_id"])
                st.switch_page("views/plc_cycles.py")
        elif st.button("Start a PLC Cycle", type="primary", width="stretch"):
            start_cycle_dialog(teams)

metric_columns = st.columns(4)
metric_columns[0].metric("Active cycles", kpis["active_cycles"])
metric_columns[1].metric("Students with evidence", kpis["students_assessed"])
metric_columns[2].metric("Current mastery", percent(kpis["mastery_rate"]))
metric_columns[3].metric("Need intensive support", kpis["intensive_results"])

alert_col, task_col = st.columns([1, 1], gap="large")
with alert_col:
    st.subheader("Needs attention")
    alerts = workspace["alerts"]
    if not alerts:
        st.success("No early-warning triggers in this team scope.", icon=":material/check_circle:")
    for index, alert in enumerate(alerts):
        message = f"**{alert['Alert']}**  \n{alert['Action']}"
        if alert["Priority"] == "High":
            st.error(message, icon=":material/warning:")
        else:
            st.warning(message, icon=":material/schedule:")

with task_col:
    st.subheader("Upcoming team tasks")
    open_commitments = [
        item for item in workspace["commitments"] if item["status"] == "Open"
    ]
    if not open_commitments:
        st.info("No open commitments in this team scope.", icon=":material/task_alt:")
    else:
        for item in open_commitments[:4]:
            with st.container(border=True):
                st.markdown(f"**{item['name']}**")
                st.caption(
                    f"{item['owner']} · due {_date_label(item['due_date'])} · "
                    f"{item['standard']}"
                )

if team_label == "All Teams" and role == "Coach":
    st.subheader("Teachers I support")
    teacher_frame = pd.DataFrame(
        [
            {
                "Teacher": item["display_name"],
                "PLC teams": item["plc_teams"],
                "Open commitments": item["open_commitments"],
                "Overdue": item["overdue_commitments"],
            }
            for item in workspace["teacher_summaries"]
        ]
    )
    if teacher_frame.empty:
        st.caption("No assigned teachers are available yet.")
    else:
        st.dataframe(teacher_frame, hide_index=True, width="stretch")

if team_label == "All Teams" and role in {
    "District Administrator",
    "School Administrator",
    "Principal",
}:
    st.subheader("Schools and teams I support")
    school_frame = pd.DataFrame(
        [
            {
                "School": item["school"],
                "PLC teams": item["plc_teams"],
                "Active cycles": item["active_cycles"],
                "Students with evidence": item["assessed"],
                "Mastery": percent(item["mastery_rate"]),
                "Intensive": item["intensive"],
            }
            for item in workspace["school_summaries"]
        ]
    )
    if school_frame.empty:
        st.caption("No school-level evidence is available yet.")
    else:
        st.dataframe(school_frame, hide_index=True, width="stretch")

st.subheader("Team history")
completed_cycles = workspace.get("completed_cycles", [])
if not completed_cycles:
    st.caption("Completed PLC cycles will appear here with their final evidence.")
else:
    history_frame = pd.DataFrame(
        [
            {
                "PLC team": cycle["plc"],
                "Cycle": cycle["cycle_name"],
                "Standard": cycle["standard"],
                "Students assessed": cycle["students_assessed"],
                "Final mastery": percent(cycle["mastery_rate"]),
                "Completed": cycle["end_date"],
            }
            for cycle in completed_cycles
        ]
    )
    st.dataframe(history_frame, hide_index=True, width="stretch")