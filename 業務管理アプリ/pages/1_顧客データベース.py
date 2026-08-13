"""顧客データベース画面。顧客の登録・検索・編集・削除ができます。"""

import pandas as pd
import streamlit as st

import auth_gate
from chat import show_chat_panel, show_chat_toggle
from db import (
    init_db,
    add_customer,
    update_customer,
    delete_customer,
    get_all_customers,
    search_customers,
)
from layout import show_header

st.set_page_config(page_title="顧客データベース", layout="wide")
auth_gate.require_password()

init_db()

show_header()
st.title("顧客データベース")

# --- 新規登録 ---
with st.expander("新しい顧客を登録する", expanded=False):
    with st.form("add_customer_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("氏名・会社名 *")
            kana = st.text_input("フリガナ")
            phone = st.text_input("電話番号")
        with col2:
            email = st.text_input("メールアドレス")
            address = st.text_input("住所")
        memo = st.text_area("備考")

        submitted = st.form_submit_button("登録する")
        if submitted:
            if not name.strip():
                st.error("氏名・会社名は必須です。")
            else:
                add_customer(name.strip(), kana.strip(), phone.strip(), email.strip(), address.strip(), memo.strip())
                st.success(f"「{name}」を登録しました。")
                st.rerun()

st.divider()

# --- 検索 ---
keyword = st.text_input("顧客を検索（氏名・フリガナ・電話番号・メール・住所）")
rows = search_customers(keyword) if keyword.strip() else get_all_customers()

st.write(f"登録件数: {len(rows)} 件")

if not rows:
    st.info("該当する顧客が見つかりません。")
else:
    df = pd.DataFrame(
        [
            {
                "ID": r["id"],
                "氏名・会社名": r["name"],
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

    id_to_label = {r["id"]: f'{r["id"]}: {r["name"]}' for r in rows}
    selected_id = st.selectbox(
        "編集・削除する顧客を選択してください",
        options=list(id_to_label.keys()),
        format_func=lambda x: id_to_label[x],
    )

    if selected_id is not None:
        customer = next(r for r in rows if r["id"] == selected_id)

        with st.form("edit_customer_form"):
            col1, col2 = st.columns(2)
            with col1:
                e_name = st.text_input("氏名・会社名 *", value=customer["name"])
                e_kana = st.text_input("フリガナ", value=customer["kana"] or "")
                e_phone = st.text_input("電話番号", value=customer["phone"] or "")
            with col2:
                e_email = st.text_input("メールアドレス", value=customer["email"] or "")
                e_address = st.text_input("住所", value=customer["address"] or "")
            e_memo = st.text_area("備考", value=customer["memo"] or "")

            col_save, col_delete = st.columns(2)
            with col_save:
                save = st.form_submit_button("更新する", width="stretch")
            with col_delete:
                delete = st.form_submit_button("削除する", width="stretch")

            if save:
                if not e_name.strip():
                    st.error("氏名・会社名は必須です。")
                else:
                    update_customer(
                        selected_id, e_name.strip(), e_kana.strip(), e_phone.strip(),
                        e_email.strip(), e_address.strip(), e_memo.strip(),
                    )
                    st.success("更新しました。")
                    st.rerun()

            if delete:
                st.session_state["pending_delete_id"] = selected_id

    # 削除確認（誤操作防止のため、確認ボタンを別途表示）
    if st.session_state.get("pending_delete_id") == selected_id and selected_id is not None:
        st.warning(f"「{customer['name']}」を本当に削除しますか？この操作は取り消せません。")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("はい、削除する", type="primary"):
                delete_customer(selected_id)
                del st.session_state["pending_delete_id"]
                st.success("削除しました。")
                st.rerun()
        with col_no:
            if st.button("キャンセル"):
                del st.session_state["pending_delete_id"]
                st.rerun()

# チャットのトグル・パネルは、ページ固有のウィジェット（顧客の選択など）をすべて
# 生成し終えたあとに呼び出す。先に呼び出すと、チャットの開閉ボタンが押されたときの
# st.rerun()がそれらのウィジェットに到達する前にスクリプトを打ち切ってしまい、
# 選択状態がsession_stateから失われてしまう不具合があったため。
show_chat_toggle()
show_chat_panel(category="案件管理")
