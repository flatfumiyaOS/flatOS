"""アプリ全体の簡易パスワード保護。

社内アプリを外部にデプロイする際、URLを知っている人なら誰でも開けてしまう状態を
避けるための最低限のログイン画面。st.secrets["APP_PASSWORD"] が設定されていない
場合（ローカル開発など）は何もせず素通りする。
"""

from __future__ import annotations

import streamlit as st


def require_password() -> None:
    try:
        required_password = st.secrets.get("APP_PASSWORD")
    except Exception:
        required_password = None

    if not required_password:
        return
    if st.session_state.get("app_authenticated"):
        return

    st.title("ログイン")
    entered = st.text_input("パスワード", type="password", key="app_password_input")
    if st.button("入る", key="app_password_submit"):
        if entered == required_password:
            st.session_state["app_authenticated"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")
    st.stop()
