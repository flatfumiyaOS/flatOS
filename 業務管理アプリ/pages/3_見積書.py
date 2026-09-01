"""見積書スプレッドシート画面。Googleスプレッドシートを画面いっぱいに埋め込み、直接編集できるようにする。"""

from __future__ import annotations

import re

import streamlit as st

import auth_gate
import google_auth
import project_store
import property_store
import sheets
from chat import show_chat_panel, show_chat_toggle
from db import get_all_customers, get_customer, get_customer_contacts_for_customer, init_db
from layout import APP_ICON_PATH
from postal import lookup_postal_code

ESTIMATE_DETAIL_SHEET = "御見積内訳書"
ESTIMATE_SUMMARY_SHEET = "御見積書"
ESTIMATE_ITEM_START_ROW = 32
ESTIMATE_ITEM_ROW_HEIGHT_PX = 28

# 案件の自社支社（project_store.OFFICE_OPTIONS）に応じて、御見積書シートの発行元
# 郵便番号・住所（D22:D24）を差し替える。東京オフィス・未設定（""）のときは
# テンプレートの現在値（東京オフィスの住所）のままにする。
OFFICE_ADDRESS_OVERRIDES = {
    "長野オフィス": {"D22": "〒389-0207", "D23": "長野県北佐久郡御代田町", "D24": "大字馬瀬口1597-486"},
}


def _format_customer_honorific(name: str) -> str:
    """氏名を、テンプレートの例（「石田　なつえ 様」）と同じ形式にする。

    苗字と名前の間は全角スペース、「様」の前は半角スペース。姓名の区切りが
    分からない場合は、名前全体の末尾にそのまま「 様」を付ける。
    """
    parts = [p for p in re.split(r"[ 　]+", name.strip()) if p]
    if len(parts) >= 2:
        return "　".join(parts) + " 様"
    return name.strip() + " 様"


def _compute_honorific_fields(customer_row: dict) -> tuple[str, str]:
    """顧客の敬称・担当者登録状況に応じて、(A9・B4に入れる宛名, B3に入れる顧客名) を返す。

    - 顧客担当者が未登録: 顧客名+顧客自身の敬称（様/御中）。B3は使わない。
    - 顧客担当者が登録済み: 最初の担当者の氏名+その担当者の敬称。B3に顧客名を入れる。
    """
    name = (customer_row.get("name") or "").strip()
    honorific = customer_row.get("honorific") or "様"
    customer_id = customer_row.get("id")

    contacts = get_customer_contacts_for_customer(customer_id) if customer_id is not None else []
    if not contacts:
        if honorific == "様":
            return _format_customer_honorific(name), ""
        return f"{name} {honorific}", ""

    first_contact = contacts[0]
    contact_name = (first_contact["name"] or "").strip()
    contact_honorific = first_contact["honorific"] or "様"
    if contact_honorific == "様":
        return _format_customer_honorific(contact_name), name
    return f"{contact_name} {contact_honorific}", name


def _find_previous_estimate_honorific(customer_name: str, exclude_project_id) -> dict | None:
    """同じ顧客名で過去に見積書を作成した案件があれば、そこで使われている
    A9・B3・B4セルの値をそのまま返す（後から手直しした表記も含めて引き継ぐため）。
    見つからなければNoneを返す。
    """
    if not customer_name:
        return None
    candidates = [
        p
        for p in project_store.get_all_projects()
        if p.get("customer_name") == customer_name
        and p.get("spreadsheet_id")
        and p["id"] != exclude_project_id
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    prev_spreadsheet_id = candidates[0]["spreadsheet_id"]
    try:
        a9 = sheets.read_cell(prev_spreadsheet_id, ESTIMATE_DETAIL_SHEET, "A9")
        b3 = sheets.read_cell(prev_spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B3")
        b4 = sheets.read_cell(prev_spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B4")
    except Exception:
        return None
    if not a9 and not b4:
        return None
    return {"a9": a9 or "", "b3": b3 or "", "b4": b4 or ""}


def _fill_estimate_defaults(
    spreadsheet_id: str, customer_row, project_name: str, exclude_project_id=None, office: str = ""
) -> None:
    """新規作成した見積書に、顧客名・住所・郵便番号・案件名をあらかじめ入力しておく。"""
    overrides = OFFICE_ADDRESS_OVERRIDES.get(office)
    if overrides:
        for cell, value in overrides.items():
            sheets.write_cell(spreadsheet_id, ESTIMATE_SUMMARY_SHEET, cell, value)

    if customer_row is not None:
        customer_row = dict(customer_row)
        name = (customer_row.get("name") or "").strip()

        previous = _find_previous_estimate_honorific(name, exclude_project_id)
        if previous is not None:
            # 同じ顧客の過去の見積書があれば、その表記（手直し済みの可能性がある）をそのまま引き継ぐ。
            if previous["b3"]:
                sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B3", previous["b3"])
            sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B4", previous["b4"])
            sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "A9", previous["a9"])
        else:
            honorific, company_name_for_b3 = _compute_honorific_fields(customer_row)
            if company_name_for_b3:
                sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B3", company_name_for_b3)
            sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B4", honorific)
            sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "A9", honorific)

        address = (customer_row.get("address") or "").strip()
        if address:
            sheets.write_cell(spreadsheet_id, ESTIMATE_DETAIL_SHEET, "B2", address)
            postal_code = (customer_row.get("postal_code") or "").strip() or lookup_postal_code(address)
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

st.set_page_config(page_title="見積書", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

init_db()

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
    source_mode = st.radio(
        "見積書のもとになる情報",
        options=["案件", "物件"],
        horizontal=True,
        key="estimate_source_mode",
    )
    st.caption(
        "「案件」: 1つの案件で1件の大きな改修工事を行う場合。"
        "「物件」: 同じ現場に対して、随時・部分的な工事が何度も発生する場合"
        "（物件を選び、その都度の工事内容を案件名として入力します）。"
    )

    customers = get_all_customers()
    create_clicked = False
    project_choice = None
    new_project_name = ""
    properties = []
    property_id = None
    property_project_name = ""

    if source_mode == "案件":
        NEW_PROJECT_CHOICE = "（新規に案件を作成）"
        existing_projects = [p for p in project_store.get_all_projects() if not p.get("archived")]
        project_choice_options = [NEW_PROJECT_CHOICE] + [p["name"] for p in existing_projects]
        project_choice = st.selectbox(
            "案件を選択", options=project_choice_options, key="estimate_project_choice"
        )

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
    else:
        properties = property_store.get_all_properties()
        if not properties:
            st.info("先に「物件」ページで物件を登録してください。")
        else:
            property_id = st.selectbox(
                "物件を選択",
                options=[p["id"] for p in properties],
                format_func=lambda x: next(
                    f"{p['customer_name']} / {p['name']}" for p in properties if p["id"] == x
                ),
                key="estimate_property_choice",
            )
            col_name, col_button = st.columns([3, 1])
            with col_name:
                property_project_name = st.text_input(
                    "案件名（今回の工事内容を入力）",
                    key="estimate_property_project_name",
                    label_visibility="collapsed",
                    placeholder="案件名（例: 3階トイレ改修工事）",
                )
            with col_button:
                create_clicked = st.button(
                    "新規見積作成", key="create_estimate_button_property", width="stretch"
                )

    if create_clicked:
        if source_mode == "案件" and project_choice == "（新規に案件を作成）" and not new_project_name.strip():
            st.error("案件名を入力してください。")
        elif source_mode == "物件" and not property_project_name.strip():
            st.error("案件名を入力してください。")
        else:
            with st.spinner("スプレッドシートを作成しています..."):
                try:
                    if source_mode == "案件":
                        if project_choice == "（新規に案件を作成）":
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
                            linked_customer_name = linked_project_existing.get("customer_name")
                            # 案件が持つ住所（現場住所）はそのまま使いつつ、敬称・担当者の
                            # 判定に必要な情報は顧客データベース側の一致するレコードから補う。
                            db_customer_row = next(
                                (c for c in customers if c["name"] == linked_customer_name), None
                            )
                            customer_row = (
                                {
                                    "id": db_customer_row["id"] if db_customer_row else None,
                                    "name": linked_customer_name,
                                    "address": linked_project_existing.get("address", ""),
                                    "postal_code": db_customer_row["postal_code"] if db_customer_row else "",
                                    "honorific": db_customer_row["honorific"] if db_customer_row else "様",
                                }
                                if linked_customer_name
                                else None
                            )

                        # 案件管理にも案件として登録し、見積書スプレッドシートを紐付ける
                        # （案件を選択していればその案件に、新規作成であれば同じ名前の
                        # 既存案件があればそこに、無ければ新規作成して紐付ける）。
                        linked_project = project_store.get_or_create_project(project_name)
                    else:
                        selected_property = next(p for p in properties if p["id"] == property_id)
                        project_name = property_project_name.strip()
                        db_customer_row = get_customer(selected_property["customer_id"])
                        if selected_property["address_type"] == property_store.ADDRESS_TYPE_SAME_AS_CUSTOMER:
                            effective_address = (db_customer_row["address"] or "") if db_customer_row else ""
                        else:
                            effective_address = selected_property["address"] or ""
                        customer_row = dict(db_customer_row) if db_customer_row else None
                        if customer_row is not None:
                            customer_row["address"] = effective_address

                        # 物件は工事完了後も現場として残り続けるため、同じ物件に対して
                        # 案件名を変えながら何度も見積書を作ることを想定する。案件名が
                        # 既存の案件と一致すればそこに、無ければ新規作成して紐付ける。
                        linked_project = project_store.get_or_create_project(project_name)
                        project_store.update_basic_info(
                            linked_project["id"],
                            selected_property["customer_name"],
                            effective_address,
                            linked_project.get("start_date", ""),
                            linked_project.get("end_date", ""),
                            linked_project.get("overview", ""),
                        )
                        project_store.set_property_link(
                            linked_project["id"], selected_property["id"], selected_property["name"]
                        )

                    # 過去の見積書検索で自分自身をヒットさせないよう、スプレッドシート
                    # 作成前に案件IDを確定させておく。
                    new_id = sheets.create_estimate_spreadsheet(
                        project_name, google_auth.get_credentials()
                    )
                    _fill_estimate_defaults(
                        new_id, customer_row, project_name,
                        exclude_project_id=linked_project["id"], office=linked_project.get("office", ""),
                    )
                    _clear_old_example_rows(new_id)

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
