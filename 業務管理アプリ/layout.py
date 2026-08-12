"""全ページ共通のヘッダー表示。"""

import base64
from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).parent / "assets" / "corp_logo_white.svg"
HEADER_BG_COLOR = "#1a1311"  # ロゴ本来の色を背景に使い、白ロゴが映えるようにする


def show_header() -> None:
    svg_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode()
    st.markdown(
        f"""
        <div style="background-color:{HEADER_BG_COLOR}; padding: 14px 20px;
                    border-radius: 6px; margin-bottom: 12px;">
            <img src="data:image/svg+xml;base64,{svg_b64}" width="160">
        </div>
        """,
        unsafe_allow_html=True,
    )
