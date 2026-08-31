"""協力会社ページで使う「ご担当者」欄（複数行＋追加・削除ボタン）の共通部品。

行の追加・削除はその場で行数を変えて再描画する必要があるため、
render_contact_rows()はst.formの外で呼び出すこと（st.form内はst.form_submit_button
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
    """担当者行の数をsession_stateで管理する。render_contact_rows呼び出し前に呼ぶこと。"""
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


def clear_contact_rows(key_prefix: str) -> None:
    """新規登録欄のご担当者の入力値を、次回の登録に備えて空にする。

    登録成功後の次の再実行の先頭（ウィジェットを描画するより前）で呼び出すこと。
    session_stateのキーをpop（削除）するだけだと、ブラウザ側の表示がリセットされずに
    前回入力した文字列が残ってしまう（Streamlitの既知の挙動）。空文字列を明示的に
    書き込むことで、ウィジェットの表示も確実にクリアされる。
    """
    count = st.session_state.pop(_count_key(key_prefix), 1)
    for i in range(count):
        st.session_state[f"{key_prefix}_contact_name_{i}"] = ""
        st.session_state[f"{key_prefix}_contact_phone_{i}"] = ""
        st.session_state[f"{key_prefix}_contact_email_{i}"] = ""


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


def contacts_from_json(contacts_json: str | None) -> list[dict]:
    if not contacts_json:
        return []
    try:
        return json.loads(contacts_json)
    except (json.JSONDecodeError, TypeError):
        return []


def render_contact_rows(key_prefix: str, contacts_value: list[dict] | None = None) -> list[dict]:
    """「ご担当者」欄（氏名・電話番号・メールアドレスの複数行、削除ボタン、追加ボタン）を描画する。

    st.formの外で、呼び出し前にinit_contact_countが必要。
    戻り値: {"name":..., "phone":..., "email":...} のリスト（未入力の行は
    呼び出し側でclean_contacts()を使って除外する想定）。
    """
    contacts_value = contacts_value or []

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

    return contacts


def clean_contacts(contacts: list[dict]) -> list[dict]:
    """氏名・電話番号・メールのいずれも空の行は保存対象から除外する。"""
    return [c for c in contacts if c.get("name") or c.get("phone") or c.get("email")]


def contact_names(contacts: list[dict]) -> str:
    """一覧表示用に、担当者名を「、」区切りで並べた文字列を返す。"""
    return "、".join(c.get("name", "") for c in contacts if c.get("name"))
