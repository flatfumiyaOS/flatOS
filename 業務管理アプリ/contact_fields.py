"""顧客データベース・協力会社ページで共通して使う、法人/個人の入力欄を描画するヘルパー。

法人の場合は「ご担当者」欄（氏名・電話番号・メールアドレスのセット）を
プラスボタンで複数登録できるようにする。個人の場合は今まで通りの単一欄。
"""

from __future__ import annotations

import json

import streamlit as st


def _count_key(key_prefix: str) -> str:
    return f"{key_prefix}_contact_count"


def init_contact_count(key_prefix: str, initial_count: int) -> None:
    """担当者行の数をsession_stateで管理する。フォームの外から、フォーム描画前に呼ぶこと。"""
    count_key = _count_key(key_prefix)
    if count_key not in st.session_state:
        st.session_state[count_key] = max(1, initial_count)


def reset_contact_count(key_prefix: str) -> None:
    st.session_state[_count_key(key_prefix)] = 1


def render_add_contact_button(key_prefix: str) -> None:
    """「＋ご担当者を追加」ボタン。st.formの外に置くこと(フォーム内はst.form_submit_buttonのみ使用可のため)。"""
    if st.button("＋ ご担当者を追加", key=f"{key_prefix}_add_contact_btn"):
        st.session_state[_count_key(key_prefix)] += 1


def contacts_from_json(contacts_json: str | None) -> list[dict]:
    if not contacts_json:
        return []
    try:
        return json.loads(contacts_json)
    except (json.JSONDecodeError, TypeError):
        return []


def render_corporate_fields(
    key_prefix: str,
    name_label: str,
    memo_label: str = "備考",
    name_value: str = "",
    kana_value: str = "",
    address_value: str = "",
    memo_value: str = "",
    contacts_value: list[dict] | None = None,
):
    """法人用の入力欄一式をst.form内に描画する。呼び出し前にinit_contact_countが必要。

    戻り値: (name, kana, address, contacts, memo)。contactsは
    {"name":..., "phone":..., "email":...} のリスト（未入力の行は呼び出し側で除外する想定）。
    """
    contacts_value = contacts_value or []

    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input(name_label, value=name_value, key=f"{key_prefix}_name")
        kana = st.text_input("フリガナ", value=kana_value, key=f"{key_prefix}_kana")
    with col2:
        address = st.text_input("住所", value=address_value, key=f"{key_prefix}_address")

    st.markdown("**ご担当者**")
    count = st.session_state[_count_key(key_prefix)]
    contacts = []
    for i in range(count):
        prefilled = contacts_value[i] if i < len(contacts_value) else {}
        c1, c2, c3 = st.columns(3)
        with c1:
            c_name = st.text_input(
                f"担当者名 {i + 1}", value=prefilled.get("name", ""), key=f"{key_prefix}_contact_name_{i}"
            )
        with c2:
            c_phone = st.text_input(
                f"電話番号 {i + 1}", value=prefilled.get("phone", ""), key=f"{key_prefix}_contact_phone_{i}"
            )
        with c3:
            c_email = st.text_input(
                f"メールアドレス {i + 1}", value=prefilled.get("email", ""), key=f"{key_prefix}_contact_email_{i}"
            )
        contacts.append({"name": c_name.strip(), "phone": c_phone.strip(), "email": c_email.strip()})

    memo = st.text_area(memo_label, value=memo_value, key=f"{key_prefix}_memo")
    return name, kana, address, contacts, memo


def clean_contacts(contacts: list[dict]) -> list[dict]:
    """氏名・電話番号・メールのいずれも空の行は保存対象から除外する。"""
    return [c for c in contacts if c.get("name") or c.get("phone") or c.get("email")]


def summarize_contacts(contacts: list[dict]):
    """一覧表示用に、代表の電話番号・メールと担当者名の一覧を返す。"""
    if not contacts:
        return "", "", ""
    first = contacts[0]
    names = "、".join(c.get("name", "") for c in contacts if c.get("name"))
    return first.get("phone", ""), first.get("email", ""), names
