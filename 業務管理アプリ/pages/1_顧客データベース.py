"""顧客データベース画面。顧客の登録・検索・編集・削除ができます。

担当者は「顧客担当者」ページ（別ファイル）で顧客ごとに複数登録する。
"""

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
from layout import APP_ICON_PATH, show_header
from postal import lookup_address_from_postal_code

st.set_page_config(page_title="顧客データベース", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

init_db()

HONORIFIC_OPTIONS = ["様", "御中"]


def _postal_lookup_pending_key(key_prefix: str) -> str:
    return f"_{key_prefix}_postal_lookup_pending"


def _apply_pending_postal_lookup(key_prefix: str) -> None:
    """「郵便番号から住所を検索」ボタンで見つかった住所を、住所欄のウィジェットが
    作られるより前にセットする（削除ボタンの詰め直しと同じ、ウィジェット生成前に
    反映するパターン）。
    """
    found = st.session_state.pop(_postal_lookup_pending_key(key_prefix), None)
    if found is not None:
        st.session_state[f"{key_prefix}_address"] = found


def _render_customer_fields(key_prefix: str, customer=None):
    """顧客の入力欄一式を描画する。st.formは使わない（郵便番号検索ボタンを
    フォーム内に置くと、他のボタン操作でも巻き込まれて値がクリアされてしまうため）。

    戻り値: (name, kana, honorific, postal_code, address, phone, fax, email, referrer, memo)
    """
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input(
            "顧客名 *", value=(customer["name"] if customer else ""), key=f"{key_prefix}_name"
        )
        kana = st.text_input(
            "フリガナ", value=(customer["kana"] or "" if customer else ""), key=f"{key_prefix}_kana"
        )
        honorific_default = customer["honorific"] if customer else HONORIFIC_OPTIONS[0]
        honorific_index = HONORIFIC_OPTIONS.index(honorific_default) if honorific_default in HONORIFIC_OPTIONS else 0
        honorific = st.selectbox(
            "敬称 * （個人なら「様」、法人なら「御中」）",
            options=HONORIFIC_OPTIONS,
            index=honorific_index,
            key=f"{key_prefix}_honorific",
        )
        postal_code = st.text_input(
            "郵便番号", value=(customer["postal_code"] or "" if customer else ""), key=f"{key_prefix}_postal"
        )
        if st.button("郵便番号から住所を検索", key=f"{key_prefix}_postal_search"):
            found = lookup_address_from_postal_code(postal_code)
            if found:
                st.session_state[_postal_lookup_pending_key(key_prefix)] = found
                st.rerun()
            else:
                st.warning("該当する住所が見つかりませんでした。郵便番号をご確認ください。")
        address = st.text_input(
            "住所", value=(customer["address"] or "" if customer else ""), key=f"{key_prefix}_address"
        )
    with col2:
        phone = st.text_input(
            "TEL", value=(customer["phone"] or "" if customer else ""), key=f"{key_prefix}_phone"
        )
        fax = st.text_input("FAX", value=(customer["fax"] or "" if customer else ""), key=f"{key_prefix}_fax")
        email = st.text_input(
            "MAIL", value=(customer["email"] or "" if customer else ""), key=f"{key_prefix}_email"
        )
        referrer = st.text_input(
            "紹介者", value=(customer["referrer"] or "" if customer else ""), key=f"{key_prefix}_referrer"
        )
    memo = st.text_area("備考", value=(customer["memo"] or "" if customer else ""), key=f"{key_prefix}_memo")

    return name, kana, honorific, postal_code, address, phone, fax, email, referrer, memo


def _reset_add_customer_fields() -> None:
    for suffix in ("name", "kana", "honorific", "postal", "address", "phone", "fax", "email", "referrer", "memo"):
        st.session_state.pop(f"add_customer_{suffix}", None)


show_header()
st.title("顧客データベース")

# --- 新規登録 ---
if st.session_state.pop("_add_customer_reset", False):
    _reset_add_customer_fields()
_apply_pending_postal_lookup("add_customer")

with st.expander("新しい顧客を登録する", expanded=False):
    name, kana, honorific, postal_code, address, phone, fax, email, referrer, memo = _render_customer_fields(
        "add_customer"
    )

    if st.button("登録する", key="add_customer_submit"):
        if not name.strip():
            st.error("顧客名は必須です。")
        else:
            add_customer(
                name.strip(), kana.strip(), honorific, phone.strip(), fax.strip(),
                email.strip(), postal_code.strip(), address.strip(), referrer.strip(), memo.strip(),
            )
            st.session_state["_add_customer_reset"] = True
            st.success(f"「{name}」を登録しました。")
            st.rerun()

st.divider()

# --- 検索 ---
keyword = st.text_input("顧客を検索（顧客名・フリガナ・電話番号・メール・住所など）")
rows = search_customers(keyword) if keyword.strip() else get_all_customers()
rows = sorted(rows, key=lambda r: r["kana"] or r["name"])

st.write(f"登録件数: {len(rows)} 件")

if not rows:
    st.info("該当する顧客が見つかりません。")
else:
    df = pd.DataFrame(
        [
            {
                "顧客名": r["name"],
                "フリガナ": r["kana"],
                "敬称": r["honorific"],
                "TEL": r["phone"],
                "MAIL": r["email"],
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
        "編集・削除する顧客を選択してください",
        options=list(id_to_label.keys()),
        format_func=lambda x: id_to_label[x],
    )

    if selected_id is not None:
        customer = next(r for r in rows if r["id"] == selected_id)
        edit_key_prefix = f"edit_customer_{selected_id}"
        _apply_pending_postal_lookup(edit_key_prefix)

        (
            e_name, e_kana, e_honorific, e_postal_code, e_address,
            e_phone, e_fax, e_email, e_referrer, e_memo,
        ) = _render_customer_fields(edit_key_prefix, customer=customer)

        col_save, col_delete = st.columns(2)
        with col_save:
            save = st.button("更新する", key=f"{edit_key_prefix}_save", width="stretch")
        with col_delete:
            delete = st.button("削除する", key=f"{edit_key_prefix}_delete", width="stretch")

        if save:
            if not e_name.strip():
                st.error("顧客名は必須です。")
            else:
                update_customer(
                    selected_id, e_name.strip(), e_kana.strip(), e_honorific, e_phone.strip(), e_fax.strip(),
                    e_email.strip(), e_postal_code.strip(), e_address.strip(), e_referrer.strip(), e_memo.strip(),
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
