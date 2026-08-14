"""Role-aware Dashboard with a top-right PLC team filter."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.dashboard import get_dashboard_workspace
from repositories.dashboard_team_filter import (
    get_team_filtered_workspace,
    list_dashboard_teams,
)
from repositories.users import (
    ROLES,
    get_coach_assignments,
    list_schools,
    list_users,
    save_coach_assignments,
    save_user,
)
from services.access_control import display_role


def percent(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "—"


current_user = st.session_state.get("current_user")

header_col, filter_col = st.columns([4.3, 1.7], vertical_alignment="bottom")

with header_col:
    page_header(
        "PLC Data Assistant",
        "Dashboard",
        "Your role-specific view of student evidence, PLC follow-through, "
        "and the next work that matters.",
    )

teams = list_dashboard_teams(current_user)

team_by_label = {
    (
        f"{team['name']} · {team['grade_level']} {team['subject']} · "
        f"{team['school_name']}"
    ): team
    for team in teams
}

with filter_col:
    team_label = st.selectbox(
        "Team",
        ["All Teams", *team_by_label],
        key="dashboard_team_filter",
        help="Filter the evidence, PLC cycles, commitments, and action queue to one team.",
    )

if not current_user:
    st.warning("Sign in to load a role-specific dashboard.")

if team_label == "All Teams":
    workspace = get_dashboard_workspace(current_user)
else:
    selected_team = team_by_label[team_label]
    workspace = get_team_filtered_workspace(
        current_user,
        int(selected_team["team_id"]),
    )

kpis = workspace["kpis"]
role = workspace["scope"]["role"]

st.info(
    f"**Viewing as:** {workspace['scope']['role']} — "
    f"{workspace['scope']['label']}"
)

if role == "Teacher":
    metric_definitions = [
        ("Students with CFA evidence", kpis["students_assessed"], "Current team filter"),
        ("Mastery rate", percent(kpis["mastery_rate"]), "Latest submitted CFA by standard"),
        ("Need intensive support", kpis["intensive_results"], "Student-standard results"),
        ("My open commitments", kpis["my_open_commitments"], "Assigned to me"),
        ("Active interventions", kpis["active_interventions"], "Current team filter"),
    ]
elif role == "Coach":
    metric_definitions = [
        ("Assigned teachers", len(workspace["teacher_summaries"]), "Teachers I support"),
        ("Active PLC cycles", kpis["active_cycles"], "Current team filter"),
        ("Mastery rate", percent(kpis["mastery_rate"]), "Latest submitted CFA by standard"),
        ("Need intensive support", kpis["intensive_results"], "Student-standard results"),
        ("Past-end interventions", kpis["overdue_interventions"], "Needs follow-up"),
    ]
elif role in {"School Administrator", "Principal"}:
    metric_definitions = [
        ("Active PLC cycles", kpis["active_cycles"], "Current team filter"),
        ("Students with evidence", kpis["students_assessed"], "Current team filter"),
        ("Mastery rate", percent(kpis["mastery_rate"]), "Latest submitted CFA by standard"),
        ("Need intensive support", kpis["intensive_results"], "Student-standard results"),
        ("Past-end interventions", kpis["overdue_interventions"], "Needs follow-up"),
    ]
else:
    metric_definitions = [
        ("Active PLC cycles", kpis["active_cycles"], "Current team filter"),
        ("Students with evidence", kpis["students_assessed"], "Current team filter"),
        ("Mastery rate", percent(kpis["mastery_rate"]), "Latest submitted CFA by standard"),
        ("Need intensive support", kpis["intensive_results"], "Student-standard results"),
        ("Past-end interventions", kpis["overdue_interventions"], "Needs follow-up"),
    ]

metric_columns = st.columns(len(metric_definitions))
for column, (label, value, help_text) in zip(metric_columns, metric_definitions):
    column.metric(label, value, help=help_text)

left, right = st.columns([1.05, 1], gap="large")

with left:
    st.subheader("Mastery overview")
    mastery_frame = pd.DataFrame(
        [
            {
                "Status": status,
                "Student-standard results": count,
            }
            for status, count in workspace["mastery_counts"].items()
        ]
    )
    st.bar_chart(
        mastery_frame,
        x="Status",
        y="Student-standard results",
        color="Status",
    )
    st.caption(
        "Each student contributes their most recent submitted CFA result for each standard."
    )

with right:
    st.subheader("My next actions")
    if not workspace["next_actions"]:
        st.success("No action is currently due in this workspace.")
    else:
        st.dataframe(
            pd.DataFrame(workspace["next_actions"]),
            hide_index=True,
            width="stretch",
        )
    st.caption(
        "The Team filter narrows evidence and PLC follow-through without changing the user's access scope."
    )

st.subheader("Active PLC cycles")

cycle_frame = pd.DataFrame(
    [
        {
            "PLC": cycle["plc"],
            "Cycle": cycle["cycle_name"],
            "Standard": cycle["standard"],
            "Stage": cycle["stage"],
            "CFA Average": percent(cycle["average"]),
            "Mastery Rate": percent(cycle["mastery_rate"]),
            "Assessed": cycle["students_assessed"],
            "Ends": cycle["end_date"],
        }
        for cycle in workspace["cycles"]
    ]
)

if cycle_frame.empty:
    st.info("No active PLC cycles are available in the selected team scope.")
else:
    st.dataframe(
        cycle_frame,
        hide_index=True,
        width="stretch",
    )

# Organization/people summaries stay access-scope-wide. The team selector is
# about instructional evidence, not changing security or user administration.
if team_label == "All Teams" and role in {
    "District Administrator",
    "School Administrator",
    "Principal",
}:
    st.subheader("School outcomes and PLC implementation")
    school_frame = pd.DataFrame(
        [
            {
                "School": item["school"],
                "PLC teams": item["plc_teams"],
                "Active cycles": item["active_cycles"],
                "Students with evidence": item["assessed"],
                "CFA average": percent(item["average"]),
                "Mastery rate": percent(item["mastery_rate"]),
                "Intensive": item["intensive"],
            }
            for item in workspace["school_summaries"]
        ]
    )
    if school_frame.empty:
        st.caption("No submitted CFA evidence is available for this access scope yet.")
    else:
        st.dataframe(
            school_frame,
            hide_index=True,
            width="stretch",
        )

if team_label == "All Teams" and role == "Coach":
    st.subheader("Assigned teacher follow-through")
    teacher_frame = pd.DataFrame(
        [
            {
                "Teacher": item["display_name"],
                "PLC teams": item["plc_teams"],
                "Open commitments": item["open_commitments"],
                "Overdue commitments": item["overdue_commitments"],
                "Email": item["email"],
            }
            for item in workspace["teacher_summaries"]
        ]
    )
    if teacher_frame.empty:
        st.caption("No assigned teacher follow-through is available yet.")
    else:
        st.dataframe(
            teacher_frame,
            hide_index=True,
            width="stretch",
        )

st.subheader("Teacher commitments")
commitment_frame = pd.DataFrame(
    [
        {
            "Commitment": item["name"],
            "Standard": item["standard"],
            "Owner": item["owner"],
            "Due": item["due_date"],
            "Status": item["status"],
        }
        for item in workspace["commitments"]
    ]
)

if commitment_frame.empty:
    st.caption("No teacher commitments are recorded in this team scope.")
else:
    st.dataframe(
        commitment_frame,
        hide_index=True,
        width="stretch",
    )

if team_label == "All Teams" and role != "Teacher":
    st.subheader("People in this access scope")
    people_frame = pd.DataFrame(
        [
            {
                "Name": person["display_name"],
                "Role": display_role(person["role"]),
                "School assignment": person["schools"],
                "Email": person["email"],
            }
            for person in workspace["people"]
        ]
    )
    if people_frame.empty:
        st.caption("No people are assigned to this workspace yet.")
    else:
        st.dataframe(
            people_frame,
            hide_index=True,
            width="stretch",
        )

if (
    team_label == "All Teams"
    and workspace["scope"]["role"] == "District Administrator"
):
    with st.expander("Manage users and coach assignments"):
        schools = list_schools()
        school_labels = {
            school["school_name"]: school["school_id"]
            for school in schools
        }

        with st.form("save_user_form", clear_on_submit=True):
            st.markdown("##### Add or update a user")
            user_email = st.text_input("Email")
            user_name = st.text_input("Display name")
            user_role = st.selectbox("Role", ROLES)
            assigned_school_names = st.multiselect(
                "School assignment(s)",
                list(school_labels),
            )

            if st.form_submit_button("Save user", type="primary"):
                try:
                    saved_user = save_user(
                        user_email,
                        user_name,
                        user_role,
                        [
                            school_labels[name]
                            for name in assigned_school_names
                        ],
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success(
                        f"Saved {saved_user['display_name']} as "
                        f"{saved_user['role']}."
                    )
                    st.rerun()

        users = list_users()
        coaches = [
            user for user in users
            if user["role"] == "Coach"
        ]
        teachers = [
            user for user in users
            if user["role"] == "Teacher"
        ]

        if coaches:
            coach_lookup = {
                f"{user['display_name']} ({user['email']})": user
                for user in coaches
            }
            selected_coach_label = st.selectbox(
                "Coach",
                list(coach_lookup),
                key="assignment_coach",
            )
            selected_coach = coach_lookup[selected_coach_label]

            teacher_labels = {
                f"{user['display_name']} ({user['email']})": user["user_id"]
                for user in teachers
            }

            current_ids = set(
                get_coach_assignments(selected_coach["user_id"])
            )

            selected_teacher_labels = st.multiselect(
                "Teachers assigned to this coach",
                list(teacher_labels),
                default=[
                    label
                    for label, user_id in teacher_labels.items()
                    if user_id in current_ids
                ],
                key=f"coach_teachers_{selected_coach['user_id']}",
            )

            if st.button("Save coach assignments"):
                save_coach_assignments(
                    selected_coach["user_id"],
                    [
                        teacher_labels[label]
                        for label in selected_teacher_labels
                    ],
                )
                st.success("Coach assignments saved.")
                st.rerun()

st.subheader("Continue the workflow")
action_columns = st.columns(4)

if action_columns[0].button(
    "Open Weekly PLCs",
    type="primary",
    width="stretch",
):
    if team_label != "All Teams":
        st.session_state.plc_calendar_team_id = int(
            team_by_label[team_label]["team_id"]
        )
    st.switch_page("views/PLC_Cycles.py")

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
    "Standards Map",
    width="stretch",
):
    st.switch_page("views/standards.py")
