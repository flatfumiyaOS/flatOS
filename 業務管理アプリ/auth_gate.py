"""アプリ全体の簡易パスワード保護。

社内アプリを外部にデプロイする際、URLを知っている人なら誰でも開けてしまう状態を
避けるための最低限のログイン画面。st.secrets["APP_PASSWORD"] が設定されていない
場合（ローカル開発など）は何もせず素通りする。

「Googleでログイン」を押すと、一度Google側のページに画面が移動して戻ってくる。
この「戻ってくる」タイミングでStreamlitのセッション（st.session_state）が
作り直されてしまうため、session_stateだけで認証状態を覚えていると、Googleログイン
のたびにパスワードを二重に聞かれてしまう。これを避けるため、認証済みの状態を
ブラウザのCookieにも保存し、一定期間はパスワード再入力を省略できるようにする。

Cookieの読み取りには st.context.cookies を使う（ブラウザが送ってきたHTTP
リクエストのCookieヘッダーをそのまま同期的に読めるStreamlit標準の仕組み）。
以前はextra_streamlit_componentsのCookieManagerコンポーネントを使っていたが、
あれはブラウザ側との通信が非同期のため、ページ再読み込み直後の最初のスクリプト
実行ではまだ値を受け取れておらず、実際にはCookieが残っているのに再ログインを
求められてしまう不具合があった。st.context.cookiesならその往復を待つ必要が
無く、再読み込み直後から確実に読み取れる。
書き込み側（ログイン成功時にCookieをセットする部分）だけは、Streamlitに
Cookie書き込みの標準APIが無いため、JavaScriptでdocument.cookieを直接
設定する方法を使う。
"""

from __future__ import annotations

import hashlib
import time

import streamlit as st
import streamlit.components.v1 as components

COOKIE_NAME = "flatos_authenticated"
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60  # 30日


def _password_token(password: str) -> str:
    # Cookieには実際のパスワードそのものではなく、そのハッシュ値だけを保存する。
    return hashlib.sha256(password.encode()).hexdigest()


def _set_auth_cookie(token: str) -> None:
    is_https = str(st.context.url).startswith("https")
    secure_attr = "; Secure" if is_https else ""
    components.html(
        f"""
        <script>
        (function() {{
            const doc = window.parent.document;
            doc.cookie = "{COOKIE_NAME}={token}; path=/; max-age={COOKIE_MAX_AGE_SECONDS}; "
                + "SameSite=Lax{secure_attr}";
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def require_password() -> None:
    try:
        required_password = st.secrets.get("APP_PASSWORD")
    except Exception:
        required_password = None

    if not required_password:
        return
    if st.session_state.get("app_authenticated"):
        return

    if st.context.cookies.get(COOKIE_NAME) == _password_token(required_password):
        st.session_state["app_authenticated"] = True
        return

    st.title("ログイン")
    entered = st.text_input("パスワード", type="password", key="app_password_input")
    if st.button("入る", key="app_password_submit"):
        if entered == required_password:
            st.session_state["app_authenticated"] = True
            _set_auth_cookie(_password_token(required_password))
            time.sleep(0.5)  # Cookie書き込み用のJSがブラウザ側で実行される猶予を与える
            st.rerun()
        else:
            st.error("パスワードが違います。")
    st.stop()
