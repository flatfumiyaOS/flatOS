"""ユーザー自身のGoogleアカウントでログインするためのOAuth認証の共通部品。

新しい見積書スプレッドシートを作成する際、サービスアカウントには保存容量が
ないため、ユーザー本人のGoogleアカウントの権限でファイルを作成する必要がある。
そのためのログイン（OAuth認可コードフロー）をここで扱う。
"""

from __future__ import annotations

import os

import streamlit as st
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

# ユーザーが過去にこのOAuthクライアントへ別のスコープ（例: 以前試験的に追加していた
# gmail.readonly）を一度でも許可していると、Googleはauthorization_url()の
# include_granted_scopes="true"により、今回明示的に要求していないスコープも
# まとめて返してくることがある。その結果、fetch_token()時に実際に返ってきた
# スコープが要求したSCOPESと一致せず、google-auth-oauthlib側が
# 「Scope has changed」エラーを投げて毎回ログインに失敗していた。
# OAUTHLIB_RELAX_TOKEN_SCOPEを設定すると、この厳密な一致チェックを緩め、
# 実際に許可された（要求より多い）スコープをそのまま受け入れるようになる。
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_redirect_uri() -> str:
    """OAuthのリダイレクト先URL。

    ローカル開発では http://localhost:8501 のままでよいが、Streamlit Community
    Cloudなどにデプロイした場合は、実際に割り当てられたアプリのURLを
    st.secrets["GOOGLE_OAUTH_REDIRECT_URI"] に設定する必要がある
    （Google Cloud Console側の「承認済みのリダイレクトURI」にも同じURLの登録が必要）。
    """
    try:
        return st.secrets.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8501")
    except Exception:
        return "http://localhost:8501"


REDIRECT_URI = _get_redirect_uri()


def _get_client_config() -> dict:
    return {
        "web": {
            "client_id": st.secrets["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": st.secrets["GOOGLE_OAUTH_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


def _build_flow() -> Flow:
    # Googleへのリダイレクトを挟む間にStreamlitのsession_stateが失われることがあり、
    # PKCE用のcode_verifierを引き継げず認証に失敗するケースがあった。このOAuthクライアントは
    # クライアントシークレットを持つ「ウェブアプリケーション」種別でPKCEは必須ではないため、
    # autogenerate_code_verifier=Falseにして最初から使わないようにする。
    return Flow.from_client_config(
        _get_client_config(),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=False,
    )


def get_login_url() -> str:
    """Googleログイン画面へのURLを返す。"""
    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    return auth_url


def handle_login_redirect() -> None:
    """Googleログイン後のリダイレクトを処理し、認証情報をセッションに保存する。"""
    if "google_credentials" in st.session_state:
        return

    code = st.query_params.get("code")
    if not code:
        return

    # 認可コードは一度しか使えないため、成功・失敗にかかわらずURLから消しておく
    # （残したままだと再実行時に同じコードで再試行して失敗してしまう）。
    st.query_params.clear()

    flow = _build_flow()
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001 — 画面にエラー内容を表示するため
        st.error(f"Googleログインに失敗しました。もう一度「Googleでログイン」からやり直してください。\n\n{exc}")
        return
    st.session_state["google_credentials"] = flow.credentials


def get_credentials() -> Credentials | None:
    """ログイン済みならその認証情報を返す。未ログインならNone。"""
    return st.session_state.get("google_credentials")


def is_logged_in() -> bool:
    return get_credentials() is not None


def logout() -> None:
    st.session_state.pop("google_credentials", None)
