"""アプリ全体の簡易パスワード保護。

社内アプリを外部にデプロイする際、URLを知っている人なら誰でも開けてしまう状態を
避けるための最低限のログイン画面。st.secrets["APP_PASSWORD"] が設定されていない
場合（ローカル開発など）は何もせず素通りする。

「Googleでログイン」を押すと、一度Google側のページに画面が移動して戻ってくる。
この「戻ってくる」タイミングでStreamlitのセッション（st.session_state）が
作り直されてしまうため、session_stateだけで認証状態を覚えていると、Googleログイン
のたびにパスワードを二重に聞かれてしまう。これを避けるため、認証済みの状態を
ブラウザのCookieにも保存し、一定期間はパスワード再入力を省略できるようにする。
"""

from __future__ import annotations

import hashlib
import time

import extra_streamlit_components as stx
import streamlit as st

COOKIE_NAME = "flatos_authenticated"
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30日


def _password_token(password: str) -> str:
    # Cookieには実際のパスワードそのものではなく、そのハッシュ値だけを保存する。
    return hashlib.sha256(password.encode()).hexdigest()


def require_password() -> None:
    try:
        required_password = st.secrets.get("APP_PASSWORD")
    except Exception:
        required_password = None

    if not required_password:
        return
    if st.session_state.get("app_authenticated"):
        return

    cookie_manager = stx.CookieManager(key="auth_cookie_manager")
    if cookie_manager.get(COOKIE_NAME) == _password_token(required_password):
        st.session_state["app_authenticated"] = True
        return

    st.title("ログイン")
    entered = st.text_input("パスワード", type="password", key="app_password_input")
    if st.button("入る", key="app_password_submit"):
        if entered == required_password:
            st.session_state["app_authenticated"] = True
            cookie_manager.set(
                COOKIE_NAME,
                _password_token(required_password),
                key="app_password_cookie_set",
                max_age=COOKIE_MAX_AGE_SECONDS,
            )
            time.sleep(0.5)  # Cookie書き込み用コンポーネントがブラウザ側で実行される猶予を与える
            st.rerun()
        else:
            st.error("パスワードが違います。")
    st.stop()
