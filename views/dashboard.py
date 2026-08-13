"""Landing page for the PLC Intelligence workflow."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import plotly.express as px

from components.styles import page_header
from repositories.dashboard import get_dashboard_workspace


def percent(value: float | None) -> str:
    """Keep missing CFA evidence visibly distinct from a zero score."""
    return f"{value:.1f}%" if value is not None else "—"


page_header(
    "PLC Data Assistant",
    "Dashboard",
    "A live view of mastery, active PLC work, and the next actions that need attention.",
)

# This is calculated from submitted CFA item scores, cycles, interventions,
# and commitments each time the page loads—there are no hard-coded metrics.
current_user = st.session_state.get("current_user")

workspace = get_dashboard_workspace()
kpis = workspace["kpis"]

metric_columns = st.columns(5)
metric_columns[0].metric("Students Assessed", kpis["students_assessed"])
metric_columns[1].metric("Standards Mastered", kpis["standards_mastered"])
metric_columns[2].metric("Current PLC Cycles", kpis["active_cycles"])
metric_columns[3].metric("Interventions Active", kpis["active_interventions"])
metric_columns[4].metric("Past End Date", kpis["overdue_interventions"])

colors = {
    "Mastered": "#1fb42b",
    "Developing": "#ff7f0e",
    "Approaching": "#eadc19",
    "Intensive": "#d62728",
}

left, right = st.columns([1.05, 1], gap="large")

with left:
    st.subheader("Mastery overview")

    mastery_frame = pd.DataFrame(
        [
            {
                "Status": status,
                "Count": count,
            }
            for status, count in workspace["mastery_counts"].items()
        ]
    )

    # Calculate percent of total
    total = mastery_frame["Count"].sum()

    mastery_frame["Percent"] = (
        mastery_frame["Count"] / total * 100
        if total > 0
        else 0
    )

    fig = px.bar(
        mastery_frame,
        x="Status",
        y="Percent",
        color="Status",
        color_discrete_map=colors,
        category_orders={
            "Status": [
                "Mastered",
                "Approaching",
                "Developing",
                "Intensive",
            ]
        },
        text="Percent",
        custom_data=["Count"],
    )

    # Format percentage labels
    fig.update_traces(
        texttemplate="%{text:.0f}%",
        textposition="outside",
        cliponaxis=False,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "%{y:.1f}%<br>"
            "%{customdata[0]} student-standard results"
            "<extra></extra>"
        ),
    )

    fig.update_layout(
        height=300,
        showlegend=False,

        # Dashboard-style appearance
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",

        xaxis_title=None,
        yaxis_title=None,

        yaxis=dict(
            range=[0, 100],
            ticksuffix="%",
            showgrid=True,
            fixedrange=True,
        ),

        xaxis=dict(
            fixedrange=True,
        ),

        margin=dict(
            l=10,
            r=10,
            t=25,
            b=10,
        ),

        bargap=0.25,
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "displayModeBar": False,
            "scrollZoom": False,
            "doubleClick": False,
            "showTips": False,
        },
    )
    st.caption("Each student contributes their most recent submitted CFA result for each standard.")

with right:
    st.subheader("Recent alerts")
    if not workspace["alerts"]:
        st.success("No current alerts from the data connected to this prototype.")
    else:
        for alert in workspace["alerts"]:
            if alert["Priority"] == "High":
                st.error(f"**{alert['Alert']}** — {alert['Action']}")
            else:
                st.warning(f"**{alert['Alert']}** — {alert['Action']}")
    st.caption("Attendance and behavior alerts will appear here after those SIS data sources are connected.")

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
    st.info("No active PLC cycles have been created yet.")
else:
    st.dataframe(cycle_frame, hide_index=True, width="stretch")

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
    st.caption("No teacher commitments have been recorded yet.")
else:
    st.dataframe(commitment_frame, hide_index=True, width="stretch")

# These are shortcuts only; their destination pages remain the source of all
# creation and editing, so the Dashboard stays safe and read-only.
st.subheader("Continue the workflow")
action_columns = st.columns(3)
if action_columns[0].button("Review CFA Results", type="primary", width="stretch"):
    st.switch_page("views/cfa_results.py")
if action_columns[1].button("Open Student Groups", width="stretch"):
    st.switch_page("views/student_groups.py")
if action_columns[2].button("Manage Interventions", width="stretch"):
    st.switch_page("views/interventions.py")