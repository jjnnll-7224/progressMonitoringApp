"""Plan interventions for saved groups, then create a POST CFA reassessment."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from components.styles import page_header
from repositories.interventions import (
    INTERVENTION_STATUSES,
    INTERVENTION_TYPES,
    create_intervention,
    create_post_reassessment,
    get_intervention_workspace,
    list_intervention_cycles,
    set_intervention_status,
)


page_header(
    "Reteach and reassess",
    "Interventions",
    "Assign strategies to saved groups, then measure pre/post growth.",
)

current_user = st.session_state.get("current_user")

# Student Groups stores this key before it brings a teacher here. The selectbox
# still works normally if this page is opened directly from the sidebar.
cycles = list_intervention_cycles(current_user)
if not cycles:
    st.info("Save Student Groups for a cycle before creating an intervention.")
    if st.button("Open Student Groups"):
        st.switch_page("views/student_groups.py")
    st.stop()

cycle_by_label = {f"{row['plc']} · {row['standard']} · {row['name']}": row for row in cycles}
labels = list(cycle_by_label)
selected_cycle_id = st.session_state.get("selected_cycle_id")
default_index = next(
    (index for index, row in enumerate(cycles) if row["cycle_id"] == selected_cycle_id), 0
)
selected_label = st.selectbox("PLC cycle", labels, index=default_index)
cycle = cycle_by_label[selected_label]
workspace = get_intervention_workspace(
    int(cycle["cycle_id"]),
    current_user,
)
if workspace is None:
    st.error("That PLC cycle could not be found.")
    st.stop()

if message := st.session_state.pop("intervention_flash", None):
    st.success(message)

with st.container(border=True):
    st.subheader(f"{workspace['plc']} PLC · {workspace['standard']}")
    st.caption(f"Cycle stage: {workspace['stage']} · {workspace['start_date']} to {workspace['end_date']}")
    if workspace["growth_points"] is not None:
        st.metric("Latest assessment growth", f"{workspace['growth_points']:+.1f} points")

left, right = st.columns([1.15, 1], gap="large")
with left:
    st.markdown("### Create intervention")
    st.caption("Each plan connects to one saved group. You can create separate plans for different groups.")

    group_by_label = {
        f"{group['name']} · {len(group['members'])} student(s)": group
        for group in workspace["groups"]
    }
    if not group_by_label:
        st.warning("No saved groups are available for this cycle.")
        st.stop()

    with st.form(f"intervention_form_{workspace['cycle_id']}", clear_on_submit=True):
        chosen_group_label = st.selectbox("Student group", list(group_by_label))
        chosen_group = group_by_label[chosen_group_label]
        name = st.text_input("Intervention name", value=f"{chosen_group['name']} support")
        type_col, owner_col = st.columns(2)
        intervention_type = type_col.selectbox("Intervention type", INTERVENTION_TYPES)
        owner_by_label = {"Unassigned": None} | {
            member["display_name"]: member["user_id"] for member in workspace["team_members"]
        }
        owner_label = owner_col.selectbox("Owner", list(owner_by_label))
        start_col, end_col, status_col = st.columns(3)
        start = start_col.date_input("Start date", value=date.today())
        end = end_col.date_input("End date", value=date.today() + timedelta(days=7))
        status = status_col.selectbox("Status", INTERVENTION_STATUSES, index=0)
        strategy = st.text_area("Instructional strategy", placeholder="What will the teacher do with this group?")
        evidence = st.text_input("Evidence to collect", placeholder="Example: exit ticket or annotated response")
        criterion = st.text_input("Success criterion", placeholder="Example: 80% or higher on the post-CFA")
        notes = st.text_area("Notes (optional)")
        create_clicked = st.form_submit_button("Save Intervention", type="primary", width="stretch")

    if create_clicked:
        try:
            create_intervention(
                current_user_id=current_user,
                cycle_id=int(workspace["cycle_id"]), group_id=int(chosen_group["group_id"]),
                name=name, intervention_type=intervention_type,
                owner_user_id=owner_by_label[owner_label], start_date=start.isoformat(),
                end_date=end.isoformat(), strategy=strategy, evidence_to_collect=evidence,
                success_criterion=criterion, notes=notes, status=status,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state.intervention_flash = "Intervention saved."
            st.rerun()

with right:
    st.markdown("### Group context")
    for group in workspace["groups"]:
        with st.container(border=True):
            st.markdown(f"**{group['name']}** · {len(group['members'])} student(s)")
            st.caption(group["focus"] or "No instructional focus entered")
            st.caption(", ".join(member["student_name"] for member in group["members"]))

st.markdown("### Current intervention plans")
if not workspace["interventions"]:
    st.info("No intervention plans have been saved for this cycle yet.")
else:
    for intervention in workspace["interventions"]:
        with st.container(border=True):
            title_col, status_col = st.columns([3, 1])
            title_col.markdown(f"**{intervention['name']}**")
            new_status = status_col.selectbox(
                "Status", INTERVENTION_STATUSES,
                index=INTERVENTION_STATUSES.index(intervention["status"]),
                key=f"intervention_status_{intervention['intervention_id']}",
                label_visibility="collapsed",
            )
            if new_status != intervention["status"]:
                set_intervention_status(intervention["intervention_id"], new_status, current_user)
                st.rerun()
            st.caption(
                f"{intervention['group_name'] or 'No group'} · {intervention['student_count']} student(s) · "
                f"Owner: {intervention['owner_name']} · {intervention['start_date']} to {intervention['end_date'] or 'No end date'}"
            )
            st.write(intervention["strategy"] or "No instructional strategy entered.")
            if intervention["success_criterion"]:
                st.caption(f"Success criterion: {intervention['success_criterion']}")

st.markdown("### Reassessment")
st.caption("Create a POST administration when the intervention is ready to measure. Score entry remains on the CFA Data Entry page.")
reassess_col, _ = st.columns([1, 3])
reassessment_date = reassess_col.date_input("POST CFA date", value=date.today())
if reassess_col.button("Create POST CFA", type="primary", width="stretch"):
    try:
        administration_id = create_post_reassessment(
            int(workspace["cycle_id"]), reassessment_date.isoformat(), current_user,
        )
    except ValueError as error:
        st.error(str(error))
    else:
        st.session_state.cfa_administration_id = administration_id
        st.session_state.cfa_assessment_id = workspace["latest"]["assessment_id"]
        st.switch_page("views/cfa_data_entry.py")