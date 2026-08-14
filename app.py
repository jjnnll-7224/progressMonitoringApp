import streamlit as st

from components.login import render_authenticated_sidebar, require_login
from components.styles import apply_styles
from services.analytics import track_page_from_context
from services.database import initialize_demo_database


st.set_page_config(
    page_title="PLC Intelligence",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_styles()
initialize_demo_database()

if not require_login():
    st.stop()

current_user = st.session_state.get("current_user")
render_authenticated_sidebar()
track_page_from_context(current_user)

workspace_pages = [
    st.Page(
        "views/dashboard.py",
        title="Dashboard",
        icon=":material/dashboard:",
        default=True,
    ),
    st.Page(
        "views/plc_cycles.py",
        title="PLC Cycles",
        icon=":material/cycle:",
    ),
    st.Page(
        "views/assessments.py",
        title="Assessments",
        icon=":material/assignment:",
    ),
    st.Page(
        "views/cfa_results.py",
        title="CFA Results",
        icon=":material/insights:",
    ),
    st.Page(
        "views/cfa_data_entry.py",
        title="CFA Data Entry",
        url_path="cfa-entry",
        visibility="hidden",
    ),
    st.Page(
        "views/standards.py",
        title="Standards",
        icon=":material/checklist:",
    ),
    st.Page(
        "views/student_groups.py",
        title="Student Groups",
        url_path="student-groups",
        visibility="hidden",
    ),
    st.Page(
        "views/interventions.py",
        title="Interventions",
        url_path="interventions",
        visibility="hidden",
    ),
]

pages = {
    "Workspace": workspace_pages,
    "Insights": [
        st.Page(
            "views/reports.py",
            title="Reports",
            icon=":material/analytics:",
        ),
        st.Page(
            "views/resources.py",
            title="Resources",
            icon=":material/folder:",
        ),
    ],
    "Account": [
        st.Page(
            "views/settings.py",
            title="Settings",
            icon=":material/settings:",
        ),
    ],
}

if current_user and current_user.get("role") == "District Administrator":
    pages["Administration"] = [
        st.Page(
            "views/product_analytics.py",
            title="Product Analytics",
            icon=":material/monitoring:",
            url_path="product-analytics",
        ),
    ]

st.logo(":material/school:", icon_image=":material/school:")
st.navigation(pages, position="sidebar").run()
