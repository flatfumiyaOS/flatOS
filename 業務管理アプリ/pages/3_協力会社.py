"""協力会社データベース画面。協力会社（外注業者）の登録・検索・編集・削除ができます。

顧客データベースとは別に管理する。顧客は見積書を送る相手、協力会社は請求書を
受け取る相手で、請求の向きが逆であるため、混在させると見積書作成時の顧客選択
リストなどに支障が出るため。

担当者は顧客データベースと違い、独立ページではなくこのフォーム内で複数登録する
（contact_fields.render_contact_rows）。
"""

import pandas as pd
import streamlit as st

import auth_gate
import contact_fields
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
from postal import lookup_address_from_postal_code

st.set_page_config(page_title="協力会社", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

init_db()

HONORIFIC_OPTIONS = ["様", "御中"]
RATING_OPTIONS = ["未評価", "1", "2", "3", "4", "5"]


def _postal_lookup_pending_key(key_prefix: str) -> str:
    return f"_{key_prefix}_postal_lookup_pending"


def _apply_pending_postal_lookup(key_prefix: str) -> None:
    """「郵便番号から住所を検索」ボタンで見つかった住所を、住所欄のウィジェットが
    作られるより前にセットする。
    """
    found = st.session_state.pop(_postal_lookup_pending_key(key_prefix), None)
    if found is not None:
        st.session_state[f"{key_prefix}_address"] = found


def _rating_index(value) -> int:
    return RATING_OPTIONS.index(str(value)) if value and str(value) in RATING_OPTIONS else 0


def _render_vendor_fields(key_prefix: str, vendor=None):
    """協力会社の入力欄一式を描画する。st.formは使わない（ご担当者の追加・削除・
    郵便番号検索ボタンをフォーム内に置くと、他のボタン操作でも巻き込まれて値が
    クリアされてしまうため）。

    戻り値: (name, kana, honorific, postal_code, address, phone, fax, email, referrer,
             contacts, quality, service, communication, it_literacy, memo)
    """
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input(
            "会社名 *", value=(vendor["name"] if vendor else ""), key=f"{key_prefix}_name"
        )
        kana = st.text_input(
            "フリガナ", value=(vendor["kana"] or "" if vendor else ""), key=f"{key_prefix}_kana"
        )
        honorific_default = vendor["honorific"] if vendor else HONORIFIC_OPTIONS[0]
        honorific_index = HONORIFIC_OPTIONS.index(honorific_default) if honorific_default in HONORIFIC_OPTIONS else 0
        honorific = st.selectbox(
            "敬称 * （個人なら「様」、法人なら「御中」）",
            options=HONORIFIC_OPTIONS,
            index=honorific_index,
            key=f"{key_prefix}_honorific",
        )
        postal_code = st.text_input(
            "郵便番号", value=(vendor["postal_code"] or "" if vendor else ""), key=f"{key_prefix}_postal"
        )
        if st.button("郵便番号から住所を検索", key=f"{key_prefix}_postal_search"):
            found = lookup_address_from_postal_code(postal_code)
            if found:
                st.session_state[_postal_lookup_pending_key(key_prefix)] = found
                st.rerun()
            else:
                st.warning("該当する住所が見つかりませんでした。郵便番号をご確認ください。")
        address = st.text_input(
            "住所", value=(vendor["address"] or "" if vendor else ""), key=f"{key_prefix}_address"
        )
    with col2:
        phone = st.text_input(
            "TEL", value=(vendor["phone"] or "" if vendor else ""), key=f"{key_prefix}_phone"
        )
        fax = st.text_input("FAX", value=(vendor["fax"] or "" if vendor else ""), key=f"{key_prefix}_fax")
        email = st.text_input(
            "MAIL", value=(vendor["email"] or "" if vendor else ""), key=f"{key_prefix}_email"
        )
        referrer = st.text_input(
            "紹介者", value=(vendor["referrer"] or "" if vendor else ""), key=f"{key_prefix}_referrer"
        )

    existing_contacts = contact_fields.contacts_from_json(vendor["contacts_json"]) if vendor else []
    contact_fields.init_contact_count(key_prefix, len(existing_contacts) or 1)
    contacts = contact_fields.render_contact_rows(key_prefix, contacts_value=existing_contacts)

    st.markdown("**評価**（5段階。まだ評価できない場合は「未評価」のままでよい）")
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        quality = st.selectbox(
            "施工品質", options=RATING_OPTIONS,
            index=_rating_index(vendor["quality_rating"] if vendor else None), key=f"{key_prefix}_quality",
        )
    with r2:
        service = st.selectbox(
            "接客態度", options=RATING_OPTIONS,
            index=_rating_index(vendor["service_rating"] if vendor else None), key=f"{key_prefix}_service",
        )
    with r3:
        communication = st.selectbox(
            "コミュニケーション", options=RATING_OPTIONS,
            index=_rating_index(vendor["communication_rating"] if vendor else None), key=f"{key_prefix}_communication",
        )
    with r4:
        it_literacy = st.selectbox(
            "ITリテラシー", options=RATING_OPTIONS,
            index=_rating_index(vendor["it_literacy_rating"] if vendor else None), key=f"{key_prefix}_it_literacy",
        )

    memo = st.text_area(
        "工種・備考", value=(vendor["memo"] or "" if vendor else ""), key=f"{key_prefix}_memo"
    )

    return (
        name, kana, honorific, postal_code, address, phone, fax, email, referrer,
        contacts, quality, service, communication, it_literacy, memo,
    )


def _rating_value(selection: str) -> str:
    return "" if selection == "未評価" else selection


def _reset_add_vendor_fields() -> None:
    """新規登録欄の入力値を、次回の登録に備えて空にする。

    session_stateのキーをpop（削除）するだけだと、ブラウザ側の表示がリセットされずに
    前回入力した文字列が残ってしまう（Streamlitの既知の挙動）。既定値を明示的に
    書き込むことで、ウィジェットの表示も確実にクリアされる。
    """
    for suffix in ("name", "kana", "postal", "address", "phone", "fax", "email", "referrer", "memo"):
        st.session_state[f"add_vendor_{suffix}"] = ""
    st.session_state["add_vendor_honorific"] = HONORIFIC_OPTIONS[0]
    for suffix in ("quality", "service", "communication", "it_literacy"):
        st.session_state[f"add_vendor_{suffix}"] = RATING_OPTIONS[0]
    contact_fields.clear_contact_rows("add_vendor")


show_header()
st.title("協力会社")

# --- 新規登録 ---
if st.session_state.pop("_add_vendor_reset", False):
    _reset_add_vendor_fields()
_apply_pending_postal_lookup("add_vendor")
contact_fields.apply_pending_removal("add_vendor")

with st.expander("新しい協力会社を登録する", expanded=False):
    (
        name, kana, honorific, postal_code, address, phone, fax, email, referrer,
        contacts, quality, service, communication, it_literacy, memo,
    ) = _render_vendor_fields("add_vendor")

    if st.button("登録する", key="add_vendor_submit"):
        if not name.strip():
            st.error("会社名は必須です。")
        else:
            add_vendor(
                name.strip(), kana.strip(), phone.strip(), email.strip(), address.strip(), memo.strip(),
                honorific=honorific, fax=fax.strip(), postal_code=postal_code.strip(), referrer=referrer.strip(),
                quality_rating=_rating_value(quality), service_rating=_rating_value(service),
                communication_rating=_rating_value(communication), it_literacy_rating=_rating_value(it_literacy),
                contacts=contact_fields.clean_contacts(contacts),
            )
            st.session_state["_add_vendor_reset"] = True
            st.success(f"「{name}」を登録しました。")
            st.rerun()

st.divider()

# --- 検索 ---
keyword = st.text_input("協力会社を検索（会社名・フリガナ・電話番号・メール・住所など）")
rows = search_vendors(keyword) if keyword.strip() else get_all_vendors()
rows = sorted(rows, key=lambda r: r["kana"] or r["name"])

st.write(f"登録件数: {len(rows)} 件")

if not rows:
    st.info("該当する協力会社が見つかりません。")
else:
    df = pd.DataFrame(
        [
            {
                "会社名": r["name"],
                "フリガナ": r["kana"],
                "敬称": r["honorific"],
                "TEL": r["phone"],
                "MAIL": r["email"],
                "住所": r["address"],
                "施工品質": r["quality_rating"],
                "接客態度": r["service_rating"],
                "コミュニケーション": r["communication_rating"],
                "ITリテラシー": r["it_literacy_rating"],
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
        edit_key_prefix = f"edit_vendor_{selected_id}"
        _apply_pending_postal_lookup(edit_key_prefix)
        contact_fields.apply_pending_removal(edit_key_prefix)

        (
            e_name, e_kana, e_honorific, e_postal_code, e_address, e_phone, e_fax, e_email, e_referrer,
            e_contacts, e_quality, e_service, e_communication, e_it_literacy, e_memo,
        ) = _render_vendor_fields(edit_key_prefix, vendor=vendor)

        col_save, col_delete = st.columns(2)
        with col_save:
            save = st.button("更新する", key=f"{edit_key_prefix}_save", width="stretch")
        with col_delete:
            delete = st.button("削除する", key=f"{edit_key_prefix}_delete", width="stretch")

        if save:
            if not e_name.strip():
                st.error("会社名は必須です。")
            else:
                update_vendor(
                    selected_id, e_name.strip(), e_kana.strip(), e_phone.strip(), e_email.strip(),
                    e_address.strip(), e_memo.strip(),
                    honorific=e_honorific, fax=e_fax.strip(), postal_code=e_postal_code.strip(),
                    referrer=e_referrer.strip(),
                    quality_rating=_rating_value(e_quality), service_rating=_rating_value(e_service),
                    communication_rating=_rating_value(e_communication), it_literacy_rating=_rating_value(e_it_literacy),
                    contacts=contact_fields.clean_contacts(e_contacts),
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
