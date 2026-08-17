"""協力会社データベース画面。協力会社（外注業者）の登録・検索・編集・削除ができます。

顧客データベースとは別に管理する。顧客は見積書を送る相手、協力会社は請求書を
受け取る相手で、請求の向きが逆であるため、混在させると見積書作成時の顧客選択
リストなどに支障が出るため。
"""

import pandas as pd
import streamlit as st

import auth_gate
from chat import show_chat_panel, show_chat_toggle
from db import (
    init_db,
    add_vendor,
    update_vendor,
    delete_vendor,
    get_all_vendors,
    search_vendors,
)
from layout import APP_ICON_PATH, show_header

st.set_page_config(page_title="協力会社", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

init_db()

show_header()
st.title("協力会社")

# --- 新規登録 ---
with st.expander("新しい協力会社を登録する", expanded=False):
    with st.form("add_vendor_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("業者名 *")
            kana = st.text_input("フリガナ")
            phone = st.text_input("電話番号")
        with col2:
            email = st.text_input("メールアドレス")
            address = st.text_input("住所")
        memo = st.text_area("備考（担当工種など）")

        submitted = st.form_submit_button("登録する")
        if submitted:
            if not name.strip():
                st.error("業者名は必須です。")
            else:
                add_vendor(name.strip(), kana.strip(), phone.strip(), email.strip(), address.strip(), memo.strip())
                st.success(f"「{name}」を登録しました。")
                st.rerun()

st.divider()

# --- 検索 ---
keyword = st.text_input("協力会社を検索（業者名・フリガナ・電話番号・メール・住所）")
rows = search_vendors(keyword) if keyword.strip() else get_all_vendors()
rows = sorted(rows, key=lambda r: r["kana"] or r["name"])

st.write(f"登録件数: {len(rows)} 件")

if not rows:
    st.info("該当する協力会社が見つかりません。")
else:
    df = pd.DataFrame(
        [
            {
                "業者名": r["name"],
                "フリガナ": r["kana"],
                "電話番号": r["phone"],
                "メール": r["email"],
                "住所": r["address"],
                "更新日": r["updated_at"],
            }
            for r in rows
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True)

    st.divider()
    st.subheader("編集・削除")

    id_to_label = {r["id"]: r["name"] for r in rows}
    selected_id = st.selectbox(
        "編集・削除する協力会社を選択してください",
        options=list(id_to_label.keys()),
        format_func=lambda x: id_to_label[x],
    )

    if selected_id is not None:
        vendor = next(r for r in rows if r["id"] == selected_id)

        with st.form("edit_vendor_form"):
            col1, col2 = st.columns(2)
            with col1:
                e_name = st.text_input("業者名 *", value=vendor["name"])
                e_kana = st.text_input("フリガナ", value=vendor["kana"] or "")
                e_phone = st.text_input("電話番号", value=vendor["phone"] or "")
            with col2:
                e_email = st.text_input("メールアドレス", value=vendor["email"] or "")
                e_address = st.text_input("住所", value=vendor["address"] or "")
            e_memo = st.text_area("備考（担当工種など）", value=vendor["memo"] or "")

            col_save, col_delete = st.columns(2)
            with col_save:
                save = st.form_submit_button("更新する", width="stretch")
            with col_delete:
                delete = st.form_submit_button("削除する", width="stretch")

            if save:
                if not e_name.strip():
                    st.error("業者名は必須です。")
                else:
                    update_vendor(
                        selected_id, e_name.strip(), e_kana.strip(), e_phone.strip(),
                        e_email.strip(), e_address.strip(), e_memo.strip(),
                    )
                    st.success("更新しました。")
                    st.rerun()

            if delete:
                st.session_state["pending_delete_vendor_id"] = selected_id

    # 削除確認(誤操作防止のため、確認ボタンを別途表示)
    if st.session_state.get("pending_delete_vendor_id") == selected_id and selected_id is not None:
        st.warning(f"「{vendor['name']}」を本当に削除しますか？この操作は取り消せません。")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("はい、削除する", type="primary"):
                delete_vendor(selected_id)
                del st.session_state["pending_delete_vendor_id"]
                st.success("削除しました。")
                st.rerun()
        with col_no:
            if st.button("キャンセル"):
                del st.session_state["pending_delete_vendor_id"]
                st.rerun()

show_chat_toggle()
show_chat_panel(category="案件管理")
