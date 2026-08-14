"""Create editable instructional groups from the latest CFA administration."""

from __future__ import annotations

from collections import defaultdict

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.groups import GROUP_DEFAULTS, get_group_workspace, list_groupable_cycles, save_groups

current_user = st.session_state.get("current_user")

def build_group_payload(grid: pd.DataFrame, focus_by_group: dict[str, str]) -> list[dict]:
    """Convert the editable student grid into the normalized database payload."""
    students_by_group: dict[str, list[int]] = defaultdict(list)
    for _, row in grid.iterrows():
        students_by_group[str(row["Assigned Group"])].append(int(row["student_id"]))
    return [
        {"name": name, "focus": focus_by_group.get(name, ""), "student_ids": student_ids}
        for name, student_ids in students_by_group.items()
        if name.strip()
    ]


page_header(
    "Targeted support",
    "Student Groups",
    "Generate, adjust, and save groups from the latest CFA evidence.",
)

cycles = list_groupable_cycles(current_user)
if not cycles:
    st.info("Submit CFA results for an active cycle before building student groups.")
    st.stop()

cycle_by_label = {f"{row['plc']} · {row['standard']} · {row['name']}": row for row in cycles}
selected_label = st.selectbox("PLC cycle", list(cycle_by_label))
selected_cycle = cycle_by_label[selected_label]
workspace = get_group_workspace(
    int(selected_cycle["cycle_id"]),
    current_user,
)
if workspace is None:
    st.warning("No submitted CFA administration was found for this cycle.")
    st.stop()

if message := st.session_state.pop("groups_flash", None):
    st.success(message)

with st.container(border=True):
    st.subheader(f"{workspace['plc']} PLC · {workspace['standard']}")
    st.caption(
        f"Evidence source: {workspace['latest']['assessment_name']} · "
        f"{workspace['latest']['administration_type']} · {workspace['latest']['administered_on']}"
    )

# The editable grid is the one place teachers move students. Suggested groups
# remain visible so every manual move has a clear starting point.
grid = pd.DataFrame(
    [
        {
            "student_id": row["student_id"],
            "Student": row["student_name"],
            "Status": row["status"],
            "Score": f"{row['percent']:.1f}%" if row["percent"] is not None else "Incomplete",
            "Suggested Group": row["suggested_group"],
            "Assigned Group": row["assigned_group"],
        }
        for row in workspace["students"]
    ]
)
default_names = [item["name"] for item in GROUP_DEFAULTS.values()]
saved_names = [group["name"] for group in workspace["saved_groups"]]

st.markdown("### Group roster")
st.caption("Edit Assigned Group to move a student or create a clearly named custom group. Saved groups reopen here on refresh.")
edited_grid = st.data_editor(
    grid,
    hide_index=True,
    width="stretch",
    disabled=["student_id", "Student", "Status", "Score", "Suggested Group"],
    column_config={
        "student_id": None,
        # A text column permits teachers to name a custom group without needing
        # a separate configuration screen before they can make a decision.
        "Assigned Group": st.column_config.TextColumn("Assigned Group", required=True),
    },
    key=f"group_editor_{workspace['cycle_id']}",
)

st.markdown("### Group plans")
st.caption("Add a brief focus for each group. Rename a group by editing its Assigned Group value in the roster.")
focus_by_group = {item["name"]: item["focus"] for item in GROUP_DEFAULTS.values()}
focus_by_group.update({group["name"]: group["focus"] or "" for group in workspace["saved_groups"]})

for group_name in sorted(edited_grid["Assigned Group"].dropna().unique()):
    count = int((edited_grid["Assigned Group"] == group_name).sum())
    with st.container(border=True):
        name_col, focus_col = st.columns([1.2, 3])
        name_col.markdown(f"**{group_name}**")
        name_col.caption(f"{count} student(s)")
        focus_by_group[group_name] = focus_col.text_input(
            "Instructional focus",
            value=focus_by_group.get(group_name, ""),
            key=f"group_focus_{workspace['cycle_id']}_{group_name}",
        )

save_col, next_col = st.columns([1, 1.5])
if save_col.button("Save Groups", type="primary", width="stretch"):
    try:
        save_groups(
            current_user=current_user,
            cycle_id=workspace["cycle_id"],
            administration_id=workspace["administration_id"],
            groups=build_group_payload(edited_grid, focus_by_group),
        )
    except ValueError as error:
        st.error(str(error))
    else:
        st.session_state.groups_flash = "Student groups saved."
        st.rerun()

if next_col.button("Continue to Interventions →", width="stretch"):
    st.session_state.selected_cycle_id = workspace["cycle_id"]
    st.switch_page("views/interventions.py")

if workspace["saved_groups"]:
    with st.expander("Review saved groups"):
        for group in workspace["saved_groups"]:
            names = ", ".join(member["student_name"] for member in group["members"])
            st.markdown(f"**{group['name']}** — {group['focus'] or 'No focus entered'}")
            st.caption(names)