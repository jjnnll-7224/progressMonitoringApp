"""Front-door login experience for the public Streamlit demo."""

from __future__ import annotations

import hmac
import streamlit as st

from repositories.auth import get_demo_user, list_demo_users


ROLE_DESCRIPTIONS = {
    "Teacher": "Explore classroom mastery, CFAs, PLC cycles, and instructional groups.",
    "Coach": "See PLC work and teacher support across assigned classrooms.",
    "Principal": "Review school-wide PLC implementation and student evidence.",
    "School Administrator": "Review school-wide PLC implementation and student evidence.",
    "District Administrator": "Explore district-level implementation, outcomes, and configuration.",
}


def _demo_password() -> str:
    try:
        return str(st.secrets["demo_auth"]["password"])
    except Exception:
        return "plcdemo"


def _verify_password(candidate: str) -> bool:
    return hmac.compare_digest(candidate, _demo_password())


def _login_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display: none; }
        .block-container { max-width: 1080px; padding-top: 3.2rem; }
        .plc-eyebrow {
            font-size:.78rem; font-weight:750; letter-spacing:.12em;
            text-transform:uppercase; opacity:.62; margin-bottom:.45rem;
        }
        .plc-title {
            font-size:2.65rem; line-height:1.04; font-weight:800;
            margin-bottom:.7rem;
        }
        .plc-subtitle {
            font-size:1.05rem; line-height:1.55; opacity:.78;
            max-width:720px; margin-bottom:1.4rem;
        }
        .plc-feature {
            border:1px solid rgba(128,128,128,.25);
            border-radius:14px; padding:1rem 1.05rem; min-height:118px;
        }
        .plc-feature-title { font-weight:750; margin-bottom:.25rem; }
        .plc-footer { margin-top:2rem; opacity:.55; font-size:.78rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_login_page() -> None:
    _login_css()

    st.markdown(
        '<div class="plc-eyebrow">PLC Intelligence</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="plc-title">Turn assessment evidence into the next instructional move.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="plc-subtitle">
        A guided PLC workspace for standards mastery, common formative assessments,
        student grouping, reteaching, and continuous instructional improvement.
        This public deployment uses demonstration data only.
        </div>
        """,
        unsafe_allow_html=True,
    )

    features = st.columns(3, gap="medium")
    feature_copy = [
        ("See the learning",
         "Standards heatmaps and Core Idea evidence show what students know and what they should work on next."),
        ("Focus the PLC",
         "Move from scores to shared questions, targeted groups, notes, and instructional decisions."),
        ("Close the loop",
         "Connect CFA evidence to reteaching and reassessment instead of treating assessment as the end of the process."),
    ]

    for column, (title, body) in zip(features, feature_copy):
        with column:
            st.markdown(
                f"""
                <div class="plc-feature">
                    <div class="plc-feature-title">{title}</div>
                    {body}
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.write("")
    left, right = st.columns([1.15, 1], gap="large")
    users = list_demo_users()

    with left:
        st.subheader("Explore the demo")
        st.caption("Choose a role to see how the same evidence changes for different users.")

        if not users:
            st.error("No demo users exist in app_users. Initialize the demo database first.")
            return

        user_by_label = {
            f"{user['display_name']} — {user['role']}": user
            for user in users
        }

        selected_label = st.selectbox(
            "Demo user",
            list(user_by_label),
            label_visibility="collapsed",
        )
        selected = user_by_label[selected_label]

        st.markdown(f"**{selected['role']}**")
        st.caption(
            ROLE_DESCRIPTIONS.get(
                selected["role"],
                "Explore this role in the PLC Intelligence demo.",
            )
        )
        if selected.get("school_name"):
            st.caption(selected["school_name"])

    with right:
        st.subheader("Sign in")
        with st.form("plc_demo_login"):
            password = st.text_input(
                "Demo password",
                type="password",
                placeholder="Enter demo password",
            )
            submit = st.form_submit_button(
                "Enter PLC Intelligence",
                type="primary",
                width="stretch",
            )

        if submit:
            if not _verify_password(password):
                st.error("The demo password is incorrect.")
            else:
                current_user = get_demo_user(int(selected["user_id"]))
                if current_user is None:
                    st.error("That demo user no longer exists.")
                else:
                    st.session_state.current_user = current_user
                    st.session_state.authenticated = True
                    st.session_state.auth_user_id = int(current_user["user_id"])
                    st.rerun()

        st.caption(
            "The public demo password is stored in Streamlit Community Cloud Secrets, not in GitHub."
        )

    st.markdown(
        """
        <div class="plc-footer">
        Prototype environment · Demonstration student data only ·
        Not connected to a production student information system
        </div>
        """,
        unsafe_allow_html=True,
    )


def require_login() -> bool:
    if (
        st.session_state.get("authenticated")
        and st.session_state.get("current_user")
    ):
        return True

    render_login_page()
    return False


def render_authenticated_sidebar() -> None:
    current_user = st.session_state.get("current_user")
    if not current_user:
        return

    with st.sidebar:
        st.markdown("### PLC Intelligence")
        st.caption("Demo environment")
        st.divider()
        st.markdown(f"**{current_user['display_name']}**")
        st.caption(current_user["role"])

        if current_user.get("school_name"):
            st.caption(current_user["school_name"])

        st.divider()

        if st.button("Sign out", width="stretch", key="plc_demo_logout"):
            for key in (
                "authenticated",
                "auth_user_id",
                "current_user",
                "selected_assessment_id",
                "cfa_cycle_assessment_id",
                "cfa_assessment_id",
                "cfa_administration_id",
                "cfa_section_id",
            ):
                st.session_state.pop(key, None)
            st.rerun()
