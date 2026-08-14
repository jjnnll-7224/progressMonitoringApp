"""District Administrator view of PLC Intelligence product telemetry."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from components.styles import page_header
from repositories.product_analytics import get_product_analytics


current_user = st.session_state.get("current_user")

if not current_user or current_user.get("role") != "District Administrator":
    st.error("Product Analytics is available only to District Administrators.")
    st.stop()


page_header(
    "Prototype telemetry",
    "Product Analytics",
    "See who tested the app, where the workflow loses people, what is slow, and what is failing.",
)

range_label = st.segmented_control(
    "Time range",
    ["7 days", "30 days", "All"],
    default="30 days",
    label_visibility="collapsed",
)

days = {
    "7 days": 7,
    "30 days": 30,
    "All": None,
}[range_label or "30 days"]

workspace = get_product_analytics(days)

if not workspace["persistent"]:
    st.warning(
        "Analytics is currently using the local SQLite fallback. "
        "This is fine for Mac testing, but Streamlit Community Cloud can replace local files. "
        "Add a PostgreSQL analytics database URL to Streamlit Secrets before relying on these results."
    )
else:
    st.caption(f"Persistent analytics backend: {workspace['backend']}")

kpi = workspace["kpis"]
cols = st.columns(6)
cols[0].metric("Testers", kpi["testers"])
cols[1].metric("Sessions", kpi["sessions"])
cols[2].metric(
    "Workflow Completion",
    f"{kpi['workflow_completion_rate']:.0f}%"
    if kpi["workflow_completion_rate"] is not None
    else "—",
)
cols[3].metric("Responses Saved", kpi["workflow_completions"])
cols[4].metric("Errors", kpi["errors"])
cols[5].metric(
    "Median Operation",
    f"{kpi['median_operation_ms']:.0f} ms"
    if kpi["median_operation_ms"] is not None
    else "—",
)

st.markdown("### PLC workflow funnel")
st.caption(
    "Each stage counts unique browser sessions that reached that event. "
    "Streamlit reruns do not count as additional funnel conversions."
)

funnel = pd.DataFrame(workspace["funnel"])
if funnel.empty:
    st.info("No tracked workflow events yet.")
else:
    st.dataframe(
        funnel,
        hide_index=True,
        width="stretch",
        column_config={
            "Sessions": st.column_config.NumberColumn(format="%d"),
            "Step conversion": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

left, right = st.columns([1, 1], gap="large")

with left:
    st.markdown("### Most-used pages")
    page_views = pd.DataFrame(workspace["page_views"])
    if page_views.empty:
        st.caption("No page-view events yet.")
    else:
        st.bar_chart(
            page_views.set_index("Page"),
            y="Views",
        )

with right:
    st.markdown("### Performance")
    performance = pd.DataFrame(workspace["performance"])
    if performance.empty:
        st.caption("No timed operations have been recorded yet.")
    else:
        st.dataframe(
            performance,
            hide_index=True,
            width="stretch",
        )

st.markdown("### Recent testers")
sessions = pd.DataFrame(workspace["recent_sessions"])
if sessions.empty:
    st.caption("No tester sessions have been recorded yet.")
else:
    st.dataframe(
        sessions,
        hide_index=True,
        width="stretch",
    )

st.markdown("### Errors")
errors = pd.DataFrame(workspace["errors"])
if errors.empty:
    st.success("No application errors have been captured in this period.")
else:
    st.dataframe(
        errors,
        hide_index=True,
        width="stretch",
    )

with st.expander("What the analytics means"):
    st.markdown(
        """
        **Tester** is the person who entered their name on the demo login.

        **Demo persona** is the Teacher, Coach, Principal, or District Administrator
        account they chose to explore.

        **Workflow Completion** currently means the session reached
        `instructional_response_saved`. It does not require a POST CFA because a
        tester may not have enough time to complete the full instructional cycle.

        **Performance** measures only operations explicitly wrapped with the
        analytics timing helper. Add timing around additional repository calls as
        the prototype grows.
        """
    )
