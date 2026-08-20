"""Student × standard mastery heatmap with actionable Study Hall priorities."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.standards import (
    get_mastery_heatmap,
    get_student_mastery_profile,
    list_visible_sections,
)

PROFICIENCY_COLORS = {
    "Mastered": "#1f77b4",
    "Approaching": "#eadc19",
    "Developing": "#ff7f0e",
    "Intensive": "#d62728",
    "No Evidence": "#D1D5DB",
}

STATUS_ABBREVIATIONS = {
    "Mastered": "M",
    "Approaching": "A",
    "Developing": "D",
    "Intensive": "I",
    "No Evidence": "—",
}


def percent(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "—"


def heatmap_style(value):
    status = {
        "M": "Mastered",
        "A": "Approaching",
        "D": "Developing",
        "I": "Intensive",
        "—": "No Evidence",
    }.get(value)

    if status is None:
        return ""

    background = PROFICIENCY_COLORS[status]
    text = "#111827" if status in {"Approaching", "No Evidence"} else "#FFFFFF"

    return (
        f"background-color: {background}; "
        f"color: {text}; "
        "font-weight: 700; "
        "text-align: center;"
    )


def legend_chip(label: str) -> str:
    color = PROFICIENCY_COLORS[label]
    text = "#111827" if label in {"Approaching", "No Evidence"} else "#FFFFFF"
    return (
        "<span style='display:inline-block;padding:5px 10px;margin-right:7px;"
        f"border-radius:7px;background:{color};color:{text};font-weight:600;'>"
        f"{label}</span>"
    )


page_header(
    "Student mastery map",
    "Standards",
    "See each student's current standard mastery, identify what to work on next, "
    "and turn Core Idea evidence into a growing learning Backpack.",
)

current_user = st.session_state.get("current_user")

if not current_user:
    st.info(
        "Enter your email in the sidebar so the mastery map can apply your data scope."
    )
    st.stop()

sections = list_visible_sections(current_user)

if not sections:
    st.info("No visible course sections are available for this user.")
    st.stop()

filter_1, filter_2, filter_3 = st.columns([1.2, 1.2, 2.2])

subjects = sorted({row["subject"] for row in sections})
with filter_1:
    subject = st.selectbox("Subject", subjects)

grade_levels = sorted(
    {row["grade_level"] for row in sections if row["subject"] == subject}
)
with filter_2:
    grade_level = st.selectbox("Grade / Course", grade_levels)

matching_sections = [
    row
    for row in sections
    if row["subject"] == subject and row["grade_level"] == grade_level
]

section_by_label = {
    (
        f"{row['teacher_name']} · {row['section_name']} · "
        f"{row['school_name']} · {row['student_count']} students"
    ): row["section_id"]
    for row in matching_sections
}

with filter_3:
    section_label = st.selectbox(
        "Class",
        ["All visible sections", *section_by_label],
    )

section_id = (
    None if section_label == "All visible sections" else section_by_label[section_label]
)

mode_col, search_col = st.columns([1.2, 2.5])

with mode_col:
    mode = st.radio(
        "View",
        ["Current Course", "RISE Prep"],
        horizontal=True,
        help=(
            "Current Course keeps standards in code order. "
            "RISE Prep moves standards with the weakest current evidence first."
        ),
    )

with search_col:
    search_text = st.text_input(
        "Find student",
        placeholder="Search by student name or student number",
    )

workspace = get_mastery_heatmap(
    current_user,
    subject=subject,
    grade_level=grade_level,
    section_id=section_id,
    mode=mode,
)

students = workspace["students"]
standards = workspace["standards"]
cells = workspace["cells"]

if search_text.strip():
    needle = search_text.strip().lower()
    students = [
        row
        for row in students
        if needle in row["student_name"].lower()
        or needle in row["student_number"].lower()
    ]

st.markdown(
    "".join(
        legend_chip(label)
        for label in (
            "Mastered",
            "Approaching",
            "Developing",
            "Intensive",
            "No Evidence",
        )
    ),
    unsafe_allow_html=True,
)

st.caption(
    "Select a student row to open Study Hall / RISE priorities. "
    "M = Mastered, A = Approaching, D = Developing, I = Intensive."
)

if not students:
    st.info("No students match the selected filters.")
    st.stop()

cell_by_key = {(int(row["student_id"]), int(row["standard_id"])): row for row in cells}

heatmap_rows = []
for student in students:
    row = {
        "Student ID": student["student_id"],
        "Student": student["student_name"],
        "Student Number": student["student_number"],
        "Learning Pattern": student["archetype"],
        # "Backpack": student["backpack"],
    }

    for standard in standards:
        cell = cell_by_key.get(
            (
                int(student["student_id"]),
                int(standard["standard_id"]),
            )
        )
        row[standard["code"]] = STATUS_ABBREVIATIONS[
            cell["status"] if cell else "No Evidence"
        ]

    heatmap_rows.append(row)

heatmap_frame = pd.DataFrame(heatmap_rows)
standard_codes = [row["code"] for row in standards]

styled = heatmap_frame.style.map(heatmap_style, subset=standard_codes).set_properties(
    subset=standard_codes,
    **{"min-width": "58px", "width": "58px"},
)

event = st.dataframe(
    styled,
    hide_index=True,
    width="stretch",
    height=min(720, 52 + 35 * len(heatmap_frame)),
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Student ID": None,
        "Student": st.column_config.TextColumn("Student", width="medium"),
        "Student Number": st.column_config.TextColumn("ID", width="small"),
        "Learning Pattern": st.column_config.TextColumn(
            "Learning Pattern",
            width="medium",
            help=(
                "A temporary evidence pattern, not a permanent student label. "
                "It changes as mastery changes."
            ),
        ),
        # "Backpack": st.column_config.TextColumn(
        #     "Backpack / Skills",
        #     width="large",
        #     help=(
        #         "Core Ideas already demonstrated (✓) or currently approaching mastery (↗)."
        #     ),
        # ),
        **{
            code: st.column_config.TextColumn(code, width="small")
            for code in standard_codes
        },
    },
)

selected_rows = event.selection.rows

if selected_rows:
    selected_index = selected_rows[0]
    selected_student_id = int(heatmap_frame.iloc[selected_index]["Student ID"])
    st.session_state.standard_selected_student_id = selected_student_id
elif "standard_selected_student_id" not in st.session_state:
    st.session_state.standard_selected_student_id = int(
        heatmap_frame.iloc[0]["Student ID"]
    )

selected_student_id = st.session_state.get("standard_selected_student_id")
visible_student_ids = {int(row["student_id"]) for row in students}

if selected_student_id not in visible_student_ids:
    selected_student_id = int(heatmap_frame.iloc[0]["Student ID"])
    st.session_state.standard_selected_student_id = selected_student_id

profile = get_student_mastery_profile(
    int(selected_student_id),
    subject=subject,
    grade_level=grade_level,
)

if profile is None:
    st.stop()

student = profile["student"]
counts = profile["counts"]

st.divider()
st.subheader(student["student_name"])
st.caption(
    f"{student['student_number']} · {student['school_name']} · "
    f"{subject} · {grade_level}"
)

st.markdown(
    """
    <style>
    [class*="shrink-value"] [data-testid="stMetricValue"] {
        font-size: 1.2rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


with st.container(key="shrink-value"):
    metrics = st.columns(5)
    metrics[0].metric("Learning Pattern", profile["archetype"])
    metrics[1].metric("Mastered", counts["Mastered"])
    metrics[2].metric("Approaching", counts["Approaching"])
    metrics[3].metric(
        "Needs Reteaching",
        counts["Developing"] + counts["Intensive"],
    )
    metrics[4].metric("No Evidence Yet", counts["No Evidence"])

priority_col, backpack_col = st.columns([1.6, 1], gap="large")

with priority_col:
    st.subheader("What should I work on?")
    st.caption(
        "Use this list during Study Hall or test preparation. "
        "Highest-need standards are prioritized automatically."
    )

    priority_rows = [
        row for row in profile["priorities"] if row["status"] != "Mastered"
    ]

    if not priority_rows:
        st.success(
            "Current submitted evidence shows mastery across all standards with evidence. "
            "Use enrichment or extension work."
        )
    else:
        priority_frame = pd.DataFrame(
            [
                {
                    "Priority": index + 1,
                    "Standard": row["code"],
                    "Status": row["status"],
                    "Latest": percent(row["percent"]),
                    "Focus Core Idea": row["focus_core_idea"] or "Build evidence",
                    "Core Idea": percent(row["focus_core_percent"]),
                }
                for index, row in enumerate(priority_rows)
            ]
        )
        st.dataframe(priority_frame, hide_index=True, width="stretch")

with backpack_col:
    st.subheader("Learning Backpack")
    st.caption("The Backpack is derived from Core Ideas—not manually assigned traits.")

    if not profile["backpack"]:
        st.caption(
            "No Core Ideas are Mastered or Approaching yet. "
            "As evidence grows, skills will appear here."
        )
    else:
        for item in profile["backpack"][:8]:
            with st.container(border=True):
                marker = "✓" if item["backpack_status"] == "Packed" else "↗"
                st.markdown(f"**{marker} {item['core_idea_name']}**")
                st.caption(
                    f"{item['standard_code']} · {item['backpack_status']} · "
                    f"{percent(item['percent'])}"
                )

st.subheader("Inspect a standard")

standard_by_code = {row["code"]: row for row in profile["priorities"]}

default_standard_code = (
    priority_rows[0]["code"] if priority_rows else profile["priorities"][0]["code"]
)

inspect_code = st.selectbox(
    "Standard",
    list(standard_by_code),
    index=list(standard_by_code).index(default_standard_code),
    label_visibility="collapsed",
)

inspect_standard = standard_by_code[inspect_code]

with st.container(border=True):
    color = PROFICIENCY_COLORS[inspect_standard["status"]]
    st.markdown(
        (
            "<div style='height:7px;border-radius:7px;"
            f"background:{color};margin-bottom:10px;'></div>"
        ),
        unsafe_allow_html=True,
    )

    title_col, score_col = st.columns([4, 1])
    title_col.markdown(
        f"#### {inspect_standard['code']} · {inspect_standard['status']}"
    )
    title_col.write(inspect_standard["description"])
    score_col.metric("Latest Evidence", percent(inspect_standard["percent"]))

    if inspect_standard["assessment_name"]:
        st.caption(
            f"Latest evidence: {inspect_standard['assessment_name']} · "
            f"{inspect_standard['administered_on']}"
        )
    else:
        st.caption(
            "No submitted CFA evidence has been mapped to this student-standard yet."
        )

standard_core_ideas = [
    row for row in profile["core_ideas"] if row["standard_code"] == inspect_code
]

if standard_core_ideas:
    core_frame = pd.DataFrame(
        [
            {
                "Core Idea": row["core_idea_name"],
                "Status": row["status"],
                "Latest": percent(row["percent"]),
                "Assessment": row["assessment_name"],
                "Date": row["administered_on"],
            }
            for row in standard_core_ideas
        ]
    )
    st.dataframe(core_frame, hide_index=True, width="stretch")
else:
    st.caption(
        "No question-level Core Idea evidence is available for this standard yet."
    )

action_col_1, action_col_2 = st.columns(2)

if action_col_1.button(
    "Open Student Groups",
    type="primary",
    width="stretch",
):
    st.switch_page("views/student_groups.py")

if action_col_2.button(
    "Open PLC Cycles",
    width="stretch",
):
    st.switch_page("views/plc_cycles.py")
