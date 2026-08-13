import streamlit as st

from components.login import render_authenticated_sidebar, require_login
from components.styles import apply_styles
from services.database import get_or_create_user, initialize_demo_database

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

render_authenticated_sidebar()

# Temporary prototype sign-in. This lets us test a real app_users record and
# retain the selected user across page navigation. It is not authentication.
if "current_user" not in st.session_state:
    st.session_state.current_user = None

with st.sidebar:
    st.caption("Prototype user")
    entered_email = st.text_input(
        label="",
        value=st.session_state.get("current_user_email", ""),
        placeholder="Enter your email",
    )

    if st.button("Continue", use_container_width=True):
        try:
            user = get_or_create_user(entered_email)
        except ValueError as error:
            st.error(str(error))
        else:
            st.session_state.current_user = user
            st.session_state.current_user_email = user["email"]
            st.rerun()

    if st.session_state.current_user:
        current_user = st.session_state.current_user
        st.success(f"Signed in as {current_user['display_name']}")
        st.caption(f"{current_user['role']} · {current_user['email']}")

pages = {
    "Workspace": [
        st.Page("views/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True),
        st.Page("views/plc_cycles.py", title="PLC Cycles", icon=":material/cycle:"),
        st.Page("views/assessments.py", title="Assessments", icon=":material/assignment:"),
        st.Page("views/cfa_results.py", title="CFA Results", icon=":material/insights:"),
        # Score entry opens from an assessment, so it is routable but not shown
        # as a separate item in the main navigation.
        st.Page(
            "views/cfa_data_entry.py",
            title="CFA Data Entry",
            url_path="cfa-entry",
            visibility="hidden",
        ),
        st.Page("views/standards.py", title="Standards", icon=":material/checklist:"),
        st.Page("views/student_groups.py", title="Student Groups", icon=":material/groups:"),
        st.Page("views/interventions.py", title="Interventions", icon=":material/clinical_notes:"),
    ],
    "Insights": [
        st.Page("views/reports.py", title="Reports", icon=":material/analytics:"),
        st.Page("views/resources.py", title="Resources", icon=":material/folder:"),
    ],
    "Account": [
        st.Page("views/settings.py", title="Settings", icon=":material/settings:"),
    ],
}

st.logo(":material/school:", icon_image=":material/school:")
st.navigation(pages, position="sidebar").run()