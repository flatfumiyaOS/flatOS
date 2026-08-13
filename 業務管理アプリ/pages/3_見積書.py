"""見積書スプレッドシート画面。Googleスプレッドシートを画面いっぱいに埋め込み、直接編集できるようにする。"""

from __future__ import annotations

import re

import streamlit as st

import auth_gate
import google_auth
import project_store
import sheets
from chat import show_chat_panel, show_chat_toggle
from db import get_all_customers
from postal import lookup_postal_code

ESTIMATE_DETAIL_SHEET = "御見積内訳書"
ESTIMATE_SUMMARY_SHEET = "御見積書"
ESTIMATE_ITEM_START_ROW = 32
ESTIMATE_ITEM_ROW_HEIGHT_PX = 28


def _format_customer_honorific(name: str) -> str:
    """顧客名を、テンプレートの例（「石田　なつえ 様」）と同じ形式にする。

    苗字と名前の間は全角スペース、「様」の前は半角スペース。姓名の区切りが
    分からない場合は、名前全体の末尾にそのまま「 様」を付ける。
    """
    parts = [p for p in re.split(r"[ 　]+", name.strip()) if p]
    if len(parts) >= 2:
        return "　".join(parts) + " 様"
    return name.strip() + " 様"


def _fill_estimate_defaults(spreadsheet_id: str, customer_row, project_name: str) -> None:
    """新規作成した見積書に、顧客名・住所・郵便番号・案件名をあらかじめ入力しておく。"""
    if customer_row is not None:
        honorific = _format_customer_honorific(customer_row["name"])
        sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B4", honorific)
        sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "A9", honorific)

        address = (customer_row["address"] or "").strip()
        if address:
            sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B2", address)
            postal_code = lookup_postal_code(address)
            if postal_code:
                sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B1", f"〒{postal_code}")

    sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B14", project_name)


def _find_marker_row(spreadsheet_id: str, sheet_name: str, marker: str, search_rows: int) -> int | None:
    """A列を上から探して、指定した見出し文字列（例: 「【諸経費】」）が最初に現れる行番号を返す。

    見つからなければNoneを返す。
    """
    end_row = ESTIMATE_ITEM_START_ROW + search_rows - 1
    values = sheets.read_range(
        spreadsheet_id, sheet_name, f"A{ESTIMATE_ITEM_START_ROW}:A{end_row}"
    )
    for i, row in enumerate(values):
        if row and row[0] == marker:
            return ESTIMATE_ITEM_START_ROW + i
    return None


def _clear_old_example_rows(spreadsheet_id: str) -> None:
    """コピー直後の見積書に残っている、前の案件の明細をあらかじめ空にする。

    テンプレートには過去の案件の明細が数百行残っていることがあり、そのままだと
    チャットで明細を書き換える際にAIが大量の既存内容を読み込む必要が生じ、応答が
    長さの上限に達して処理が完了しなくなることがあった。そのため、作成した時点で
    あらかじめアプリ側の処理として値だけを空にしておく（行そのものは削除しない。
    行を削除すると後ろに続く【諸経費】以降の位置が詰まってしまい、大きな案件の
    明細を書き込むための余白が失われてしまうため）。
    """
    detail_end = _find_marker_row(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "【諸経費】", 400)
    if detail_end is not None:
        detail_end -= 1  # 【諸経費】の手前（空行）までを対象にする
        rows = detail_end - ESTIMATE_ITEM_START_ROW + 1
        if rows > 0:
            sheets.write_range(
                spreadsheet_id,
                ESTIMATE_DETAIL_SHEET,
                f"A{ESTIMATE_ITEM_START_ROW}:F{detail_end}",
                [[""] * 6 for _ in range(rows)],
            )
            # テンプレートに残っていた旧案件の文章の長さに合わせて、行の高さが
            # 部分的に高くなっていることがある（2行に折り返す文章だった箇所など）。
            # 値を空にしても高さはそのまま残ってしまうため、標準の高さに揃え直す。
            sheets.set_row_height(
                spreadsheet_id,
                ESTIMATE_DETAIL_SHEET,
                ESTIMATE_ITEM_START_ROW,
                detail_end,
                ESTIMATE_ITEM_ROW_HEIGHT_PX,
            )

    summary_end = _find_marker_row(spreadsheet_id, ESTIMATE_SUMMARY_SHEET, "出精値引き", 40)
    if summary_end is not None:
        summary_end -= 1  # 「出精値引き」の手前までを対象にする
        rows = summary_end - ESTIMATE_ITEM_START_ROW + 1
        if rows > 0:
            sheets.write_range(
                spreadsheet_id,
                ESTIMATE_SUMMARY_SHEET,
                f"A{ESTIMATE_ITEM_START_ROW}:F{summary_end}",
                [[""] * 6 for _ in range(rows)],
            )
            sheets.set_row_height(
                spreadsheet_id,
                ESTIMATE_SUMMARY_SHEET,
                ESTIMATE_ITEM_START_ROW,
                summary_end,
                ESTIMATE_ITEM_ROW_HEIGHT_PX,
            )

st.set_page_config(page_title="見積書", layout="wide")
auth_gate.require_password()

google_auth.handle_login_redirect()

st.markdown(
    """
    <style>
        .block-container {
            /* Streamlit自身の固定ヘッダー（高さ約3.75rem）の下に本文の先頭が
               隠れてしまわないよう、それより大きい上余白を確保する。 */
            padding-top: 4rem;
            padding-bottom: 0;
            padding-left: 0.5rem;
            padding-right: 0.5rem;
            max-width: 100% !important;
        }
        /* アプリのページ本体は普通にスクロールできるようにしたまま、スプレッドシート
           （iframe）の端までスクロールしたときに、その続きがページ本体側のスクロールに
           漏れ出さないようにする（overscroll-behavior: contain）。
           上のpadding-topを増やした分、iframeの高さもその分差し引く。 */
        iframe.gsheet-embed {
            width: 100%;
            height: calc(100vh - 13.5rem);
            border: none;
            display: block;
            overscroll-behavior: contain;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

if not google_auth.is_logged_in():
    st.link_button("Googleでログイン", google_auth.get_login_url())
    st.caption("新しい案件の見積書を作成するには、Googleアカウントでログインしてください。")
else:
    NEW_PROJECT_CHOICE = "（新規に案件を作成）"
    existing_projects = project_store.get_all_projects()
    project_choice_options = [NEW_PROJECT_CHOICE] + [p["name"] for p in existing_projects]
    project_choice = st.selectbox(
        "案件を選択", options=project_choice_options, key="estimate_project_choice"
    )

    customers = get_all_customers()
    new_project_name = ""
    if project_choice == NEW_PROJECT_CHOICE:
        customer_names = ["（選択してください）"] + [c["name"] for c in customers]
        st.selectbox("顧客を選択", options=customer_names, key="selected_customer_name")

        col_name, col_button = st.columns([3, 1])
        with col_name:
            new_project_name = st.text_input(
                "新規案件名",
                key="new_project_name",
                label_visibility="collapsed",
                placeholder="新規案件名（例: 〇〇邸 改修工事）",
            )
        with col_button:
            create_clicked = st.button(
                "新規見積作成", key="create_estimate_button", width="stretch"
            )
    else:
        st.caption(f"「{project_choice}」の見積書を新規作成します。")
        create_clicked = st.button(
            "新規見積作成", key="create_estimate_button", width="stretch"
        )

    if create_clicked:
        if project_choice == NEW_PROJECT_CHOICE and not new_project_name.strip():
            st.error("案件名を入力してください。")
        else:
            with st.spinner("スプレッドシートを作成しています..."):
                try:
                    if project_choice == NEW_PROJECT_CHOICE:
                        project_name = new_project_name.strip()
                        selected_name = st.session_state.get("selected_customer_name")
                        customer_row = next(
                            (c for c in customers if c["name"] == selected_name), None
                        )
                    else:
                        linked_project_existing = next(
                            p for p in existing_projects if p["name"] == project_choice
                        )
                        project_name = linked_project_existing["name"]
                        customer_row = (
                            {
                                "name": linked_project_existing["customer_name"],
                                "address": linked_project_existing.get("address", ""),
                            }
                            if linked_project_existing.get("customer_name")
                            else None
                        )

                    new_id = sheets.create_estimate_spreadsheet(
                        project_name, google_auth.get_credentials()
                    )
                    _fill_estimate_defaults(new_id, customer_row, project_name)
                    _clear_old_example_rows(new_id)

                    # 案件管理にも案件として登録し、見積書スプレッドシートを紐付ける
                    # （案件を選択していればその案件に、新規作成であれば同じ名前の
                    # 既存案件があればそこに、無ければ新規作成して紐付ける）。
                    linked_project = project_store.get_or_create_project(project_name)
                    project_store.set_spreadsheet_id(linked_project["id"], new_id)

                    st.session_state["current_spreadsheet_id"] = new_id
                    st.session_state["current_project_name"] = project_name
                    st.success(f"「{project_name}」の見積書を作成しました。")
                    st.rerun()
                except Exception as exc:
                    st.error(f"作成に失敗しました: {exc}")

current_id = st.session_state.get("current_spreadsheet_id", sheets.TEMPLATE_SPREADSHEET_ID)
current_label = st.session_state.get("current_project_name", "見積書【例】（テンプレート）")

col_caption, col_open = st.columns([4, 1])
with col_caption:
    st.caption(
        f"表示中: {current_label}"
        "（Googleアカウントでログインし、編集権限があればそのまま編集できます）"
    )
with col_open:
    st.link_button(
        "Googleドライブで開く",
        sheets.spreadsheet_url(current_id),
        width="stretch",
    )

st.markdown(
    f'<iframe class="gsheet-embed" src="{sheets.spreadsheet_url(current_id)}"></iframe>',
    unsafe_allow_html=True,
)

# チャットのトグル・パネルは、ページ固有のウィジェット（顧客選択など）をすべて
# 生成し終えたあとに呼び出す。先に呼び出すと、チャットの開閉ボタンが押されたときの
# st.rerun()がそれらのウィジェットに到達する前にスクリプトを打ち切ってしまい、
# 選択状態がsession_stateから失われてしまう不具合があったため。
show_chat_toggle()
show_chat_panel(category="見積書")
