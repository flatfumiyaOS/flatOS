"""顧客担当者データベース画面。顧客データベースとは別に、顧客ごとの担当者を
複数登録・検索・編集・削除できます。
"""

import pandas as pd
import streamlit as st

import auth_gate
from chat import show_chat_panel, show_chat_toggle
from db import (
    init_db,
    add_customer_contact,
    update_customer_contact,
    delete_customer_contact,
    get_all_customers,
    get_all_customer_contacts,
    search_customer_contacts,
)
from layout import APP_ICON_PATH, show_header

st.set_page_config(page_title="顧客担当者", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

init_db()

HONORIFIC_OPTIONS = ["様", "御中"]

show_header()
st.title("顧客担当者")
st.caption("顧客データベースに登録済みの顧客ごとに、担当者を複数登録できます。")

customers = get_all_customers()

# --- 新規登録 ---
with st.expander("新しい担当者を登録する", expanded=False):
    if not customers:
        st.info("先に「顧客データベース」で顧客を登録してください。")
    else:
        with st.form("add_customer_contact_form", clear_on_submit=True):
            customer_id = st.selectbox(
                "顧客 *",
                options=[c["id"] for c in customers],
                format_func=lambda x: next(c["name"] for c in customers if c["id"] == x),
            )
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("担当者名 *")
                kana = st.text_input("フリガナ")
                honorific = st.selectbox("敬称 *", options=HONORIFIC_OPTIONS)
            with col2:
                title = st.text_input("肩書")
                email = st.text_input("MAIL")
            memo = st.text_area("備考")

            submitted = st.form_submit_button("登録する")
            if submitted:
                if not name.strip():
                    st.error("担当者名は必須です。")
                else:
                    customer_name = next(c["name"] for c in customers if c["id"] == customer_id)
                    add_customer_contact(
                        customer_id, customer_name, name.strip(), kana.strip(),
                        honorific, title.strip(), email.strip(), memo.strip(),
                    )
                    st.success(f"「{name}」を登録しました。")
                    st.rerun()

st.divider()

# --- 検索 ---
keyword = st.text_input("担当者を検索（担当者名・フリガナ・肩書・メール・顧客名）")
rows = search_customer_contacts(keyword) if keyword.strip() else get_all_customer_contacts()
rows = sorted(rows, key=lambda r: r["kana"] or r["name"])

st.write(f"登録件数: {len(rows)} 件")

if not rows:
    st.info("該当する担当者が見つかりません。")
else:
    df = pd.DataFrame(
        [
            {
                "顧客名": r["customer_name"],
                "担当者名": r["name"],
                "フリガナ": r["kana"],
                "敬称": r["honorific"],
                "肩書": r["title"],
                "MAIL": r["email"],
                "備考": r["memo"],
                "更新日": r["updated_at"],
            }
            for r in rows
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True)

    st.divider()
    st.subheader("編集・削除")

    id_to_label = {r["id"]: f"{r['customer_name']} / {r['name']}" for r in rows}
    selected_id = st.selectbox(
        "編集・削除する担当者を選択してください",
        options=list(id_to_label.keys()),
        format_func=lambda x: id_to_label[x],
    )

    if selected_id is not None:
        contact = next(r for r in rows if r["id"] == selected_id)

        with st.form("edit_customer_contact_form"):
            customer_ids = [c["id"] for c in customers]
            current_customer_id = contact["customer_id"]
            customer_index = (
                customer_ids.index(current_customer_id) if current_customer_id in customer_ids else 0
            )
            e_customer_id = st.selectbox(
                "顧客 *",
                options=customer_ids,
                index=customer_index,
                format_func=lambda x: next(c["name"] for c in customers if c["id"] == x),
            )
            col1, col2 = st.columns(2)
            with col1:
                e_name = st.text_input("担当者名 *", value=contact["name"])
                e_kana = st.text_input("フリガナ", value=contact["kana"] or "")
                e_honorific_index = (
                    HONORIFIC_OPTIONS.index(contact["honorific"]) if contact["honorific"] in HONORIFIC_OPTIONS else 0
                )
                e_honorific = st.selectbox("敬称 *", options=HONORIFIC_OPTIONS, index=e_honorific_index)
            with col2:
                e_title = st.text_input("肩書", value=contact["title"] or "")
                e_email = st.text_input("MAIL", value=contact["email"] or "")
            e_memo = st.text_area("備考", value=contact["memo"] or "")

            col_save, col_delete = st.columns(2)
            with col_save:
                save = st.form_submit_button("更新する", width="stretch")
            with col_delete:
                delete = st.form_submit_button("削除する", width="stretch")

            if save:
                if not e_name.strip():
                    st.error("担当者名は必須です。")
                else:
                    e_customer_name = next(c["name"] for c in customers if c["id"] == e_customer_id)
                    update_customer_contact(
                        selected_id, e_customer_id, e_customer_name, e_name.strip(), e_kana.strip(),
                        e_honorific, e_title.strip(), e_email.strip(), e_memo.strip(),
                    )
                    st.success("更新しました。")
                    st.rerun()

            if delete:
                st.session_state["pending_delete_contact_id"] = selected_id

    # 削除確認（誤操作防止のため、確認ボタンを別途表示）
    if st.session_state.get("pending_delete_contact_id") == selected_id and selected_id is not None:
        st.warning(f"「{contact['name']}」を本当に削除しますか？この操作は取り消せません。")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("はい、削除する", type="primary"):
                delete_customer_contact(selected_id)
                del st.session_state["pending_delete_contact_id"]
                st.success("削除しました。")
                st.rerun()
        with col_no:
            if st.button("キャンセル"):
                del st.session_state["pending_delete_contact_id"]
                st.rerun()

show_chat_toggle()
show_chat_panel(category="案件管理")
