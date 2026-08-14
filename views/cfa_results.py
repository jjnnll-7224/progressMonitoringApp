"""PRE → POST CFA reporting for one PLC-cycle assessment assignment."""

from __future__ import annotations

from datetime import date
from html import escape

import pandas as pd
import plotly.express as px
import streamlit as st

from components.styles import page_header
from repositories.assessments import get_cycle_assessment_assignments
from repositories.cfa_results import (
    get_administration_results,
    get_administrations,
    get_result_sections,
)


MASTERY_STATUSES = (
    "Mastered",
    "Approaching",
    "Developing",
    "Intensive",
)

COLORS = {
    "Mastered": "#1f77b4",
    "Approaching": "#eadc19",
    "Developing": "#ff7f0e",
    "Intensive": "#d62728",
}


def pct(value: float | None, digits: int = 1) -> str:
    return f"{value:.{digits}f}%" if value is not None else "—"


def display_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return date.fromisoformat(value).strftime("%m-%d-%Y")
    except ValueError:
        return str(value)


def mastery_rate(result: dict | None) -> float | None:
    if not result or not result["completed"]:
        return None
    return result["counts"]["Mastered"] / result["completed"] * 100


def most_missed(result: dict | None) -> str:
    if not result:
        return "—"
    row = min(
        result["question_performance"],
        key=lambda item: item["percent"] if item["percent"] is not None else 101,
        default=None,
    )
    return row["question"] if row else "—"


def ban(
    label: str,
    current_value: str,
    *,
    pre_value: str | None = None,
    current_period: str = "PRE",
) -> None:
    pre_html = ""
    if current_period == "POST" and pre_value is not None:
        pre_html = (
            "<div style='text-align:right;color:#9CA3AF;font-size:.76rem;"
            "margin-top:7px;'>"
            f"PRE&nbsp;&nbsp;<span style='font-size:.95rem;font-weight:650;'>"
            f"{escape(pre_value)}</span></div>"
        )

    st.markdown(
        f"""
        <div style="border:1px solid #E5E7EB;border-radius:12px;padding:13px 15px;
                    min-height:114px;background:white;">
            <div style="font-size:.78rem;color:#6B7280;font-weight:650;">
                {escape(label)}
            </div>
            <div style="font-size:1.85rem;line-height:1.15;font-weight:760;
                        color:#111827;margin-top:7px;">
                {escape(current_value)}
            </div>
            {pre_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _latest_by_type(
    administrations: list[dict],
    administration_type: str,
) -> dict | None:
    matching = [
        row
        for row in administrations
        if row["status"] == "Submitted"
        and row["administration_type"] == administration_type
    ]
    if not matching:
        return None
    return max(
        matching,
        key=lambda row: (
            row["administered_on"],
            int(row["administration_id"]),
        ),
    )


def _mastery_frame(
    result: dict | None,
    label: str,
) -> list[dict]:
    if not result:
        return []
    total = result["completed"]
    return [
        {
            "Administration": label,
            "Status": status,
            "Students": result["counts"][status],
            "Percent": (
                result["counts"][status] / total * 100
                if total
                else 0
            ),
        }
        for status in MASTERY_STATUSES
    ]


def _performance_frame(
    pre: dict | None,
    post: dict | None,
    key: str,
    label_key: str,
) -> pd.DataFrame:
    rows = []
    for label, result in (("PRE", pre), ("POST", post)):
        if not result:
            continue
        for row in result[key]:
            rows.append(
                {
                    "Administration": label,
                    "Label": row[label_key],
                    "Correct (%)": row["percent"],
                }
            )
    return pd.DataFrame(rows)


def _question_comparison(
    pre: dict | None,
    post: dict | None,
) -> pd.DataFrame:
    by_question: dict[str, dict] = {}

    for label, result in (("PRE", pre), ("POST", post)):
        if not result:
            continue
        for row in result["question_performance"]:
            item = by_question.setdefault(
                row["question"],
                {
                    "Question": row["question"],
                    "Subskill": row["core_idea"] or "Not specified",
                    "Pre Correct %": None,
                    "Post Correct %": None,
                },
            )
            item[
                "Pre Correct %" if label == "PRE" else "Post Correct %"
            ] = row["percent"]

    def q_number(value: str) -> int:
        try:
            return int(value.lstrip("Q"))
        except ValueError:
            return 9999

    return pd.DataFrame(
        sorted(
            by_question.values(),
            key=lambda row: q_number(row["Question"]),
        )
    )


def _student_comparison(
    pre: dict | None,
    post: dict | None,
) -> pd.DataFrame:
    rows: dict[int, dict] = {}

    for label, result in (("PRE", pre), ("POST", post)):
        if not result:
            continue
        for student in result["student_results"]:
            student_id = int(student["student_id"])
            item = rows.setdefault(
                student_id,
                {
                    "Student": student["student_name"],
                    "Student Number": student["student_number"],
                    "Pre Score": None,
                    "Pre Status": None,
                    "Post Score": None,
                    "Post Status": None,
                },
            )
            if label == "PRE":
                item["Pre Score"] = student["percent"]
                item["Pre Status"] = student["status"]
            else:
                item["Post Score"] = student["percent"]
                item["Post Status"] = student["status"]

    return pd.DataFrame(
        sorted(rows.values(), key=lambda row: row["Student"])
    )


page_header(
    "Assessment report",
    "CFA Results",
    "Compare PRE and POST evidence without switching between administration screens.",
)

current_user = st.session_state.get("current_user")
assignments = get_cycle_assessment_assignments(current_user)

if not assignments:
    st.info(
        "Assign a CFA to a PLC cycle and submit scores before viewing results."
    )
    st.stop()

assignment_by_label = {
    (
        f"{item['team_name']} · {item['cycle_name']} · "
        f"{item['assessment_name']} · {item['standards'] or 'No standards'}"
    ): item
    for item in assignments
}

labels = list(assignment_by_label)
selected_cycle_id = st.session_state.get("selected_cycle_id")
default_index = next(
    (
        index
        for index, item in enumerate(assignments)
        if selected_cycle_id is not None
        and int(item["cycle_id"]) == int(selected_cycle_id)
    ),
    0,
)

top_left, pre_date_col, post_date_col = st.columns(
    [4.1, 1, 1],
    vertical_alignment="bottom",
)

with top_left:
    selected_label = st.selectbox(
        "PLC cycle / CFA",
        labels,
        index=default_index,
    )
assignment = assignment_by_label[selected_label]

administrations = get_administrations(
    int(assignment["cycle_assessment_id"])
)
pre_admin = _latest_by_type(administrations, "PRE")
post_admin = _latest_by_type(administrations, "POST")

with pre_date_col:
    st.caption("PRE")
    st.markdown(
        f"**{display_date(pre_admin['administered_on']) if pre_admin else '—'}**"
    )

with post_date_col:
    st.caption("POST")
    st.markdown(
        f"**{display_date(post_admin['administered_on']) if post_admin else '—'}**"
    )

if not pre_admin and not post_admin:
    st.warning(
        "This PLC-cycle CFA assignment does not have submitted PRE or POST evidence yet."
    )
    st.stop()

sections = get_result_sections(
    int(assignment["cycle_assessment_id"])
)
section_by_label = {
    (
        f"{item['teacher_name']} · {item['section_name']} · "
        f"{item['student_count']} students"
    ): int(item["section_id"])
    for item in sections
}

section_label = st.selectbox(
    "Class / period",
    ["All assigned sections", *section_by_label],
)
section_id = (
    None
    if section_label == "All assigned sections"
    else section_by_label[section_label]
)

pre = (
    get_administration_results(
        int(pre_admin["administration_id"]),
        section_id=section_id,
    )
    if pre_admin
    else None
)
post = (
    get_administration_results(
        int(post_admin["administration_id"]),
        section_id=section_id,
    )
    if post_admin
    else None
)

current = post or pre
current_period = "POST" if post else "PRE"

if current is None:
    st.error("The selected evidence could not be loaded.")
    st.stop()

st.caption(
    f"{assignment['team_name']} · {assignment['cycle_name']} · "
    f"{assignment['standards'] or 'No standards'} · {section_label}"
)

pre_mastery = mastery_rate(pre)
post_mastery = mastery_rate(post)

ban_cols = st.columns(4)
with ban_cols[0]:
    ban(
        "Average Score",
        pct(current["average"]),
        pre_value=pct(pre["average"]) if pre else None,
        current_period=current_period,
    )
with ban_cols[1]:
    ban(
        "Mastery Rate",
        pct(mastery_rate(current)),
        pre_value=pct(pre_mastery) if pre else None,
        current_period=current_period,
    )
with ban_cols[2]:
    ban(
        "Students Assessed",
        str(current["completed"]),
        pre_value=str(pre["completed"]) if pre else None,
        current_period=current_period,
    )
with ban_cols[3]:
    ban(
        "Most Missed",
        most_missed(current),
        pre_value=most_missed(pre) if pre else None,
        current_period=current_period,
    )

if pre and post:
    newly_mastered = 0
    pre_by_student = {
        int(row["student_id"]): row["status"]
        for row in pre["student_results"]
    }
    for row in post["student_results"]:
        if (
            row["status"] == "Mastered"
            and pre_by_student.get(int(row["student_id"])) not in (None, "Mastered")
        ):
            newly_mastered += 1

    mastery_change = (
        (post_mastery - pre_mastery)
        if post_mastery is not None and pre_mastery is not None
        else None
    )
    st.caption(
        "POST growth: "
        + (
            f"{mastery_change:+.1f} percentage points in mastery · "
            if mastery_change is not None
            else ""
        )
        + f"{newly_mastered} student"
        + ("s" if newly_mastered != 1 else "")
        + " moved into Mastered."
    )

st.markdown("### Mastery distribution")
mastery_df = pd.DataFrame(
    _mastery_frame(pre, "PRE")
    + _mastery_frame(post, "POST")
)

if not mastery_df.empty:
    fig = px.bar(
        mastery_df,
        y="Status",
        x="Percent",
        color="Status",
        facet_col="Administration",
        facet_col_wrap=2,
        orientation="h",
        text="Percent",
        color_discrete_map=COLORS,
        category_orders={
            "Status": list(MASTERY_STATUSES),
            "Administration": ["PRE", "POST"],
        },
    )
    fig.update_traces(
        texttemplate="%{text:.0f}%",
        textposition="outside",
        cliponaxis=False,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "%{x:.1f}%<br>"
            "<extra></extra>"
        ),
    )
    fig.update_layout(
        height=310,
        showlegend=False,
        xaxis_title=None,
        yaxis_title=None,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    fig.for_each_annotation(
        lambda annotation: annotation.update(
            text=annotation.text.replace("Administration=", "")
        )
    )
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
    )

core_df = _performance_frame(
    pre,
    post,
    "core_idea_performance",
    "core_idea",
)

st.markdown("### Core Idea performance")
if core_df.empty:
    st.caption("No Core Idea evidence is available for this CFA.")
else:
    fig = px.bar(
        core_df,
        y="Label",
        x="Correct (%)",
        facet_col="Administration",
        facet_col_wrap=2,
        orientation="h",
        text="Correct (%)",
        category_orders={"Administration": ["PRE", "POST"]},
    )
    fig.update_traces(
        texttemplate="%{text:.0f}%",
        textposition="outside",
        cliponaxis=False,
    )
    fig.update_layout(
        height=max(310, 62 * core_df["Label"].nunique()),
        showlegend=False,
        xaxis_title=None,
        yaxis_title=None,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    fig.for_each_annotation(
        lambda annotation: annotation.update(
            text=annotation.text.replace("Administration=", "")
        )
    )
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
    )

standard_df = _performance_frame(
    pre,
    post,
    "standard_performance",
    "standard",
)

if not standard_df.empty and standard_df["Label"].nunique() > 1:
    st.markdown("### Performance by standard")
    fig = px.bar(
        standard_df,
        y="Label",
        x="Correct (%)",
        facet_col="Administration",
        facet_col_wrap=2,
        orientation="h",
        text="Correct (%)",
        category_orders={"Administration": ["PRE", "POST"]},
    )
    fig.update_traces(
        texttemplate="%{text:.0f}%",
        textposition="outside",
        cliponaxis=False,
    )
    fig.update_layout(
        height=max(260, 60 * standard_df["Label"].nunique()),
        showlegend=False,
        xaxis_title=None,
        yaxis_title=None,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    fig.for_each_annotation(
        lambda annotation: annotation.update(
            text=annotation.text.replace("Administration=", "")
        )
    )
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
    )

st.markdown("### Question / subskill comparison")
question_df = _question_comparison(pre, post)
if question_df.empty:
    st.caption("No question-level evidence is available.")
else:
    st.dataframe(
        question_df,
        hide_index=True,
        width="stretch",
        column_config={
            "Pre Correct %": st.column_config.NumberColumn(
                format="%.1f%%"
            ),
            "Post Correct %": st.column_config.NumberColumn(
                format="%.1f%%"
            ),
        },
    )

st.markdown("### Student results")
student_df = _student_comparison(pre, post)

if student_df.empty:
    st.caption("No student-level evidence is available.")
else:
    st.dataframe(
        student_df,
        hide_index=True,
        width="stretch",
        column_config={
            "Pre Score": st.column_config.NumberColumn(
                format="%.1f%%"
            ),
            "Post Score": st.column_config.NumberColumn(
                format="%.1f%%"
            ),
        },
    )

if st.button("Back to PLC Cycles", type="primary"):
    st.session_state.selected_cycle_id = int(assignment["cycle_id"])
    st.switch_page("views/plc_cycles.py")
