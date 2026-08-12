"""工程表作成画面。図面をもとに工程表（ガントチャート）を自動生成し、Googleスプレッドシートとして保存する。

作成済みの工程表は一覧から選択して開き直せる（毎回新規作成し直す必要はない）。
"""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path

import streamlit as st

import google_auth
import schedule_generator
import schedule_store
import sheets
from chat import show_chat_panel, show_chat_toggle
from db import get_all_customers
from layout import show_header

# B列以降の列幅（ピクセル）。新規作成した工程表には自動的にこの幅を適用する。
SCHEDULE_COLUMN_WIDTH_PX = 50

st.set_page_config(page_title="工程表", layout="wide")

google_auth.handle_login_redirect()

show_header()

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

st.title("工程表作成")

if not google_auth.is_logged_in():
    st.link_button("Googleでログイン", google_auth.get_login_url())
    st.caption("工程表を作成・閲覧するには、Googleアカウントでログインしてください。")
else:
    schedules = schedule_store.get_all_schedules()
    schedule_options = {
        s["id"]: f"{s['customer_name']} / {s['project_name']}（{s['created_at'][:10]}）"
        for s in schedules
    }

    # selectbox（key="selected_schedule_id"）がこの下で生成される前に反映しておく必要がある。
    # ウィジェットが一度生成された後に同じキーのsession_stateへ直接代入するとエラーになるため。
    if "_pending_select_schedule_id" in st.session_state:
        st.session_state["selected_schedule_id"] = st.session_state.pop(
            "_pending_select_schedule_id"
        )

    col_select, col_new = st.columns([3, 1])
    with col_select:
        selected_schedule_id = (
            st.selectbox(
                "作成済みの工程表を選択",
                options=list(schedule_options.keys()),
                format_func=lambda sid: schedule_options[sid],
                key="selected_schedule_id",
            )
            if schedule_options
            else None
        )
        if not schedule_options:
            st.info("まだ工程表が作成されていません。「＋ 新規作成」から作成してください。")
    with col_new:
        st.write("")
        if st.button("＋ 新規作成", key="new_schedule_toggle_button", width="stretch"):
            st.session_state["show_new_schedule_form"] = True

    if st.session_state.get("show_new_schedule_form"):
        with st.container(border=True):
            customers = get_all_customers()
            customer_names = ["（選択してください）"] + [c["name"] for c in customers]
            customer_name_selection = st.selectbox(
                "顧客名", options=customer_names, key="schedule_customer_name"
            )
            project_name = st.text_input("案件名", key="schedule_project_name")

            uploaded_drawing = st.file_uploader(
                "図面・仕様書を添付（PDF・画像）",
                type=["pdf", "png", "jpg", "jpeg"],
                key="schedule_drawing_uploader",
            )

            # 着工日・工期は「新規工程表を作成」ボタンより下に配置する。日付選択のカレンダーが
            # 開いたままになった場合に、下にある要素(ボタンなど)がクリックできなくなる問題を避けるため。
            col_start, col_days = st.columns(2)
            with col_start:
                start_date_value = st.date_input(
                    "着工日", value=datetime.date.today(), key="schedule_start_date"
                )
            with col_days:
                total_days = st.number_input(
                    "工期の目安（実働日数）",
                    min_value=1,
                    max_value=180,
                    value=21,
                    step=1,
                    help="土日・祝日を除いた実際に作業する日数の目安です。土日・祝日は自動的にスキップして日程を組みます。",
                    key="schedule_total_days",
                )

            create_clicked = st.button(
                "新規工程表を作成", key="create_schedule_button", type="primary"
            )

            if create_clicked:
                if customer_name_selection == "（選択してください）":
                    st.error("顧客名を選択してください。")
                elif not project_name.strip():
                    st.error("案件名を入力してください。")
                elif uploaded_drawing is None:
                    st.error("図面・仕様書を添付してください。")
                else:
                    with st.spinner("図面を解析し、工程表を作成しています..."):
                        try:
                            media_type = uploaded_drawing.type or "application/octet-stream"
                            config = schedule_generator.analyze_drawing_to_config(
                                uploaded_drawing.getvalue(),
                                media_type,
                                customer_name_selection,
                                project_name.strip(),
                                start_date_value.isoformat(),
                                int(total_days),
                            )

                            with tempfile.TemporaryDirectory() as tmp_dir:
                                xlsx_path = str(Path(tmp_dir) / "schedule.xlsx")
                                schedule_generator.build_schedule_xlsx(config, xlsx_path)

                                date_str = datetime.date.today().strftime("%Y%m%d")
                                file_name = (
                                    f"【工程表】{date_str}_{customer_name_selection}_"
                                    f"{project_name.strip()}"
                                )
                                new_id = sheets.create_schedule_spreadsheet(
                                    xlsx_path, file_name, google_auth.get_credentials()
                                )

                            # B列以降の列幅をピクセル単位で統一する（ユーザー希望）。
                            sheet_name = config.get("sheet_name", "工程表")
                            column_count = sheets.get_column_count(new_id, sheet_name)
                            if column_count >= 2:
                                sheets.set_column_width(
                                    new_id, sheet_name, 2, column_count, SCHEDULE_COLUMN_WIDTH_PX
                                )

                            new_record = schedule_store.add_schedule(
                                customer_name_selection, project_name.strip(), new_id, file_name
                            )
                            st.session_state["_pending_select_schedule_id"] = new_record["id"]
                            st.session_state["show_new_schedule_form"] = False
                            st.success(f"「{file_name}」を作成しました。")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"作成に失敗しました: {exc}")

    if selected_schedule_id is not None:
        schedule = schedule_store.get_schedule(selected_schedule_id)
        col_caption, col_open = st.columns([4, 1])
        with col_caption:
            st.caption(
                f"表示中: {schedule['file_name']}"
                "（Googleアカウントでログインし、編集権限があればそのまま編集できます）"
            )
        with col_open:
            st.link_button(
                "Googleドライブで開く",
                sheets.spreadsheet_url(schedule["spreadsheet_id"]),
                width="stretch",
            )
        st.markdown(
            f'<iframe class="gsheet-embed" '
            f'src="{sheets.spreadsheet_url(schedule["spreadsheet_id"])}"></iframe>',
            unsafe_allow_html=True,
        )

# チャットのトグル・パネルは、ページ固有のウィジェット（工程表選択など）をすべて
# 生成し終えたあとに呼び出す。先に呼び出すと、チャットの開閉ボタンが押されたときの
# st.rerun()がそれらのウィジェットに到達する前にスクリプトを打ち切ってしまい、
# 選択状態がsession_stateから失われて最初の項目に戻ってしまう不具合があったため。
show_chat_toggle()
show_chat_panel(category="工程表")
