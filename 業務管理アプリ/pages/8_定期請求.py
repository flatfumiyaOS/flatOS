"""定期請求ページ。毎月/毎年決まった日に、指定した見積書をもとに請求書を自動作成する
「定期請求」のルールを登録・一覧表示する。

Streamlitアプリには裏側で定期実行する仕組み（cron等）が無いため、実際の請求書生成は
このページか「会計・原価管理」ページを開いたタイミングで行う（billing_generator.
check_and_generate_due_recurring_billings）。多少開くのが遅れても、次に開いたときに
その時点までの未生成分に追いつく。
"""

from __future__ import annotations

import re

import streamlit as st

import auth_gate
import billing_generator
import google_auth
import recurring_billing_store
import sheets
from chat import show_chat_panel, show_chat_toggle
from db import get_all_customers, init_db
from layout import APP_ICON_PATH, show_header

st.set_page_config(page_title="定期請求", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

init_db()
google_auth.handle_login_redirect()


def _extract_spreadsheet_id(text: str) -> str:
    """GoogleスプレッドシートのURLが貼り付けられた場合はIDを取り出す。すでにIDのみの場合はそのまま返す。"""
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", text)
    return match.group(1) if match else text


show_header()

col_title, col_new = st.columns([4, 1])
with col_title:
    st.title("定期請求")
with col_new:
    st.write("")
    if st.button("＋ 新規登録", key="new_recurring_toggle_button", width="stretch"):
        st.session_state["show_new_recurring_form"] = True

if google_auth.is_logged_in():
    generated_messages = billing_generator.check_and_generate_due_recurring_billings(
        google_auth.get_credentials()
    )
    for msg in generated_messages:
        st.success(msg)
else:
    st.caption(
        "Googleアカウントでログインすると、請求日を迎えた定期請求の請求書が自動で作成されます"
        "（このページを開いたタイミングでチェックされます）。"
    )

if st.session_state.get("show_new_recurring_form"):
    with st.container(border=True):
        st.markdown("##### 新しい定期請求を登録する")

        customers = get_all_customers()
        if not customers:
            st.info("先に「顧客データベース」で顧客を登録してください。")
        else:
            customer_names = ["（選択してください）"] + [c["name"] for c in customers]
            customer_name = st.selectbox(
                "顧客", options=customer_names, key="new_recurring_customer"
            )
            base_url_input = st.text_input(
                "ベースの見積書URL",
                key="new_recurring_base_url",
                placeholder="https://docs.google.com/spreadsheets/d/.....",
                help="請求日ごとに、このスプレッドシートを複製して請求書を作成します。",
            )
            recurrence_type = st.radio(
                "定期",
                options=recurring_billing_store.RECURRENCE_OPTIONS,
                horizontal=True,
                key="new_recurring_type",
            )

            col_month, col_day = st.columns(2)
            with col_month:
                billing_month_input = st.selectbox(
                    "請求月（「年一」の場合のみ使用）",
                    options=list(range(1, 13)),
                    key="new_recurring_month",
                )
            with col_day:
                billing_day_input = st.number_input(
                    "請求日",
                    min_value=1,
                    max_value=31,
                    value=25,
                    step=1,
                    help="月末が無い日付（31日など）を指定した月は、その月の末日に読み替えます。",
                    key="new_recurring_day",
                )

            if st.button("登録する", key="create_recurring_button", type="primary"):
                if customer_name == "（選択してください）":
                    st.error("顧客を選択してください。")
                elif not base_url_input.strip():
                    st.error("ベースの見積書URLを入力してください。")
                else:
                    spreadsheet_id = _extract_spreadsheet_id(base_url_input.strip())
                    try:
                        sheet_names = sheets.list_sheet_names(spreadsheet_id)
                    except Exception as exc:
                        sheet_names = []
                        st.error(f"スプレッドシートを読み込めませんでした。URLをご確認ください。（詳細: {exc}）")
                    else:
                        if "御見積書" not in sheet_names or "御見積内訳書" not in sheet_names:
                            st.error(
                                "このスプレッドシートには「御見積書」「御見積内訳書」のシートが"
                                "見つかりませんでした。見積書ページで作成したスプレッドシートのURLを"
                                "指定してください。"
                            )
                        else:
                            selected_customer = next(
                                c for c in customers if c["name"] == customer_name
                            )
                            is_yearly = recurrence_type == recurring_billing_store.RECURRENCE_YEARLY
                            recurring_billing_store.add_recurring_billing(
                                selected_customer["id"],
                                selected_customer["name"],
                                spreadsheet_id,
                                recurrence_type,
                                int(billing_day_input),
                                int(billing_month_input) if is_yearly else None,
                            )
                            st.session_state["show_new_recurring_form"] = False
                            st.success(f"「{customer_name}」の定期請求を登録しました。")
                            st.rerun()

st.divider()

registrations = recurring_billing_store.get_all_recurring_billings()
if not registrations:
    st.info("まだ定期請求が登録されていません。")
else:
    for r in reversed(registrations):
        with st.container(border=True):
            col_info, col_action = st.columns([4, 1])
            with col_info:
                if r["recurrence_type"] == recurring_billing_store.RECURRENCE_YEARLY:
                    schedule_label = f"毎年{r['billing_month']}月{r['billing_day']}日"
                else:
                    schedule_label = f"毎月{r['billing_day']}日"
                st.write(f"**{r['customer_name']}** ／ {schedule_label} ／ {r['status']}")
                st.link_button(
                    "ベースの見積書を開く", sheets.spreadsheet_url(r["base_spreadsheet_id"])
                )
            with col_action:
                st.write("")
                if r["status"] == recurring_billing_store.STATUS_ACTIVE:
                    if st.button("停止する", key=f"pause_recurring_{r['id']}", width="stretch"):
                        recurring_billing_store.set_status(
                            r["id"], recurring_billing_store.STATUS_PAUSED
                        )
                        st.rerun()
                else:
                    if st.button("再開する", key=f"resume_recurring_{r['id']}", width="stretch"):
                        recurring_billing_store.set_status(
                            r["id"], recurring_billing_store.STATUS_ACTIVE
                        )
                        st.rerun()

show_chat_toggle()
show_chat_panel(category="請求書")
