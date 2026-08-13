"""業務管理アプリのトップページ。"""

import streamlit as st

import auth_gate
import google_auth
from chat import show_chat_panel, show_chat_toggle
from db import init_db
from layout import APP_ICON_PATH, show_header

st.set_page_config(page_title="業務管理アプリ", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

# Googleログインのリダイレクトは、この画面（アプリのトップURL）に戻ってくるため、
# どのページを開いていてもここで認可コードを処理できるようにする。
# init_db()より先に呼ぶことで、ログイン直後の同じ実行の中でcustomers.dbの
# Googleドライブからの復元判定が正しくログイン済み状態で行われるようにする。
google_auth.handle_login_redirect()

init_db()

show_header()
show_chat_toggle()
st.title("業務管理アプリ")
st.write("リフォーム会社の現場監督・施工管理業務を支援するアプリです。")

if google_auth.is_logged_in():
    st.caption("Googleアカウントにログイン済みです。")
else:
    st.link_button("Googleでログイン", google_auth.get_login_url())
    st.caption("見積書・工程表などでスプレッドシートを新規作成するには、Googleアカウントでログインしてください。")

show_chat_panel(category="案件管理")
