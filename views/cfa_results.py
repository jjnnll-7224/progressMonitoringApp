"""CFA results workspace: turn submitted scores into PLC-ready evidence."""

from __future__ import annotations

import pandas as pd
import streamlit as st
import plotly.express as px

from components.styles import page_header
from repositories.assessments import get_assessments
from repositories.cfa_results import get_administration_results, get_administrations
from services.scoring import percentage_point_growth


def percent(value: float | None) -> str:
    """Show missing data as an em dash, never as a misleading zero."""
    return f"{value:.1f}%" if value is not None else "—"


def results_table(results: list[dict]) -> pd.DataFrame:
    """Convert internal result records into the readable student table."""
    return pd.DataFrame([
        {
            "Student": row["student_name"],
            "Student Number": row["student_number"],
            "Score": percent(row["percent"]),
            "Mastery Status": row["status"],
        }
        for row in results
    ])


page_header(
    "Assessment report",
    "CFA Results",
    "Review mastery, identify skill gaps, and move directly into PLC decisions.",
)

# Start with a submitted administration. Drafts are deliberately excluded so
# PLC teams do not make instructional decisions from incomplete data.
assessments = get_assessments()
assessment_by_label = {
    f"{item['name']} · {item['standards'] or 'No standards'}": item
    for item in assessments
}
if not assessment_by_label:
    st.info("Create an assessment and submit CFA scores before viewing results.")
    st.stop()

selected_assessment = st.selectbox("Assessment", list(assessment_by_label))
assessment = assessment_by_label[selected_assessment]
submitted = [item for item in get_administrations(assessment["assessment_id"])
             if item["status"] == "Submitted"]
if not submitted:
    st.warning("This assessment has no submitted administrations yet.")
    st.stop()

administration_by_label = {
    f"{item['administration_type']} · {item['administered_on']}": item
    for item in submitted
}
selected_administration = st.selectbox("Results administration", list(administration_by_label))
current = get_administration_results(
    administration_by_label[selected_administration]["administration_id"]
)
if current is None:
    st.error("The selected administration could not be found.")
    st.stop()

# The next most-recent submitted administration provides a simple pre/post
# comparison without guessing which administration a teacher intends to use.
other_administrations = [item for item in submitted
                         if item["administration_id"] != current["administration_id"]]
comparison = get_administration_results(other_administrations[0]["administration_id"]) if other_administrations else None
growth = percentage_point_growth(comparison["average"], current["average"]) if comparison else None
mastery_rate = current["counts"]["Mastered"] / current["completed"] * 100 if current["completed"] else None
most_missed = min(current["question_performance"], key=lambda row: row["percent"] if row["percent"] is not None else 101, default=None)

metric_columns = st.columns(5)
metric_columns[0].metric("Average Score", percent(current["average"]))
metric_columns[1].metric("Mastery Rate", percent(mastery_rate))
metric_columns[2].metric("Students Assessed", current["completed"])
metric_columns[3].metric("Most Missed", most_missed["question"] if most_missed else "—")
metric_columns[4].metric("Change vs. Previous", f"{growth:+.1f} pts" if growth is not None else "—")

colors = {
    "Mastered": "#1f77b4",
    "Developing": "#ff7f0e",
    "Approaching": "#eadc19",
    "Intensive": "#d62728",
}

left, right = st.columns([1.1, 1], gap="large")

with left:
    st.subheader("Mastery distribution")

    mastery_rows = pd.DataFrame(
        [
            {
                "Status": status,
                "Students": count,
            }
            for status, count in current["counts"].items()
        ]
    )

    total_students = mastery_rows["Students"].sum()

    mastery_rows["Percent"] = (
        mastery_rows["Students"] / total_students * 100
        if total_students > 0
        else 0
    )

    fig = px.bar(
        mastery_rows,
        x="Status",
        y="Percent",
        color="Status",
        color_discrete_map=colors,
        category_orders={
            "Status": [
                "Mastered",
                "Developing",
                "Approaching",
                "Intensive",
            ]
        },
        text="Percent",
        custom_data=["Students"],
    )

    fig.update_traces(
        texttemplate="%{text:.0f}%",
        textposition="outside",
        cliponaxis=False,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "%{y:.1f}%<br>"
            "%{customdata[0]} students"
            "<extra></extra>"
        ),
    )

    fig.update_layout(
        height=300,
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis_title=None,
        yaxis_title=None,
        yaxis=dict(
            range=[0, 100],
            showgrid=False,
            showticklabels=False,
            zeroline=False,
            fixedrange=True,
        ),
        xaxis=dict(
            fixedrange=True,
        ),
        margin=dict(l=10, r=10, t=25, b=10),
        bargap=0.25,
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "displayModeBar": False,
            "scrollZoom": False,
            "doubleClick": False,
        },
    )

    st.subheader("Question analysis")

    question_rows = pd.DataFrame(
        [
            {
                "Question": row["question"],
                "Correct (%)": row["percent"],
                "Subskill": row["subskill"],
            }
            for row in current["question_performance"]
        ]
    )

    st.dataframe(
        question_rows,
        hide_index=True,
        width="stretch",
    )


with right:
    st.subheader("Subskill gaps")

    subskill_rows = pd.DataFrame(
        [
            {
                "Subskill": row["subskill"],
                "Correct (%)": row["percent"],
            }
            for row in current["subskill_performance"]
        ]
    )

    if subskill_rows.empty:
        st.caption(
            "Add subskills to assessment questions to see this analysis."
        )

    else:
        fig = px.bar(
            subskill_rows,
            x="Subskill",
            y="Correct (%)",
            text="Correct (%)",
        )

        fig.update_traces(
            texttemplate="%{text:.0f}%",
            textposition="outside",
            cliponaxis=False,
            hovertemplate=(
                "<b>%{x}</b><br>"
                "%{y:.1f}% correct"
                "<extra></extra>"
            ),
        )

        fig.update_layout(
            height=300,
            showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis_title=None,
            yaxis_title=None,
            yaxis=dict(
                range=[0, 100],
                showgrid=False,
                showticklabels=False,
                zeroline=False,
                fixedrange=True,
            ),
            xaxis=dict(
                fixedrange=True,
            ),
            margin=dict(l=10, r=10, t=25, b=10),
            bargap=0.3,
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
            config={
                "displayModeBar": False,
                "scrollZoom": False,
                "doubleClick": False,
            },
        )

    st.subheader("PLC discussion prompts")
    if most_missed and most_missed["percent"] is not None:
        st.info(f"Why did students struggle with {most_missed['question']} ({most_missed['percent']:.1f}% correct)?")
    st.write("Which students need prerequisite support, and which are ready for enrichment?")
    st.write("What evidence will show that the reteach worked before reassessment?")

st.subheader("Student mastery review")
student_frame = results_table(current["student_results"])
status_filter = st.multiselect(
    "Filter by mastery status",
    ["Mastered", "Approaching", "Developing", "Intensive"],
    default=["Mastered", "Approaching", "Developing", "Intensive"],
)
st.dataframe(
    student_frame[student_frame["Mastery Status"].isin(status_filter)],
    hide_index=True,
    width="stretch",
)

# This creates a clear handoff from evidence to the instructional grouping work which now resides in the PLC Cycles
if st.button("Back to PLC Cycles", type="primary"):
    st.switch_page("views/plc_cycles.py")
