import streamlit as st
from components.styles import page_header
page_header("Configuration", "Settings", "Configure scoring thresholds and prototype defaults.")
st.number_input("Mastered threshold", 0, 100, 80)
st.number_input("Approaching threshold", 0, 100, 70)
st.number_input("Developing threshold", 0, 100, 50)

