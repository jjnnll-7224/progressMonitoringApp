import streamlit as st


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --plc-blue: #2563EB;
            --plc-green: #22C55E;
            --plc-yellow: #EAB308;
            --plc-orange: #F97316;
            --plc-red: #EF4444;
            --plc-bg: #F8FAFC;
            --plc-text: #0F172A;
            --plc-muted: #64748B;
        }
        .stApp { background: var(--plc-bg); color: var(--plc-text); }
        [data-testid="stMetric"] {
            background: white;
            border: 1px solid #E2E8F0;
            border-radius: 14px;
            padding: 18px;
            box-shadow: 0 2px 10px rgba(15, 23, 42, .05);
        }
        [data-testid="stMetricLabel"] { color: var(--plc-muted); }
        .plc-eyebrow {
            color: var(--plc-blue);
            font-size: .75rem;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
        }
        .plc-subtitle { color: var(--plc-muted); margin-top: -.6rem; }
        div.stButton > button[kind="primary"] { background: var(--plc-blue); }

        [data-testid="stForm"] {
            background: white;
            border: 1px solid #E2E8F0;
            border-radius: 14px;
            padding: 16px 18px 18px 18px;
            box-shadow: 0 2px 10px rgba(15, 23, 42, .05);
        }
        [data-testid="stForm"] label,
        [data-testid="stForm"] p,
        [data-testid="stForm"] .stMarkdown {
            color: var(--plc-text) !important;
        }
        [data-testid="stForm"] .stTextInput input,
        [data-testid="stForm"] .stTextArea textarea,
        [data-testid="stForm"] .stSelectbox div[data-baseweb="select"] > div,
        [data-testid="stForm"] .stNumberInput input,
        [data-testid="stForm"] .stDateInput input {
            background: white !important;
            color: var(--plc-text) !important;
            border: 1px solid #CBD5E1 !important;
            border-radius: 8px;
        }
        [data-testid="stForm"] .stTextInput input::placeholder,
        [data-testid="stForm"] .stTextArea textarea::placeholder {
            color: var(--plc-muted) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(eyebrow: str, title: str, subtitle: str) -> None:
    st.markdown(f'<div class="plc-eyebrow">{eyebrow}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<div class="plc-subtitle">{subtitle}</div>', unsafe_allow_html=True)

