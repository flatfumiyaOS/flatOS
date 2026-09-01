"""案件管理画面。案件ごとに基本情報・資料・工程表・現場写真・見積書をまとめて管理する。

「案件一覧」「案件詳細」「新規案件作成」の3つの表示モードを持ち、
st.session_state["project_view_mode"] で切り替える（ANDPAD風の画面遷移）。
"""

from __future__ import annotations

import base64
import datetime
import html
import io
import re

import streamlit as st
from PIL import Image

import auth_gate
import google_auth
import project_store
import sheets
from chat import show_chat_panel, show_chat_toggle
from db import get_all_customers
from layout import APP_ICON_PATH, show_header

PHASES = ["現地調査", "解体", "隠蔽部(電気・水道・ガス)", "木工事", "仕上げ"]

STATUS_FILTER_ACTIVE = "施工中（進行中）"
STATUS_FILTER_OPTIONS = [STATUS_FILTER_ACTIVE, "着工前", "完工", "すべて"]
STATUS_BADGE_COLOR = {"施工中": "blue", "着工前": "gray", "完工": "green"}
STATUS_BADGE_HEX = {"施工中": "#2563eb", "着工前": "#6b7280", "完工": "#16a34a"}
CARD_FALLBACK_BG = "#1a1311"


def _parse_date(value: str) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _option_index(value: str, options: list[str]) -> int:
    return options.index(value) if value in options else 0


def _extract_spreadsheet_id(text: str) -> str:
    """Google スプレッドシートのURLが貼り付けられた場合はIDを取り出す。すでにIDのみの場合はそのまま返す。"""
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", text)
    return match.group(1) if match else text


def _project_status(project: dict, today: datetime.date) -> str:
    """着工予定日・完工予定日から、案件の状態（着工前／施工中／完工）を判定する。"""
    start = _parse_date(project.get("start_date", ""))
    end = _parse_date(project.get("end_date", ""))
    if end and end < today:
        return "完工"
    if start and start <= today:
        return "施工中"
    return "着工前"


def _format_date_range(project: dict) -> str:
    start = project.get("start_date") or "未定"
    end = project.get("end_date") or "未定"
    return f"{start} 〜 {end}"


@st.cache_data(show_spinner=False)
def _photo_thumbnail_data_uri(photo: dict, max_dim: int = 640) -> str | None:
    """案件カードの背景用に、現場写真を縮小してbase64データURIにして返す。

    元の写真をそのまま埋め込むとカード一覧のページが重くなるため、
    表示サイズに合わせて縮小してから埋め込む。取得できなければNoneを返す。
    """
    data = project_store.get_photo_display_bytes(photo)
    if data is None:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.thumbnail((max_dim, max_dim))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=70)
        b64 = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


def _render_project_card(p: dict, status: str) -> None:
    """案件一覧の1枚のカード（現場写真を背景にしたスタイル）を描画する。

    背景画像は、登録されていれば表紙写真（cover_photo）を優先し、
    無ければ現場写真ギャラリーの最初の1枚を使う。どちらも無ければ背景色のみにする。
    """
    photos = p.get("photos") or []
    cover_photo = p.get("cover_photo")
    bg_source = cover_photo or (photos[0] if photos else None)
    bg_data_uri = _photo_thumbnail_data_uri(bg_source) if bg_source else None

    if bg_data_uri:
        bg_style = (
            "background-image: linear-gradient(rgba(20,24,33,0.75), rgba(20,24,33,0.75)), "
            f"url('{bg_data_uri}'); background-size: cover; background-position: center;"
        )
    else:
        bg_style = f"background-color: {CARD_FALLBACK_BG};"

    badge_color = STATUS_BADGE_HEX.get(status, "#6b7280")
    name = html.escape(p["name"])
    customer = html.escape(p.get("customer_name") or "未設定")
    address = html.escape(p.get("address") or "未設定")
    period = html.escape(_format_date_range(p))
    status_label = html.escape(status)

    unbilled_badge = ""
    if p.get("order_status") == "受注済" and p.get("billing_status") != project_store.BILLING_STATUS_BILLED:
        unbilled_badge = (
            '<span style="display:inline-block; background:#dc2626; color:#ffffff; font-size:12px; '
            'font-weight:600; padding:3px 10px; border-radius:999px; margin-bottom:10px; margin-left:6px;">'
            "請求ステータス: 未請求</span>"
        )

    st.markdown(
        f"""
        <div style="{bg_style} border-radius: 12px 12px 0 0; box-shadow: 0 4px 14px rgba(0,0,0,0.25);
                    padding: 16px; min-height: 190px; display: flex; flex-direction: column;
                    justify-content: space-between;">
            <div>
                <span style="display:inline-block; background:{badge_color}; color:#ffffff;
                             font-size:12px; font-weight:600; padding:3px 10px; border-radius:999px;
                             margin-bottom:10px;">{status_label}</span>{unbilled_badge}
                <div style="color:#ffffff; font-weight:700; font-size:17px; line-height:1.4;
                            text-shadow: 0 1px 3px rgba(0,0,0,0.6);">{name}</div>
            </div>
            <div style="color:#e5e5e5; font-size:12.5px; line-height:1.7;
                        text-shadow: 0 1px 2px rgba(0,0,0,0.6);">
                顧客名: {customer}<br>
                現場住所: {address}<br>
                {period}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _go_to_list() -> None:
    st.session_state["project_view_mode"] = "list"


def _go_to_detail(project_id: int) -> None:
    st.session_state["selected_project_id"] = project_id
    st.session_state["project_view_mode"] = "detail"


st.set_page_config(page_title="案件管理", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

google_auth.handle_login_redirect()

show_header()

if "project_view_mode" not in st.session_state:
    st.session_state["project_view_mode"] = "list"

view_mode = st.session_state["project_view_mode"]
projects = project_store.get_all_projects()

# ==================== 案件一覧モード ====================
if view_mode == "list":
    col_title, col_new = st.columns([4, 1])
    with col_title:
        st.title("📁 案件管理")
    with col_new:
        st.write("")
        if st.button("＋ 新規案件を登録", key="go_create_button", width="stretch"):
            st.session_state["project_view_mode"] = "create"
            st.rerun()

    if not projects:
        st.info("まだ案件が登録されていません。「＋ 新規案件を登録」から作成してください。")
    else:
        has_archived = any(p.get("archived") for p in projects)
        show_archived = False
        if has_archived:
            show_archived = st.checkbox(
                "非表示にした案件も表示する", value=False, key="show_archived_projects"
            )
        visible_projects = projects if show_archived else [p for p in projects if not p.get("archived")]

        status_filter = st.pills(
            "絞り込み",
            options=STATUS_FILTER_OPTIONS,
            default=STATUS_FILTER_ACTIVE,
            key="project_status_filter",
        )
        if status_filter is None:
            status_filter = STATUS_FILTER_ACTIVE

        unbilled_only = st.checkbox(
            "受注済・未請求のみ表示（請求漏れの確認用）", value=False, key="unbilled_only_filter"
        )

        today = datetime.date.today()
        if status_filter == "すべて":
            filtered_projects = visible_projects
        elif status_filter == STATUS_FILTER_ACTIVE:
            filtered_projects = [p for p in visible_projects if _project_status(p, today) == "施工中"]
        else:
            filtered_projects = [p for p in visible_projects if _project_status(p, today) == status_filter]

        if unbilled_only:
            filtered_projects = [
                p for p in filtered_projects
                if p.get("order_status") == "受注済" and p.get("billing_status") != project_store.BILLING_STATUS_BILLED
            ]

        if not filtered_projects:
            st.caption("該当する案件がありません。")
        else:
            # st.container(key=...)で生成される "st-key-project_card_<id>" クラスに対して、
            # カード上部（写真+情報）と下の「詳細を開く」ボタンの間の余白を詰める。
            st.markdown(
                """
                <style>
                div[class*="st-key-project_card_"] { gap: 0 !important; }
                div[class*="st-key-project_card_"] div[data-testid="stButton"] button {
                    border-radius: 0 0 12px 12px !important;
                    border-top: none !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            cols = st.columns(3)
            for i, p in enumerate(filtered_projects):
                status = _project_status(p, today)
                with cols[i % 3]:
                    with st.container(key=f"project_card_{p['id']}"):
                        _render_project_card(p, status)
                        if st.button(
                            "詳細を開く", key=f"open_project_{p['id']}", width="stretch"
                        ):
                            _go_to_detail(p["id"])
                            st.rerun()

# ==================== 新規案件作成モード ====================
elif view_mode == "create":
    if st.button("← 案件一覧に戻る", key="back_to_list_from_create"):
        _go_to_list()
        st.rerun()

    st.title("新規案件を登録")

    customers = get_all_customers()
    customer_names = ["（選択してください）"] + [c["name"] for c in customers]

    with st.form("new_project_form"):
        new_name = st.text_input("案件名", placeholder="例: 〇〇邸 改修工事")
        customer_name = st.selectbox("顧客名", options=customer_names)
        address = st.text_input("現場住所")
        col_start, col_end = st.columns(2)
        with col_start:
            start_date_value = st.date_input("着工予定日", value=datetime.date.today())
        with col_end:
            end_date_value = st.date_input("完工予定日", value=datetime.date.today())
        overview = st.text_area("工事概要", height=120)
        cover_photo_file = st.file_uploader(
            "現場建物写真（案件一覧カードの表紙に使います）", type=["jpg", "jpeg", "png"]
        )

        st.markdown("##### 自社情報")
        col_office, col_staff = st.columns(2)
        with col_office:
            office = st.selectbox("自社支社", options=project_store.OFFICE_OPTIONS)
        with col_staff:
            staff = st.selectbox("自社担当者", options=project_store.STAFF_OPTIONS)

        st.markdown("##### ステータス・分類")
        col_order, col_billing_timing, col_billing_status = st.columns(3)
        with col_order:
            order_status = st.selectbox("受注ステータス", options=project_store.ORDER_STATUS_OPTIONS)
        with col_billing_timing:
            billing_timing = st.selectbox("請求タイミング", options=project_store.BILLING_TIMING_OPTIONS)
        with col_billing_status:
            billing_status = st.selectbox("請求ステータス", options=project_store.BILLING_STATUS_OPTIONS)
        col_payment_terms, col_billing_due_date = st.columns(2)
        with col_payment_terms:
            payment_terms = st.selectbox("支払条件", options=project_store.PAYMENT_TERMS_OPTIONS)
        with col_billing_due_date:
            billing_due_date_value = st.date_input("請求（予定）日", value=datetime.date.today())
        col_cat1, col_cat2, col_cat3 = st.columns(3)
        with col_cat1:
            category1 = st.selectbox("案件分類1", options=project_store.CATEGORY1_OPTIONS)
        with col_cat2:
            category2 = st.selectbox("案件分類2", options=project_store.CATEGORY2_OPTIONS)
        with col_cat3:
            category3 = st.selectbox("案件分類3", options=project_store.CATEGORY3_OPTIONS)

        submitted = st.form_submit_button("保存", type="primary")
        if submitted:
            if not new_name.strip():
                st.error("案件名を入力してください。")
            else:
                new_project = project_store.create_project(new_name.strip())
                selected_customer_name = (
                    customer_name if customer_name != "（選択してください）" else ""
                )
                project_store.update_basic_info(
                    new_project["id"],
                    selected_customer_name,
                    address,
                    start_date_value.isoformat(),
                    end_date_value.isoformat(),
                    overview,
                )
                project_store.update_case_details(
                    new_project["id"], office, staff, payment_terms, order_status,
                    billing_timing, billing_due_date_value.isoformat(),
                    category1, category2, category3, billing_status,
                )
                if cover_photo_file is not None:
                    project_store.set_cover_photo(
                        new_project["id"], cover_photo_file.name, cover_photo_file.getvalue()
                    )
                st.success(f"「{new_name}」を登録しました。")
                _go_to_detail(new_project["id"])
                st.rerun()

# ==================== 案件詳細モード ====================
elif view_mode == "detail":
    selected_id = st.session_state.get("selected_project_id")
    project = project_store.get_project(selected_id) if selected_id is not None else None

    if project is None:
        _go_to_list()
        st.rerun()

    if st.button("← 案件一覧に戻る", key="back_to_list_from_detail"):
        _go_to_list()
        st.rerun()

    st.title(f"📁 {project['name']}")
    caption_text = f"顧客名: {project.get('customer_name') or '未設定'}"
    if project.get("property_name"):
        caption_text += f"　／　物件: {project['property_name']}"
    st.caption(caption_text)
    if project.get("archived"):
        st.warning("この案件は非表示に設定されています（案件一覧・会計画面には表示されません）。")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["① 基本情報", "② 各種資料", "③ 工程表", "④ 現場写真", "⑤ 見積連携"]
    )

    with tab1:
        customers = get_all_customers()
        customer_names = ["（選択してください）"] + [c["name"] for c in customers]
        current_customer_name = project.get("customer_name", "")
        default_customer_index = (
            customer_names.index(current_customer_name)
            if current_customer_name in customer_names
            else 0
        )
        current_cover = project.get("cover_photo")
        if current_cover:
            cover_bytes = project_store.get_photo_display_bytes(current_cover)
            if cover_bytes:
                st.image(cover_bytes, caption="現在の現場建物写真", width=240)

        with st.form("basic_info_form"):
            customer_name = st.selectbox(
                "顧客名", options=customer_names, index=default_customer_index
            )
            address = st.text_input("現場住所", value=project.get("address", ""))
            col_start, col_end = st.columns(2)
            with col_start:
                start_date_value = st.date_input(
                    "着工予定日",
                    value=_parse_date(project.get("start_date", "")) or datetime.date.today(),
                )
            with col_end:
                end_date_value = st.date_input(
                    "完工予定日",
                    value=_parse_date(project.get("end_date", "")) or datetime.date.today(),
                )
            overview = st.text_area(
                "工事概要", value=project.get("overview", ""), height=120
            )
            cover_photo_file = st.file_uploader(
                "現場建物写真（案件一覧カードの表紙に使います）", type=["jpg", "jpeg", "png"]
            )
            saved = st.form_submit_button("保存")
            if saved:
                selected_customer_name = (
                    customer_name if customer_name != "（選択してください）" else ""
                )
                project_store.update_basic_info(
                    selected_id,
                    selected_customer_name,
                    address,
                    start_date_value.isoformat(),
                    end_date_value.isoformat(),
                    overview,
                )
                if cover_photo_file is not None:
                    project_store.set_cover_photo(
                        selected_id, cover_photo_file.name, cover_photo_file.getvalue()
                    )
                st.success("保存しました。")
                st.rerun()

        st.divider()

        with st.form("case_details_form"):
            st.markdown("##### 自社情報")
            col_office, col_staff = st.columns(2)
            with col_office:
                office = st.selectbox(
                    "自社支社", options=project_store.OFFICE_OPTIONS,
                    index=_option_index(project.get("office", ""), project_store.OFFICE_OPTIONS),
                )
            with col_staff:
                staff = st.selectbox(
                    "自社担当者", options=project_store.STAFF_OPTIONS,
                    index=_option_index(project.get("staff", ""), project_store.STAFF_OPTIONS),
                )

            st.markdown("##### ステータス・分類")
            col_order, col_billing_timing, col_billing_status = st.columns(3)
            with col_order:
                order_status = st.selectbox(
                    "受注ステータス", options=project_store.ORDER_STATUS_OPTIONS,
                    index=_option_index(project.get("order_status", ""), project_store.ORDER_STATUS_OPTIONS),
                )
            with col_billing_timing:
                billing_timing = st.selectbox(
                    "請求タイミング", options=project_store.BILLING_TIMING_OPTIONS,
                    index=_option_index(project.get("billing_timing", ""), project_store.BILLING_TIMING_OPTIONS),
                )
            with col_billing_status:
                billing_status = st.selectbox(
                    "請求ステータス", options=project_store.BILLING_STATUS_OPTIONS,
                    index=_option_index(project.get("billing_status", ""), project_store.BILLING_STATUS_OPTIONS),
                )
            col_payment_terms, col_billing_due_date = st.columns(2)
            with col_payment_terms:
                payment_terms = st.selectbox(
                    "支払条件", options=project_store.PAYMENT_TERMS_OPTIONS,
                    index=_option_index(project.get("payment_terms", ""), project_store.PAYMENT_TERMS_OPTIONS),
                )
            with col_billing_due_date:
                billing_due_date_value = st.date_input(
                    "請求（予定）日",
                    value=_parse_date(project.get("billing_due_date", "")) or datetime.date.today(),
                )
            col_cat1, col_cat2, col_cat3 = st.columns(3)
            with col_cat1:
                category1 = st.selectbox(
                    "案件分類1", options=project_store.CATEGORY1_OPTIONS,
                    index=_option_index(project.get("category1", ""), project_store.CATEGORY1_OPTIONS),
                )
            with col_cat2:
                category2 = st.selectbox(
                    "案件分類2", options=project_store.CATEGORY2_OPTIONS,
                    index=_option_index(project.get("category2", ""), project_store.CATEGORY2_OPTIONS),
                )
            with col_cat3:
                category3 = st.selectbox(
                    "案件分類3", options=project_store.CATEGORY3_OPTIONS,
                    index=_option_index(project.get("category3", ""), project_store.CATEGORY3_OPTIONS),
                )

            case_details_saved = st.form_submit_button("保存")
            if case_details_saved:
                project_store.update_case_details(
                    selected_id, office, staff, payment_terms, order_status,
                    billing_timing, billing_due_date_value.isoformat(),
                    category1, category2, category3, billing_status,
                )
                st.success("保存しました。")
                st.rerun()

        st.divider()
        if project.get("archived"):
            st.caption("この案件は非表示になっています。データは削除されていません。")
            if st.button("表示に戻す", key="unarchive_project_button"):
                project_store.unarchive_project(selected_id)
                st.success("表示に戻しました。")
                st.rerun()

            st.divider()
            st.markdown("##### この案件を完全に削除する")
            st.caption(
                "この操作は元に戻せません。写真・資料・原価データ・顧客請求データが"
                "すべて削除されます。Googleにログインしていれば、写真・見積書・工程表の"
                "Googleドライブ上のファイルもゴミ箱に移動します（ログインしていない場合、"
                "Googleドライブ上のファイルはそのまま残ります）。"
            )
            confirm_name = st.text_input(
                f"削除するには案件名「{project['name']}」を入力してください",
                key="delete_project_confirm_input",
            )
            if st.button(
                "この案件を完全に削除する",
                key="delete_project_button",
                type="primary",
                disabled=(confirm_name != project["name"]),
            ):
                credentials = google_auth.get_credentials() if google_auth.is_logged_in() else None
                project_store.delete_project(selected_id, credentials)
                st.success("削除しました。")
                _go_to_list()
                st.rerun()
        else:
            st.caption(
                "テストで作成した案件など、今後使わない案件はここで非表示にできます"
                "（写真・見積書・原価データなどは削除されず、一覧・会計画面から見えなくなるだけです）。"
            )
            if st.button("この案件を非表示にする", key="archive_project_button"):
                project_store.archive_project(selected_id)
                st.success("非表示にしました。")
                st.rerun()

    with tab2:
        if not google_auth.is_logged_in():
            st.caption(
                "⚠️ Googleにログインしていません。ログインしないまま資料をアップロードすると、"
                "サーバー再起動時に消えてしまう可能性があります。"
            )
        if "doc_uploader_counter" not in st.session_state:
            st.session_state["doc_uploader_counter"] = 0
        doc_uploader_key = f"doc_uploader_{selected_id}_{st.session_state['doc_uploader_counter']}"
        uploaded_docs = st.file_uploader(
            "図面・契約書などをアップロード（PDF・画像、複数可）",
            type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key=doc_uploader_key,
        )
        if uploaded_docs:
            for f in uploaded_docs:
                project_store.add_document(selected_id, f.name, f.getvalue())
            st.session_state["doc_uploader_counter"] += 1
            st.rerun()

        documents = project_store.get_project(selected_id).get("documents", [])
        if documents:
            for i, doc in enumerate(documents):
                col_doc, col_dl = st.columns([4, 1])
                with col_doc:
                    st.write(f"- {doc['filename']}（アップロード日時: {doc['uploaded_at']}）")
                with col_dl:
                    doc_bytes = project_store.get_file_bytes(doc)
                    if doc_bytes:
                        st.download_button(
                            "ダウンロード",
                            doc_bytes,
                            file_name=doc["filename"],
                            key=f"dl_doc_{selected_id}_{i}",
                        )
                    else:
                        st.caption("読み込めません")
        else:
            st.caption("まだ資料がアップロードされていません。")

    with tab3:
        schedule_spreadsheet_id = project.get("schedule_spreadsheet_id")
        if schedule_spreadsheet_id:
            st.markdown(
                f'<iframe src="{sheets.spreadsheet_url(schedule_spreadsheet_id)}" '
                'style="width:100%; height:70vh; border:none;"></iframe>',
                unsafe_allow_html=True,
            )
        else:
            st.info("この案件にはまだ工程表（スプレッドシート）が連携されていません。")
            st.caption(
                "「工程表」ページで作成したスプレッドシートのURLまたはIDを入力すると、"
                "ここに表示されるようになります。"
            )
            schedule_link_input = st.text_input(
                "工程表スプレッドシートのURLまたはID",
                key=f"schedule_link_input_{selected_id}",
            )
            if st.button("連携する", key="link_schedule_button"):
                if schedule_link_input.strip():
                    new_schedule_id = _extract_spreadsheet_id(schedule_link_input.strip())
                    project_store.set_schedule_spreadsheet_id(selected_id, new_schedule_id)
                    st.success("工程表を連携しました。")
                    st.rerun()
                else:
                    st.error("URLまたはIDを入力してください。")

    with tab4:
        if not google_auth.is_logged_in():
            st.caption(
                "⚠️ Googleにログインしていません。ログインしないまま写真をアップロードすると、"
                "サーバー再起動時に消えてしまう可能性があります。"
            )
        phase = st.selectbox("撮影時期", PHASES, key="photo_phase_select")
        if "photo_uploader_counter" not in st.session_state:
            st.session_state["photo_uploader_counter"] = 0
        photo_uploader_key = (
            f"photo_uploader_{selected_id}_{st.session_state['photo_uploader_counter']}"
        )
        uploaded_photos = st.file_uploader(
            "現場写真をアップロード（複数可）",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key=photo_uploader_key,
        )
        if uploaded_photos:
            for f in uploaded_photos:
                project_store.add_photo(selected_id, f.name, f.getvalue(), phase)
            st.session_state["photo_uploader_counter"] += 1
            st.rerun()

        photos = project_store.get_project(selected_id).get("photos", [])
        if photos:
            for phase_name in PHASES:
                phase_photos = [p for p in photos if p["phase"] == phase_name]
                if not phase_photos:
                    continue
                st.subheader(phase_name)
                cols = st.columns(4)
                for i, ph in enumerate(phase_photos):
                    with cols[i % 4]:
                        photo_bytes = project_store.get_photo_display_bytes(ph)
                        if photo_bytes:
                            st.image(photo_bytes, caption=ph["filename"])
                        else:
                            st.warning(f"{ph['filename']} を読み込めませんでした。")
        else:
            st.caption("まだ写真がアップロードされていません。")

    with tab5:
        current_spreadsheet_id = project.get("spreadsheet_id")
        if current_spreadsheet_id:
            st.markdown(
                f'<iframe src="{sheets.spreadsheet_url(current_spreadsheet_id)}" '
                'style="width:100%; height:70vh; border:none;"></iframe>',
                unsafe_allow_html=True,
            )
        else:
            st.info("この案件にはまだ見積書が作成されていません。")
            if not google_auth.is_logged_in():
                st.link_button("Googleでログイン", google_auth.get_login_url())
                st.caption("見積書を作成するには、Googleアカウントでログインしてください。")
            elif st.button("この案件の見積書を作成する", key="create_estimate_from_project_button"):
                with st.spinner("スプレッドシートを作成しています..."):
                    try:
                        new_id = sheets.create_estimate_spreadsheet(
                            project["name"], google_auth.get_credentials()
                        )
                        project_store.set_spreadsheet_id(selected_id, new_id)
                        st.success("見積書を作成しました。")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"作成に失敗しました: {exc}")

# チャットのトグル・パネルは、ページ固有のウィジェット（一覧のフィルターや詳細の
# タブなど）をすべて生成し終えたあとに呼び出す。先に呼び出すと、チャットの開閉
# ボタンが押されたときのst.rerun()がそれらのウィジェットに到達する前にスクリプトを
# 打ち切ってしまい、選択状態がsession_stateから失われてしまう不具合があったため。
show_chat_toggle()
show_chat_panel(category="案件管理")
