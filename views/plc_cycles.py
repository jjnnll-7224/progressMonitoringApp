"""Weekly PLC workspace: plan, assess, analyze, respond, and reassess in one place."""

from __future__ import annotations

from datetime import date, timedelta
from html import escape

import pandas as pd
import streamlit as st
from streamlit_extras.stylable_container import stylable_container
from streamlit_extras.card_selector import card_selector

from components.styles import page_header
from services.analytics import measure, track_event
from repositories.plc_instruction import (
    MASTERY_STATUSES,
    RESPONSE_DEFAULTS,
    RESPONSE_TYPES,
    assign_cfa_to_cycle,
    list_cycle_cfa_section_ids,
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


GROUP_NAMES = {
    "Mastered": "Enrichment",
    "Approaching": "Brief Reteach",
    "Developing": "Small-Group Instruction",
    "Intensive": "Prerequisite Support",
}

GROUP_STATUS_BY_NAME = {name: status for status, name in GROUP_NAMES.items()}

WORKFLOW_PHASES = [
    "Focus",
    "Plan",
    "Teach & Assess",
    "Analyze",
    "Respond",
    "Reassess",
    "Reflect",
]


# ---------- Formatting ----------


def pct(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "—"


def workflow_state(snapshot: dict | None) -> dict:
    """Translate saved cycle evidence into one clear next PLC action."""
    if snapshot is None:
        return {
            "phase_index": 0,
            "title": "Choose the learning focus",
            "body": "Select the priority standard and create the PLC cycle for this week.",
            "button": "Start This Cycle",
        }

    assignments = snapshot["assignments"]
    latest = snapshot["latest"]
    responses = snapshot["saved_responses"]

    if not assignments:
        return {
            "phase_index": 1,
            "title": "Plan the common evidence",
            "body": "Choose or create the CFA that will show whether students learned the priority standard.",
            "button": "Plan the CFA",
        }
    if latest is None:
        return {
            "phase_index": 2,
            "title": "Teach and collect CFA evidence",
            "body": "The CFA is assigned. Enter and submit the common results when the team is ready.",
            "button": "Enter CFA Results",
        }
    if latest.get("administration_type") == "POST" and snapshot["previous"]:
        growth = snapshot["growth"].get("mastery_count_change") or 0
        return {
            "phase_index": 6,
            "title": "Reflect on the cycle",
            "body": f"Follow-up evidence is ready. Mastery changed by {growth:+d} students; capture what the team learned.",
            "button": "Review Growth",
        }
    if not responses:
        return {
            "phase_index": 3,
            "title": "Analyze the CFA evidence",
            "body": "Review the component-skill patterns, misconceptions, and students in each proficiency group.",
            "button": "Analyze Evidence",
        }
    return {
        "phase_index": 5,
        "title": "Monitor the instructional response",
        "body": "The response plan is saved. Deliver the support and collect follow-up evidence on the reassessment date.",
        "button": "Plan Reassessment",
    }


def workflow_progress_html(phase_index: int) -> str:
    steps = []
    for index, phase in enumerate(WORKFLOW_PHASES):
        classes = ["plc-progress-step"]
        if index < phase_index:
            classes.append("is-complete")
        elif index == phase_index:
            classes.append("is-current")
        steps.append(f"<div class='{' '.join(classes)}'>{escape(phase)}</div>")
    return "<div class='plc-progress'>" + "".join(steps) + "</div>"


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
        return _pill(mastery_text, "#DBEAFE", "#1E3A8A") + _pill(
            count_text,
            "#DCFCE7" if count_change >= 0 else "#FEE2E2",
            "#166534" if count_change >= 0 else "#991B1B",
        )

    return _pill(
        f"Mastery {pct(latest['mastery_rate'])} · "
        f"{latest['mastered']}/{latest['completed']} students",
        "#DBEAFE",
        "#1E3A8A",
    )


def _group_assignment_key(cycle_id: int, administration_id: int) -> str:
    return f"group_assignments_{cycle_id}_{administration_id}"


def ensure_group_assignments(
    latest: dict,
    saved_responses: list[dict],
    cycle_id: int,
) -> dict[int, str]:
    """Initialize editable instructional groups from CFA status or saved response membership."""
    administration_id = int(latest["administration_id"])
    key = _group_assignment_key(cycle_id, administration_id)

    if key not in st.session_state:
        assignment_by_student = {
            int(student["student_id"]): student["status"]
            for student in latest["students"]
        }

        # Once responses have been saved, their student membership is the
        # teacher's instructional grouping decision and should reopen that way.
        for response in saved_responses:
            target_status = response["mastery_status"]
            for student_id in response.get("student_ids", []):
                assignment_by_student[int(student_id)] = target_status

        st.session_state[key] = assignment_by_student

    return {
        int(student_id): status for student_id, status in st.session_state[key].items()
    }


def assigned_group_summaries(
    latest: dict,
    assignment_by_student: dict[int, str],
) -> list[dict]:
    """Recompute Core Idea performance after teachers move students between groups."""
    students_by_group = {status: [] for status in MASTERY_STATUSES}

    for student in latest["students"]:
        target = assignment_by_student.get(
            int(student["student_id"]),
            student["status"],
        )
        if target not in students_by_group:
            target = student["status"]
        students_by_group[target].append(student)

    output = []

    for status in MASTERY_STATUSES:
        members = students_by_group[status]
        core_totals: dict[tuple[int | None, str], dict[str, float]] = {}

        for student in members:
            for core in student.get("core_ideas", []):
                key = (
                    core.get("core_idea_id"),
                    str(core.get("core_idea") or "Not specified"),
                )
                totals = core_totals.setdefault(
                    key,
                    {"earned": 0.0, "possible": 0.0},
                )
                totals["earned"] += float(core.get("earned") or 0.0)
                totals["possible"] += float(core.get("possible") or 0.0)

        core_ideas = []
        for (core_idea_id, core_idea), totals in core_totals.items():
            if not totals["possible"]:
                continue
            core_ideas.append(
                {
                    "core_idea_id": core_idea_id,
                    "core_idea": core_idea,
                    "earned": totals["earned"],
                    "possible": totals["possible"],
                    "percent": totals["earned"] / totals["possible"] * 100,
                }
            )
        core_ideas.sort(key=lambda row: row["percent"])

        weakest = core_ideas[0] if core_ideas else None
        average = (
            sum(float(student["percent"]) for student in members) / len(members)
            if members
            else None
        )

        output.append(
            {
                "status": status,
                "group_name": GROUP_NAMES[status],
                "count": len(members),
                "students": members,
                "average": average,
                "core_ideas": core_ideas,
                "weakest_core_idea_id": (weakest["core_idea_id"] if weakest else None),
                "weakest_core_idea": (weakest["core_idea"] if weakest else None),
                "weakest_percent": (weakest["percent"] if weakest else None),
                "recommended_response": RESPONSE_DEFAULTS[status]["label"],
                "recommended_strategy": RESPONSE_DEFAULTS[status]["strategy"],
            }
        )

    return output


def initialize_response_state(
    *,
    cycle_id: int,
    administration_id: int,
    groups: list[dict],
    saved_responses: list[dict],
) -> None:
    saved_by_status = {row["mastery_status"]: row for row in saved_responses}

    for group in groups:
        status = group["status"]
        saved = saved_by_status.get(status)
        type_key = f"response_type_{cycle_id}_{administration_id}_{status}"
        strategy_key = f"response_strategy_{cycle_id}_{administration_id}_{status}"

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


def render_core_idea_profile(group: dict) -> None:
    if not group["core_ideas"]:
        st.caption("No Core Idea evidence is available for this group.")
        return

    for row in group["core_ideas"]:
        label_col, score_col = st.columns([3.2, 1], vertical_alignment="center")
        label_col.caption(row["core_idea"])
        score_col.markdown(
            f"<div style='text-align:right;font-weight:700;'>{pct(row['percent'])}</div>",
            unsafe_allow_html=True,
        )
        st.progress(min(max(float(row["percent"]) / 100, 0.0), 1.0))


# ---------- Dialogs ----------


@st.dialog("Review Students", width="large")
def review_students_dialog(
    latest: dict,
    cycle_id: int,
    saved_responses: list[dict],
) -> None:
    st.caption(
        f"{latest['assessment_name']} · {latest['administration_type']} · "
        f"{latest['administered_on']}"
    )
    st.info(
        "Status is the student's CFA evidence and does not change. "
        "Assigned Group is the PLC team's instructional decision and can be changed."
    )

    assignment_by_student = ensure_group_assignments(
        latest,
        saved_responses,
        cycle_id,
    )
    groups = assigned_group_summaries(
        latest,
        assignment_by_student,
    )

    edited_frames: list[pd.DataFrame] = []
    tabs = st.tabs([f"{group['group_name']} ({group['count']})" for group in groups])

    for tab, group in zip(tabs, groups):
        with tab:
            if not group["students"]:
                st.caption("No students are currently assigned to this group.")
                continue

            frame = pd.DataFrame(
                [
                    {
                        "student_id": int(student["student_id"]),
                        "Student": student["student_name"],
                        "Score": student["percent"],
                        "Status": student["status"],
                        "Assigned Group": group["group_name"],
                    }
                    for student in group["students"]
                ]
            )

            edited = st.data_editor(
                frame,
                hide_index=True,
                width="stretch",
                disabled=[
                    "student_id",
                    "Student",
                    "Score",
                    "Status",
                ],
                column_config={
                    "student_id": None,
                    "Score": st.column_config.ProgressColumn(
                        "Score",
                        min_value=0,
                        max_value=100,
                        format="%.0f%%",
                    ),
                    "Assigned Group": st.column_config.SelectboxColumn(
                        "Assigned Group",
                        options=list(GROUP_STATUS_BY_NAME),
                        required=True,
                        help=(
                            "Move a student to the group that best matches the "
                            "instructional response they need. Their CFA status stays unchanged."
                        ),
                    ),
                },
                key=(
                    f"review_group_{cycle_id}_{latest['administration_id']}_"
                    f"{group['status']}"
                ),
            )
            edited_frames.append(edited)

    if st.button(
        "Apply Group Changes",
        type="primary",
        width="stretch",
    ):
        updated = dict(assignment_by_student)

        for frame in edited_frames:
            for row in frame.to_dict("records"):
                selected_group = str(row["Assigned Group"])
                updated[int(row["student_id"])] = GROUP_STATUS_BY_NAME[selected_group]

        st.session_state[
            _group_assignment_key(
                cycle_id,
                int(latest["administration_id"]),
            )
        ] = updated
        st.session_state[f"selected_group_{cycle_id}_{latest['administration_id']}"] = (
            next(
                (
                    status
                    for status in MASTERY_STATUSES
                    if any(value == status for value in updated.values())
                ),
                "Approaching",
            )
        )
        st.rerun()


@st.dialog("Manage CFA", width="large")
def assign_cfa_dialog(
    cycle_id: int,
    current_user: dict | None,
    preselected_assessment_id: int | None = None,
) -> None:
    st.caption(
        "Choose a reusable CFA that measures at least one standard in this "
        "PLC cycle. You can assign a new CFA or update the class sections "
        "for an existing assignment."
    )

    search = st.text_input(
        "Search CFA library",
        placeholder="Assessment name, type, or standard",
        key=f"cfa_search_{cycle_id}",
    )

    cfas = list_compatible_cfas(
        cycle_id=cycle_id,
        current_user=current_user,
        search=search,
    )

    if not cfas:
        st.info("No compatible CFAs match this search.")

        if st.button(
            "Create a new CFA",
            type="primary",
            width="stretch",
            key=f"create_cfa_empty_{cycle_id}",
        ):
            st.session_state.assessment_target_cycle_id = cycle_id
            st.session_state.show_assessment_form = True
            st.session_state.selected_assessment_id = None
            st.switch_page("views/assessments.py")

        return

    cfa_by_id = {int(row["assessment_id"]): row for row in cfas}

    assessment_ids = list(cfa_by_id)
    default_index = 0

    if preselected_assessment_id is not None:
        preselected_id = int(preselected_assessment_id)

        if preselected_id in assessment_ids:
            default_index = assessment_ids.index(preselected_id)

    def format_cfa(assessment_id: int) -> str:
        row = cfa_by_id[assessment_id]

        standards = (
            row.get("overlapping_standards")
            or row.get("standards")
            or "No standards listed"
        )

        question_count = int(row.get("question_count") or 0)

        return f"{row['name']} · " f"{standards} · " f"{question_count} questions"

    selected_assessment_id = st.selectbox(
        "CFA",
        options=assessment_ids,
        index=default_index,
        format_func=format_cfa,
        key=f"cfa_choice_{cycle_id}",
    )

    selected = cfa_by_id[int(selected_assessment_id)]
    is_update = bool(selected.get("already_assigned"))

    standards_display = (
        selected.get("overlapping_standards") or selected.get("standards") or "—"
    )

    possible_points = float(selected.get("possible_points") or 0)

    info_cols = st.columns(4)

    info_cols[0].metric(
        "Status",
        selected.get("status") or "Unknown",
    )

    info_cols[1].metric(
        "Questions",
        int(selected.get("question_count") or 0),
    )

    info_cols[2].metric(
        "Points",
        f"{possible_points:g}",
    )

    info_cols[3].metric(
        "Standards",
        standards_display,
    )

    if is_update:
        st.info(
            "This CFA is already linked to the PLC cycle. "
            "Adjust the selected class sections and save your changes."
        )
    else:
        st.info("Select the class sections that will administer this CFA.")

    sections = list_visible_cycle_sections(
        cycle_id=cycle_id,
        current_user=current_user,
    )

    section_by_label = {
        (
            f"{row['teacher_name']} · "
            f"{row['section_name']} · "
            f"{row.get('term_name') or 'Current term'} · "
            f"{int(row.get('student_count') or 0)} students"
        ): row
        for row in sections
    }

    if not sections:
        st.warning(
            "No visible class sections match this PLC team's grade and "
            "subject. Section assignments must be loaded before score "
            "entry can begin."
        )

    assigned_section_ids: set[int] = set()

    if is_update:
        assigned_section_ids = set(
            list_cycle_cfa_section_ids(
                current_user=current_user,
                cycle_id=cycle_id,
                assessment_id=int(selected_assessment_id),
            )
        )

    default_section_labels = [
        label
        for label, section in section_by_label.items()
        if int(section["section_id"]) in assigned_section_ids
    ]

    selected_section_labels = st.multiselect(
        "Class sections",
        options=list(section_by_label),
        default=default_section_labels,
        placeholder="Select one or more class sections",
        key=(f"cfa_sections_{cycle_id}_" f"{selected_assessment_id}"),
    )

    selected_section_ids = [
        int(section_by_label[label]["section_id"]) for label in selected_section_labels
    ]

    save_label = "Update CFA assignment" if is_update else "Assign CFA to PLC"

    save_col, create_col = st.columns(2)

    save_clicked = save_col.button(
        save_label,
        type="primary",
        width="stretch",
        disabled=not selected_section_ids,
        key=(f"save_cfa_assignment_{cycle_id}_" f"{selected_assessment_id}"),
    )

    create_clicked = create_col.button(
        "Create a new CFA",
        width="stretch",
        key=f"create_cfa_{cycle_id}",
    )

    if save_clicked:
        try:
            with measure(
                ("update_cfa_assignment" if is_update else "assign_cfa_to_cycle"),
                current_user=current_user,
                page_name="PLC Cycles",
                entity_type="plc_cycle",
                entity_id=cycle_id,
            ):
                cycle_assessment_id = assign_cfa_to_cycle(
                    current_user=current_user,
                    cycle_id=cycle_id,
                    assessment_id=int(selected_assessment_id),
                    section_ids=selected_section_ids,
                )

        except (ValueError, PermissionError) as error:
            st.error(str(error))

        else:
            event_name = "cfa_assignment_updated" if is_update else "cfa_assigned"

            track_event(
                event_name,
                current_user=current_user,
                page_name="PLC Cycles",
                entity_type="plc_cycle",
                entity_id=cycle_id,
                metadata={
                    "assessment_id": int(selected_assessment_id),
                    "cycle_assessment_id": int(cycle_assessment_id),
                    "section_count": len(selected_section_ids),
                },
            )

            st.session_state.cfa_cycle_assessment_id = cycle_assessment_id

            section_count = len(selected_section_ids)
            section_word = "section" if section_count == 1 else "sections"

            if is_update:
                st.session_state.weekly_plc_flash = (
                    f"{selected['name']} was updated for "
                    f"{section_count} class {section_word}."
                )
            else:
                st.session_state.weekly_plc_flash = (
                    f"{selected['name']} is now assigned " "to this PLC cycle."
                )

            st.session_state.pop(
                "plc_assign_assessment_id",
                None,
            )

            st.rerun()

    if create_clicked:
        st.session_state.assessment_target_cycle_id = cycle_id
        st.session_state.show_assessment_form = True
        st.session_state.selected_assessment_id = None
        st.switch_page("views/assessments.py")


# ---------- Page ----------

page_header(
    "Guided instructional workflow",
    "Cycle Workspace",
    "Move from essential learning to common evidence, instructional response, and "
    "follow-up without losing the team's place.",
)

current_user = st.session_state.get("current_user")
teams = list_visible_teams(current_user)
terms = list_terms()

if not teams:
    st.info("No PLC teams are visible for this user.")
    st.stop()
if not terms:
    st.error(
        "No school terms exist yet. Load the weekly PLC calendar schema and term seed."
    )
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

term_by_label = {f"{term['term_name']} · {term['school_year']}": term for term in terms}
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
    bool(snapshot and snapshot["latest"]) for snapshot in snapshot_cache.values()
)
responses_saved = sum(
    bool(snapshot and snapshot["saved_responses"])
    for snapshot in snapshot_cache.values()
)

# Choose the cycle teachers most likely came here to continue: the current week,
# then the first assigned week, then the current empty week for cycle creation.
focus_week = next(
    (week for week in weeks if current_id == int(week["week_id"])),
    None,
)
if focus_week is None or focus_week["cycle_id"] is None:
    focus_week = next(
        (week for week in weeks if week["cycle_id"] is not None), focus_week
    )
if focus_week is None and weeks:
    focus_week = weeks[0]

focus_cycle_id = (
    int(focus_week["cycle_id"])
    if focus_week and focus_week["cycle_id"] is not None
    else None
)
focus_snapshot = snapshot_cache.get(focus_cycle_id) if focus_cycle_id else None
next_step = workflow_state(focus_snapshot)

with st.container(border=True):
    next_col, continue_col = st.columns([4.7, 1.3], vertical_alignment="center")
    with next_col:
        st.markdown(
            "<div class='plc-eyebrow'>YOUR NEXT PLC ACTION</div>",
            unsafe_allow_html=True,
        )
        st.subheader(next_step["title"])
        st.write(next_step["body"])
        if focus_week:
            st.caption(
                f"{selected_team['name']} · {focus_week['label']} · "
                f"{focus_week['cycle_name'] or 'No cycle assigned'}"
            )
    with continue_col:
        if st.button(next_step["button"], type="primary", width="stretch"):
            if focus_week:
                st.session_state[f"plc_week_open_{int(focus_week['week_id'])}"] = True
            st.rerun()

    st.markdown(
        workflow_progress_html(int(next_step["phase_index"])),
        unsafe_allow_html=True,
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

    # Determine whether this is a past week.
    week_end = week["week_end_date"]
    if isinstance(week_end, str):
        week_end = date.fromisoformat(week_end)

    is_past = week_end < date.today() and not is_current
    is_open = bool(st.session_state[open_key])

    cycle_name = week["cycle_name"] or "No PLC assigned"
    arrow = (
        ":material/expand_circle_down:" if is_open else ":material/arrow_forward_ios:"
    )

    # Visual state for each type of week.
    if is_current:
        card_bg = "linear-gradient(135deg, #EEF2FF 0%, #E0E7FF 100%)"
        border_color = "#6366F1"
        text_color = "#1E1B4B"
        font_weight = "700"
        badge_text = "CURRENT WEEK"
        action_text = "[ Close - ]" if is_open else "[ Click to Expand + ]"
        opacity = "1"
        box_shadow = "0 4px 12px rgba(99, 102, 241, 0.15)"

    elif is_past:
        card_bg = "#F9FAFB"
        border_color = "#E5E7EB"
        text_color = "#6B7280"
        font_weight = "500"
        badge_text = "PAST WEEK"
        action_text = "[ Close - ]" if is_open else "[ Expand View + ]"
        opacity = "0.72"
        box_shadow = "none"

    else:
        card_bg = "#FFFFFF"
        border_color = "#D1D5DB"
        text_color = "#1F2937"
        font_weight = "500"
        badge_text = "UPCOMING"
        action_text = "[Close - ]" if is_open else "[Expand + ]"
        opacity = "1"
        box_shadow = "0 1px 3px rgba(15, 23, 42, 0.06)"

    st.markdown(
        f"""
        <style>
        .st-key-toggle_week_{week_id} button {{
            background: {card_bg} !important;
            border: 1px solid {border_color} !important;
            border-left: 6px solid {border_color} !important;
            border-radius: 10px !important;
            box-shadow: {box_shadow} !important;
            color: {text_color} !important;
            min-height: 66px;
            opacity: {opacity};
            padding: 12px 16px !important;
            text-align: left !important;
            transition:
                transform 0.15s ease,
                box-shadow 0.15s ease,
                filter 0.15s ease !important;
        }}

        .st-key-toggle_week_{week_id} button:hover {{
            border-color: {border_color} !important;
            box-shadow: 0 5px 14px rgba(15, 23, 42, 0.10) !important;
            filter: brightness(0.98);
            transform: translateY(-1px);
        }}

        .st-key-toggle_week_{week_id} button p {{
            color: {text_color} !important;
            font-size: 1rem !important;
            font-weight: {font_weight} !important;
            text-align: left !important;
            width: 100%;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container():
        left, right = st.columns(
            [4.7, 2.3],
            vertical_alignment="center",
        )

        button_label = (
            f"{arrow} {badge_text} · {week['label']} · "
            f"{date_label(week['week_start_date'], week['week_end_date'])} · "
            f"{cycle_name} — {action_text}"
        )

        if left.button(
            button_label,
            key=f"toggle_week_{week_id}",
            width="stretch",
        ):
            st.session_state[open_key] = not is_open
            st.rerun()

        right.markdown(
            f"<div style='text-align:right;'>{week_pills(snapshot)}</div>",
            unsafe_allow_html=True,
        )

        if not st.session_state[open_key]:
            continue

        if cycle_id is not None:
            viewed_key = f"_analytics_plc_cycle_viewed_{cycle_id}"
            if not st.session_state.get(viewed_key):
                track_event(
                    "plc_cycle_viewed",
                    current_user=current_user,
                    page_name="PLC Cycles",
                    entity_type="plc_cycle",
                    entity_id=cycle_id,
                    metadata={"week_id": week_id},
                )
                st.session_state[viewed_key] = True

        st.divider()

        if cycle_id is None:
            if week["pacing_standards"]:
                st.info(
                    f"District pacing: **{week['pacing_standards']}**"
                    + (f" — {week['pacing_focus']}" if week["pacing_focus"] else "")
                )

            assign_tab, create_tab = st.tabs(
                ["Assign existing PLC", "Create weekly PLC"]
            )
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
                        standard_label = st.selectbox(
                            "Standard", list(standard_by_label)
                        )
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

        header_left, header_right = st.columns([3.3, 1.7])
        with header_left:
            st.markdown(f"### {cycle['name']}")
            st.caption(
                f"{cycle['team_name']} · {cycle['primary_standard']} · "
                f"{cycle['grade_level']} {cycle['subject']}"
            )
        with header_right:
            if assignments:
                st.caption("Assigned CFA")
                assigned_cfa, change_cfa = st.columns([3.0, 1.0], gap="small")
                with assigned_cfa:
                    st.markdown(f"**{assignments[0]['assessment_name']}**")

                with change_cfa:
                    if st.button(
                        "Change CFA",
                        key=f"change_cfa_{week_id}",
                        width="content",
                        type="secondary",
                    ):
                        assign_cfa_dialog(cycle_id, current_user)
            else:
                st.caption("Evidence source")
                st.markdown("**CFA needed**")

        if week["pacing_standards"]:
            st.caption(
                f"District pacing this week: {week['pacing_standards']}"
                + (f" · {week['pacing_focus']}" if week["pacing_focus"] else "")
            )

        evidence_col, group_col = st.columns([1.0, 1.55], gap="large")
        ## this is the start of the CFA Evidence section, which is the left column of the two-column layout. I want to edit this.
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
                    f"{assignment['standards']} · "
                    f"{assignment['section_count']} section(s) assigned"
                )
                st.info(
                    "The CFA is assigned. Enter and submit PRE results to populate "
                    "the evidence groups."
                )
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
                    st.session_state.cfa_return_page = "views/plc_cycles.py"
                    st.session_state.pop("cfa_administration_id", None)
                    st.switch_page("views/cfa_data_entry.py")
                if change_col.button(
                    "Change CFA / Sections",
                    key=f"change_cfa_{week_id}",
                    width="stretch",
                ):
                    assign_cfa_dialog(cycle_id, current_user)

            else:
                selected_group_key = (
                    f"selected_group_{cycle_id}_{latest['administration_id']}"
                )
                if selected_group_key not in st.session_state:
                    st.session_state[selected_group_key] = next(
                        (
                            status
                            for status in (
                                "Approaching",
                                "Developing",
                                "Intensive",
                                "Mastered",
                            )
                            if latest["counts"][status] > 0
                        ),
                        "Mastered",
                    )

                st.caption(
                    "Select an evidence band to work with that instructional group."
                )
                ## these are the buttons that need HTML styling to match the rest of the page. These should be more like cards stacked on top of each other with a colored border on the left side. The color should match the proficiency color for that status. the background should match the proficiency color but lighter. The text should be the status name and the count of students in that status. The selected status should have a checkmark next to it.

                statuses = list(MASTERY_STATUSES)

                # Convert the currently selected status into the index expected
                # by card_selector.
                current_status = st.session_state[selected_group_key]
                current_index = (
                    statuses.index(current_status) if current_status in statuses else 0
                )

                card_items = [
                    {
                        "icon": ":material/groups:",
                        "title": GROUP_NAMES[status],
                        "description": (
                            f"{status} evidence · "
                            f"{int(latest['counts'][status])} "
                            f"{'student' if int(latest['counts'][status]) == 1 else 'students'}"
                        ),
                    }
                    for status in statuses
                ]

                selected_index = card_selector(
                    card_items,
                    selection_mode="single",
                    default=current_index,
                    key=(
                        f"evidence_card_selector_"
                        f"{cycle_id}_{latest['administration_id']}_{week_id}"
                    ),
                )

                if selected_index is not None:
                    selected_status = statuses[selected_index]

                    if st.session_state[selected_group_key] != selected_status:
                        st.session_state[selected_group_key] = selected_status
                        st.rerun()

                weakest = latest["weakest_core_idea"]
                st.write("")
                st.caption("Weakest Core Idea — all assessed students")
                if weakest:
                    st.markdown(
                        f"**{weakest['core_idea']} — {pct(weakest['percent'])}**"
                    )
                else:
                    st.markdown("**No Core Idea evidence available**")

                if snapshot["previous"]:
                    change = growth["mastery_count_change"] or 0
                    st.caption("Mastery movement")
                    st.markdown(
                        f"**{pct(growth['previous_mastery_rate'])} → "
                        f"{pct(growth['latest_mastery_rate'])} · "
                        f"{change:+d} students at Mastered**"
                    )
                    if growth["newly_mastered_count"] is not None:
                        st.caption(
                            f"{growth['newly_mastered_count']} students moved into "
                            "Mastered from a lower mastery band."
                        )
                else:
                    st.caption(
                        "Mastery movement will appear after the POST CFA is submitted."
                    )

                review_col, results_col = st.columns(2)
                if review_col.button(
                    "Review Students",
                    key=f"review_students_{week_id}",
                    width="stretch",
                ):
                    track_event(
                        "review_students",
                        current_user=current_user,
                        page_name="PLC Cycles",
                        entity_type="plc_cycle",
                        entity_id=cycle_id,
                        metadata={
                            "administration_id": int(latest["administration_id"]),
                        },
                    )
                    review_students_dialog(
                        latest,
                        cycle_id,
                        snapshot["saved_responses"],
                    )
                if results_col.button(
                    "CFA Results",
                    key=f"cfa_results_{week_id}",
                    width="stretch",
                ):
                    st.session_state.selected_cycle_id = cycle_id
                    st.switch_page("views/cfa_results.py")

        with group_col:
            if latest is None:
                st.markdown("#### Instructional Group")
                st.caption(
                    "Submit CFA evidence to see the group profile and choose the "
                    "instructional response."
                )
            else:
                assignment_by_student = ensure_group_assignments(
                    latest,
                    snapshot["saved_responses"],
                    cycle_id,
                )
                groups = assigned_group_summaries(
                    latest,
                    assignment_by_student,
                )
                group_by_status = {group["status"]: group for group in groups}
                selected_status = st.session_state[
                    f"selected_group_{cycle_id}_{latest['administration_id']}"
                ]
                selected_group = group_by_status[selected_status]

                initialize_response_state(
                    cycle_id=cycle_id,
                    administration_id=int(latest["administration_id"]),
                    groups=groups,
                    saved_responses=snapshot["saved_responses"],
                )

                color = PROFICIENCY_COLORS[selected_status]
                st.markdown(
                    (
                        f"<div style='border-top:5px solid {color};"
                        "border:1px solid #E5E7EB;border-radius:12px;"
                        "padding:14px 16px;background:#FFF;'>"
                        f"<div style='font-size:.78rem;color:#6B7280;'>"
                        f"{escape(selected_status)} evidence</div>"
                        f"<div style='font-size:1.35rem;font-weight:760;'>"
                        f"{escape(selected_group['group_name'])}</div>"
                        f"<div style='margin-top:4px;color:#4B5563;'>"
                        f"{selected_group['count']} student"
                        f"{'s' if selected_group['count'] != 1 else ''} assigned · "
                        f"average {pct(selected_group['average'])}</div>"
                        "</div>"
                    ),
                    unsafe_allow_html=True,
                )
                group_color = PROFICIENCY_COLORS[selected_status]

                with stylable_container(
                    key=f"group_detail_{cycle_id}_{selected_status}",
                    css_styles=f"""
                    {{
                        background-color: {group_color}22;
                        border-left: 6px solid {group_color};
                        border-radius: 12px;
                        padding: 1rem;
                        padding-bottom: 2rem;
                        margin-top: 0.5rem;
                    }}
                    """,
                ):
                    profile_col, response_col = st.columns(
                        [1.05, 1],
                        gap="large",
                    )

                    with profile_col:
                        st.markdown("##### Core Idea Profile")

                        render_core_idea_profile(selected_group)

                        with st.expander(f"Students in {selected_group['group_name']}"):
                            if not selected_group["students"]:
                                st.caption("No students are assigned to this group.")
                            else:
                                for student in selected_group["students"]:
                                    st.caption(
                                        f"{student['student_name']} · "
                                        f"{pct(student['percent'])} · "
                                        f"{student['status']}"
                                    )

                    with response_col:
                        st.markdown("##### Instructional Response")

                        type_key = (
                            f"response_type_{cycle_id}_"
                            f"{latest['administration_id']}_{selected_status}"
                        )

                        strategy_key = (
                            f"response_strategy_{cycle_id}_"
                            f"{latest['administration_id']}_{selected_status}"
                        )

                        if selected_group["weakest_core_idea"]:
                            st.caption(
                                "Recommended focus: "
                                f"{selected_group['weakest_core_idea']} · "
                                f"{pct(selected_group['weakest_percent'])}"
                            )

                        st.selectbox(
                            "Response",
                            RESPONSE_TYPES,
                            key=type_key,
                            help=(
                                "The recommended response is loaded automatically. "
                                "Change it if this group needs a different instructional approach."
                            ),
                        )

                        st.text_area(
                            "Strategy",
                            key=strategy_key,
                            height=130,
                            placeholder=(
                                "What will the PLC do differently with this group?"
                            ),
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
                    else max(
                        date.today() + timedelta(days=7),
                        date.fromisoformat(week["week_end_date"]),
                    )
                )

                st.write("")
                reassess_col, save_col, post_col = st.columns(
                    [1.15, 1.15, 1.5],
                    vertical_alignment="bottom",
                )
                reassess_date = reassess_col.date_input(
                    "Reassess",
                    value=default_reassess,
                    key=(f"reassess_date_{cycle_id}_" f"{latest['administration_id']}"),
                )

                response_payloads = []
                for group in groups:
                    if int(group["count"]) == 0:
                        continue
                    status = group["status"]
                    response_payloads.append(
                        {
                            "mastery_status": status,
                            "response_type": st.session_state[
                                f"response_type_{cycle_id}_"
                                f"{latest['administration_id']}_{status}"
                            ],
                            "strategy": st.session_state[
                                f"response_strategy_{cycle_id}_"
                                f"{latest['administration_id']}_{status}"
                            ],
                            "focus_core_idea_id": group["weakest_core_idea_id"],
                            "focus_text": group["weakest_core_idea"],
                            "student_ids": [
                                int(student["student_id"])
                                for student in group["students"]
                            ],
                        }
                    )

                if save_col.button(
                    "Save Responses",
                    type="primary",
                    key=f"save_response_{week_id}",
                    width="stretch",
                ):
                    try:
                        with measure(
                            "save_instructional_responses",
                            current_user=current_user,
                            page_name="PLC Cycles",
                            entity_type="plc_cycle",
                            entity_id=cycle_id,
                        ):
                            save_instructional_responses(
                                current_user=current_user,
                                cycle_id=cycle_id,
                                source_administration_id=int(
                                    latest["administration_id"]
                                ),
                                reassess_date=reassess_date.isoformat(),
                                responses=response_payloads,
                            )
                    except (ValueError, PermissionError) as error:
                        st.error(str(error))
                    else:
                        track_event(
                            "instructional_response_saved",
                            current_user=current_user,
                            page_name="PLC Cycles",
                            entity_type="plc_cycle",
                            entity_id=cycle_id,
                            metadata={
                                "source_administration_id": int(
                                    latest["administration_id"]
                                ),
                                "reassess_date": reassess_date.isoformat(),
                                "response_count": len(response_payloads),
                            },
                        )
                        st.session_state.weekly_plc_flash = (
                            f"Instructional responses saved for {week['label']}."
                        )
                        st.rerun()

                if snapshot["saved_responses"]:
                    if post_col.button(
                        "Create / Open POST CFA",
                        key=f"post_cfa_{week_id}",
                        width="stretch",
                    ):
                        try:
                            with measure(
                                "create_or_get_post_reassessment",
                                current_user=current_user,
                                page_name="PLC Cycles",
                                entity_type="plc_cycle",
                                entity_id=cycle_id,
                            ):
                                administration_id = create_or_get_post_reassessment(
                                    current_user=current_user,
                                    cycle_id=cycle_id,
                                    source_administration_id=int(
                                        latest["administration_id"]
                                    ),
                                    administered_on=reassess_date.isoformat(),
                                )
                        except (ValueError, PermissionError) as error:
                            st.error(str(error))
                        else:
                            track_event(
                                "post_cfa_created",
                                current_user=current_user,
                                page_name="PLC Cycles",
                                entity_type="plc_cycle",
                                entity_id=cycle_id,
                                metadata={
                                    "administration_id": int(administration_id),
                                    "reassess_date": (reassess_date.isoformat()),
                                },
                            )
                            st.session_state.cfa_cycle_assessment_id = int(
                                latest["cycle_assessment_id"]
                            )
                            st.session_state.cfa_return_page = "views/plc_cycles.py"
                            st.session_state.cfa_administration_id = administration_id
                            st.switch_page("views/cfa_data_entry.py")
                else:
                    post_col.caption("Save responses before creating the POST CFA.")

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
                        user_id=(
                            int(current_user["user_id"]) if current_user else None
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

            for saved_note in list_week_notes(int(week["week_assignment_id"]))[:3]:
                with st.container(border=True):
                    st.caption(
                        f"{saved_note['author_name']} · {saved_note['created_at']}"
                    )
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
                st.session_state.weekly_plc_flash = (
                    f"Cleared {week['label']} assignment."
                )
                st.rerun()
