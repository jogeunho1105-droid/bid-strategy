"""Stable Streamlit entrypoint: strategy and rationale only, no password or uploads."""
import streamlit as st
from public_board import render

st.set_page_config(page_title="투찰전략 · 적용근거", page_icon="📋", layout="wide", initial_sidebar_state="collapsed")
# Explicit routing prevents the historical pages directory from exposing old tools.
st.navigation([st.Page(render, title="투찰전략 · 적용근거", default=True)], position="hidden").run()
