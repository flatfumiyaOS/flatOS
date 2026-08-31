"""物件データベース画面。顧客データベースに登録済みの顧客に紐づけて、
物件（マンション・戸建てなど）を複数登録・検索・編集・削除できます。
"""

import pandas as pd
import streamlit as st

import auth_gate
import google_auth
import property_store
from chat import show_chat_panel, show_chat_toggle
from db import get_all_customers, get_customer, init_db
from layout import APP_ICON_PATH, show_header

st.set_page_config(page_title="物件", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

init_db()

show_header()
st.title("物件")
st.caption("顧客データベースに登録済みの顧客に紐づけて、物件を複数登録できます。")

customers = get_all_customers()


def _effective_address(prop: dict) -> str:
    """一覧表示用に、物件の住所（「顧客情報の住所と同じ」ならその顧客の現在の住所）を返す。"""
    if prop["address_type"] == property_store.ADDRESS_TYPE_SAME_AS_CUSTOMER:
        customer = get_customer(prop["customer_id"])
        return (customer["address"] or "") if customer else ""
    return prop["address"] or ""


# --- 新規登録 ---
with st.expander("新しい物件を登録する", expanded=False):
    if not customers:
        st.info("先に「顧客データベース」で顧客を登録してください。")
    else:
        with st.form("add_property_form", clear_on_submit=True):
            customer_id = st.selectbox(
                "顧客 *",
                options=[c["id"] for c in customers],
                format_func=lambda x: next(c["name"] for c in customers if c["id"] == x),
            )
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("物件名 *")
                kana = st.text_input("フリガナ")
                property_type = st.selectbox("物件種別 *", options=property_store.PROPERTY_TYPE_OPTIONS)
            with col2:
                address_type = st.selectbox("物件住所種別 *", options=property_store.ADDRESS_TYPE_OPTIONS)
                address = st.text_input(
                    "物件住所（「新しい住所を入力」を選んだ場合のみ使用されます）"
                )
            image_file = st.file_uploader("外観画像", type=["jpg", "jpeg", "png"])
            memo = st.text_area("備考")

            submitted = st.form_submit_button("登録する")
            if submitted:
                if not name.strip():
                    st.error("物件名は必須です。")
                else:
                    customer_name = next(c["name"] for c in customers if c["id"] == customer_id)
                    new_property = property_store.add_property(
                        customer_id, customer_name, name.strip(), kana.strip(),
                        property_type, address_type, address.strip(), memo.strip(),
                    )
                    if image_file is not None:
                        property_store.set_property_image(
                            new_property["id"], image_file.name, image_file.getvalue()
                        )
                    st.success(f"「{name}」を登録しました。")
                    st.rerun()

st.divider()

# --- 検索 ---
keyword = st.text_input("物件を検索（物件名・フリガナ・顧客名・住所・備考）")
all_properties = property_store.get_all_properties()
if keyword.strip():
    like = keyword.strip()
    rows = [
        p for p in all_properties
        if like in (p["name"] or "") or like in (p["kana"] or "") or like in (p["customer_name"] or "")
        or like in (p["address"] or "") or like in (p["memo"] or "")
    ]
else:
    rows = all_properties
rows = sorted(rows, key=lambda r: r["kana"] or r["name"])

st.write(f"登録件数: {len(rows)} 件")

if not rows:
    st.info("該当する物件が見つかりません。")
else:
    df = pd.DataFrame(
        [
            {
                "顧客名": r["customer_name"],
                "物件名": r["name"],
                "フリガナ": r["kana"],
                "種別": r["property_type"],
                "住所": _effective_address(r),
                "画像": "あり" if r.get("image") else "なし",
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
        "編集・削除する物件を選択してください",
        options=list(id_to_label.keys()),
        format_func=lambda x: id_to_label[x],
    )

    if selected_id is not None:
        prop = next(r for r in rows if r["id"] == selected_id)

        if prop.get("image"):
            image_bytes = property_store.get_property_image_bytes(selected_id)
            if image_bytes:
                st.image(image_bytes, caption="現在の外観画像", width=240)

        with st.form("edit_property_form"):
            customer_ids = [c["id"] for c in customers]
            current_customer_id = prop["customer_id"]
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
                e_name = st.text_input("物件名 *", value=prop["name"])
                e_kana = st.text_input("フリガナ", value=prop["kana"] or "")
                e_type_index = (
                    property_store.PROPERTY_TYPE_OPTIONS.index(prop["property_type"])
                    if prop["property_type"] in property_store.PROPERTY_TYPE_OPTIONS else 0
                )
                e_property_type = st.selectbox(
                    "物件種別 *", options=property_store.PROPERTY_TYPE_OPTIONS, index=e_type_index
                )
            with col2:
                e_address_type_index = (
                    property_store.ADDRESS_TYPE_OPTIONS.index(prop["address_type"])
                    if prop["address_type"] in property_store.ADDRESS_TYPE_OPTIONS else 0
                )
                e_address_type = st.selectbox(
                    "物件住所種別 *", options=property_store.ADDRESS_TYPE_OPTIONS, index=e_address_type_index
                )
                e_address = st.text_input(
                    "物件住所（「新しい住所を入力」を選んだ場合のみ使用されます）",
                    value=prop["address"] or "",
                )
            e_image_file = st.file_uploader("外観画像を差し替える", type=["jpg", "jpeg", "png"])
            e_memo = st.text_area("備考", value=prop["memo"] or "")

            col_save, col_delete = st.columns(2)
            with col_save:
                save = st.form_submit_button("更新する", width="stretch")
            with col_delete:
                delete = st.form_submit_button("削除する", width="stretch")

            if save:
                if not e_name.strip():
                    st.error("物件名は必須です。")
                else:
                    e_customer_name = next(c["name"] for c in customers if c["id"] == e_customer_id)
                    property_store.update_property(
                        selected_id, e_customer_id, e_customer_name, e_name.strip(), e_kana.strip(),
                        e_property_type, e_address_type, e_address.strip(), e_memo.strip(),
                    )
                    if e_image_file is not None:
                        property_store.set_property_image(
                            selected_id, e_image_file.name, e_image_file.getvalue()
                        )
                    st.success("更新しました。")
                    st.rerun()

            if delete:
                st.session_state["pending_delete_property_id"] = selected_id

    # 削除確認（誤操作防止のため、確認ボタンを別途表示）
    if st.session_state.get("pending_delete_property_id") == selected_id and selected_id is not None:
        st.warning(f"「{prop['name']}」を本当に削除しますか？この操作は取り消せません。")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("はい、削除する", type="primary"):
                credentials = google_auth.get_credentials() if google_auth.is_logged_in() else None
                property_store.delete_property(selected_id, credentials)
                del st.session_state["pending_delete_property_id"]
                st.success("削除しました。")
                st.rerun()
        with col_no:
            if st.button("キャンセル"):
                del st.session_state["pending_delete_property_id"]
                st.rerun()

show_chat_toggle()
show_chat_panel(category="案件管理")
