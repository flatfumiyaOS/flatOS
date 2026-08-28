"""顧客データベース・協力会社ページで共通して使う、法人/個人の入力欄を描画するヘルパー。

法人の場合は「ご担当者」欄（氏名・電話番号・メールアドレスのセット）を、行ごとの
削除ボタンと「＋ご担当者を追加」ボタンで増減できるようにする。個人の場合は
今まで通りの単一欄。

担当者の追加・削除はその場で行数を変えて再描画する必要があるため、
render_corporate_fields()はst.formの外で呼び出すこと（st.form内はst.form_submit_button
以外のボタンを置けないため）。
"""

from __future__ import annotations

import json

import streamlit as st


def _count_key(key_prefix: str) -> str:
    return f"{key_prefix}_contact_count"


def _pending_remove_key(key_prefix: str) -> str:
    return f"{key_prefix}_pending_remove"


def init_contact_count(key_prefix: str, initial_count: int) -> None:
    """担当者行の数をsession_stateで管理する。render_corporate_fields呼び出し前に呼ぶこと。"""
    count_key = _count_key(key_prefix)
    if count_key not in st.session_state:
        st.session_state[count_key] = max(1, initial_count)


def apply_pending_removal(key_prefix: str) -> None:
    """削除ボタンで指定された担当者行を、ウィジェットを描画する前に反映する。

    Streamlitはウィジェットが一度描画された後に同じキーのsession_stateを
    書き換えられないため、削除ボタンが押された次の再実行の先頭（ウィジェットを
    描画するより前）でこれを呼び出し、行の詰め直しを行う。
    """
    index = st.session_state.pop(_pending_remove_key(key_prefix), None)
    if index is None:
        return
    count = st.session_state.get(_count_key(key_prefix), 1)
    values = []
    for i in range(count):
        values.append(
            {
                "name": st.session_state.pop(f"{key_prefix}_contact_name_{i}", ""),
                "phone": st.session_state.pop(f"{key_prefix}_contact_phone_{i}", ""),
                "email": st.session_state.pop(f"{key_prefix}_contact_email_{i}", ""),
            }
        )
    if 0 <= index < len(values):
        del values[index]
    for i, v in enumerate(values):
        st.session_state[f"{key_prefix}_contact_name_{i}"] = v["name"]
        st.session_state[f"{key_prefix}_contact_phone_{i}"] = v["phone"]
        st.session_state[f"{key_prefix}_contact_email_{i}"] = v["email"]
    st.session_state[_count_key(key_prefix)] = len(values)


def clear_fields(key_prefix: str) -> None:
    """新規登録欄（st.formの外で描画している法人欄）の入力値を、次回の登録に備えて空にする。

    登録成功後の次の再実行の先頭（ウィジェットを描画するより前）で呼び出すこと。
    """
    count = st.session_state.pop(_count_key(key_prefix), 1)
    for i in range(count):
        st.session_state.pop(f"{key_prefix}_contact_name_{i}", None)
        st.session_state.pop(f"{key_prefix}_contact_phone_{i}", None)
        st.session_state.pop(f"{key_prefix}_contact_email_{i}", None)
    for suffix in ("name", "kana", "phone", "email", "address", "memo"):
        st.session_state.pop(f"{key_prefix}_{suffix}", None)


def _text_input(label: str, key: str, value: str = ""):
    """st.text_inputのラッパー。

    削除ボタンによる行の詰め直し(apply_pending_removal)は、ウィジェットが
    作られるより前にそのキーのsession_stateへ直接書き込む。その状態のキーに
    valueも渡すと、Streamlitが「defaultとSession State両方で値が指定された」
    という警告を出してしまうため、既にsession_stateに値がある場合はvalueを
    渡さない（session_state側の値がそのまま使われる）。
    """
    if key in st.session_state:
        return st.text_input(label, key=key)
    return st.text_input(label, value=value, key=key)


def _text_area(label: str, key: str, value: str = ""):
    if key in st.session_state:
        return st.text_area(label, key=key)
    return st.text_area(label, value=value, key=key)


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
    phone_value: str = "",
    email_value: str = "",
    address_value: str = "",
    memo_value: str = "",
    contacts_value: list[dict] | None = None,
):
    """法人用の入力欄一式を描画する。st.formの外で、呼び出し前にinit_contact_countが必要。

    会社の代表電話番号・メールアドレスは、個々のご担当者の連絡先とは別に、
    会社情報として入力できるようにする（会社の代表番号・住所が担当者個人の
    連絡先と異なる場合があるため）。

    戻り値: (name, kana, phone, email, address, contacts, memo)。contactsは
    {"name":..., "phone":..., "email":...} のリスト（未入力の行は呼び出し側で除外する想定）。
    """
    contacts_value = contacts_value or []

    col1, col2 = st.columns(2)
    with col1:
        name = _text_input(name_label, value=name_value, key=f"{key_prefix}_name")
        kana = _text_input("フリガナ", value=kana_value, key=f"{key_prefix}_kana")
        phone = _text_input("電話番号（会社の代表番号）", value=phone_value, key=f"{key_prefix}_phone")
    with col2:
        email = _text_input("メールアドレス（会社の代表アドレス）", value=email_value, key=f"{key_prefix}_email")
        address = _text_input("住所", value=address_value, key=f"{key_prefix}_address")

    st.markdown("**ご担当者**")
    count = st.session_state[_count_key(key_prefix)]
    contacts = []
    remove_clicked_index = None
    for i in range(count):
        prefilled = contacts_value[i] if i < len(contacts_value) else {}
        c1, c2, c3, c4 = st.columns([3, 3, 3, 1])
        with c1:
            c_name = _text_input(
                f"担当者名 {i + 1}", value=prefilled.get("name", ""), key=f"{key_prefix}_contact_name_{i}"
            )
        with c2:
            c_phone = _text_input(
                f"電話番号 {i + 1}", value=prefilled.get("phone", ""), key=f"{key_prefix}_contact_phone_{i}"
            )
        with c3:
            c_email = _text_input(
                f"メールアドレス {i + 1}", value=prefilled.get("email", ""), key=f"{key_prefix}_contact_email_{i}"
            )
        with c4:
            st.markdown(
                "<div style='height: 1.8rem'></div>", unsafe_allow_html=True
            )  # ラベル分の高さを空けて、ボタンを入力欄の横に揃える
            # ここでst.rerun()せず、削除対象の番号だけ覚えておく。すぐに中断すると、
            # まだ描画していない後ろの行のウィジェットがsession_stateに登録されないまま
            # 次の再実行を迎え、値が失われてしまうため（例: 3行目を消したはずが2行目が
            # 空になる不具合の原因だった）。ループを最後まで描画し終えてから処理する。
            if st.button("削除", key=f"{key_prefix}_contact_remove_{i}"):
                remove_clicked_index = i
        contacts.append({"name": c_name.strip(), "phone": c_phone.strip(), "email": c_email.strip()})

    if remove_clicked_index is not None:
        st.session_state[_pending_remove_key(key_prefix)] = remove_clicked_index
        st.rerun()

    if st.button("＋ ご担当者を追加", key=f"{key_prefix}_add_contact_btn"):
        st.session_state[_count_key(key_prefix)] += 1
        st.rerun()

    memo = _text_area(memo_label, value=memo_value, key=f"{key_prefix}_memo")
    return name, kana, phone, email, address, contacts, memo


def clean_contacts(contacts: list[dict]) -> list[dict]:
    """氏名・電話番号・メールのいずれも空の行は保存対象から除外する。"""
    return [c for c in contacts if c.get("name") or c.get("phone") or c.get("email")]


def contact_names(contacts: list[dict]) -> str:
    """一覧表示用に、担当者名を「、」区切りで並べた文字列を返す。"""
    return "、".join(c.get("name", "") for c in contacts if c.get("name"))
