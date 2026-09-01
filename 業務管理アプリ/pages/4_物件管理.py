"""物件データベース画面。顧客データベースに登録済みの顧客に紐づけて、
物件（マンション・戸建てなど）を複数登録・検索・編集・削除できます。

案件管理と同じく、外観画像を背景にしたカード形式で一覧表示する。
"""

from __future__ import annotations

import base64
import html
import io

import streamlit as st
from PIL import Image

import auth_gate
import google_auth
import property_store
from chat import show_chat_panel, show_chat_toggle
from db import get_all_customers, get_customer, init_db
from layout import APP_ICON_PATH, show_header

st.set_page_config(page_title="物件管理", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

init_db()

CARD_FALLBACK_BG = "#1a1311"
OFFICE_FILTER_ALL = "すべて"
OFFICE_FILTER_OPTIONS = [OFFICE_FILTER_ALL] + property_store.OFFICE_OPTIONS


def _option_index(value: str, options: list[str]) -> int:
    return options.index(value) if value in options else 0


def _effective_address(prop: dict) -> str:
    """一覧表示用に、物件の住所（「顧客情報の住所と同じ」ならその顧客の現在の住所）を返す。"""
    if prop["address_type"] == property_store.ADDRESS_TYPE_SAME_AS_CUSTOMER:
        customer = get_customer(prop["customer_id"])
        return (customer["address"] or "") if customer else ""
    return prop["address"] or ""


@st.cache_data(show_spinner=False)
def _property_thumbnail_data_uri(property_id: int, updated_at: str, max_dim: int = 640) -> str | None:
    """物件カードの背景用に、外観画像を縮小してbase64データURIにして返す。

    元の画像をそのまま埋め込むとカード一覧のページが重くなるため、表示サイズに
    合わせて縮小してから埋め込む。キャッシュキーにupdated_atを含めることで、
    画像を差し替えたときに古いキャッシュが表示され続けないようにする。
    取得できなければNoneを返す。
    """
    data = property_store.get_property_image_bytes(property_id)
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


def _render_property_card(p: dict) -> None:
    """物件一覧の1枚のカード（外観画像を背景にしたスタイル）を描画する。"""
    bg_data_uri = _property_thumbnail_data_uri(p["id"], p["updated_at"]) if p.get("image") else None

    if bg_data_uri:
        bg_style = (
            "background-image: linear-gradient(rgba(20,24,33,0.75), rgba(20,24,33,0.75)), "
            f"url('{bg_data_uri}'); background-size: cover; background-position: center;"
        )
    else:
        bg_style = f"background-color: {CARD_FALLBACK_BG};"

    name = html.escape(p["name"])
    customer = html.escape(p.get("customer_name") or "未設定")
    address = html.escape(_effective_address(p) or "未設定")
    property_type = html.escape(p.get("property_type") or "未設定")
    office = html.escape(p.get("office") or "未設定")
    staff = html.escape(p.get("staff") or "未設定")

    st.markdown(
        f"""
        <div style="{bg_style} border-radius: 12px; box-shadow: 0 4px 14px rgba(0,0,0,0.25);
                    padding: 16px; min-height: 190px; display: flex; flex-direction: column;
                    justify-content: space-between; margin-bottom: 1rem;">
            <div>
                <span style="display:inline-block; background:#6b7280; color:#ffffff;
                             font-size:12px; font-weight:600; padding:3px 10px; border-radius:999px;
                             margin-bottom:10px;">{property_type}</span>
                <span style="display:inline-block; background:#374151; color:#ffffff;
                             font-size:12px; font-weight:600; padding:3px 10px; border-radius:999px;
                             margin-bottom:10px; margin-left:6px;">{office}</span>
                <div style="color:#ffffff; font-weight:700; font-size:17px; line-height:1.4;
                            text-shadow: 0 1px 3px rgba(0,0,0,0.6);">{name}</div>
            </div>
            <div style="color:#e5e5e5; font-size:12.5px; line-height:1.7;
                        text-shadow: 0 1px 2px rgba(0,0,0,0.6);">
                顧客名: {customer}<br>
                住所: {address}<br>
                自社担当者: {staff}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


show_header()
st.title("物件管理")
st.caption("顧客データベースに登録済みの顧客に紐づけて、物件を複数登録できます。")

customers = get_all_customers()

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

            st.markdown("##### 自社情報")
            col3, col4 = st.columns(2)
            with col3:
                office = st.selectbox("自社支社 *", options=property_store.OFFICE_OPTIONS)
            with col4:
                staff = st.selectbox("自社担当者 *", options=property_store.STAFF_OPTIONS)

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
                        property_type, address_type, address.strip(), office, staff, memo.strip(),
                    )
                    if image_file is not None:
                        property_store.set_property_image(
                            new_property["id"], image_file.name, image_file.getvalue()
                        )
                    st.success(f"「{name}」を登録しました。")
                    st.rerun()

st.divider()

# --- 検索・絞り込み ---
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

office_filter = st.pills(
    "絞り込み（自社支社）",
    options=OFFICE_FILTER_OPTIONS,
    default=OFFICE_FILTER_ALL,
    key="property_office_filter",
)
if office_filter is None:
    office_filter = OFFICE_FILTER_ALL

if office_filter == OFFICE_FILTER_ALL:
    filtered_rows = rows
else:
    filtered_rows = [p for p in rows if p.get("office") == office_filter]

# あいうえお順（フリガナ未入力の場合は物件名）で表示する。
filtered_rows = sorted(filtered_rows, key=lambda r: r["kana"] or r["name"])

st.write(f"登録件数: {len(filtered_rows)} 件")

if not filtered_rows:
    st.info("該当する物件が見つかりません。")
else:
    cols = st.columns(3)
    for i, p in enumerate(filtered_rows):
        with cols[i % 3]:
            _render_property_card(p)

    st.divider()
    st.subheader("編集・削除")

    id_to_label = {r["id"]: f"{r['customer_name']} / {r['name']}" for r in filtered_rows}
    selected_id = st.selectbox(
        "編集・削除する物件を選択してください",
        options=list(id_to_label.keys()),
        format_func=lambda x: id_to_label[x],
    )

    if selected_id is not None:
        prop = next(r for r in filtered_rows if r["id"] == selected_id)

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
                e_property_type = st.selectbox(
                    "物件種別 *", options=property_store.PROPERTY_TYPE_OPTIONS,
                    index=_option_index(prop["property_type"], property_store.PROPERTY_TYPE_OPTIONS),
                )
            with col2:
                e_address_type = st.selectbox(
                    "物件住所種別 *", options=property_store.ADDRESS_TYPE_OPTIONS,
                    index=_option_index(prop["address_type"], property_store.ADDRESS_TYPE_OPTIONS),
                )
                e_address = st.text_input(
                    "物件住所（「新しい住所を入力」を選んだ場合のみ使用されます）",
                    value=prop["address"] or "",
                )

            st.markdown("##### 自社情報")
            col3, col4 = st.columns(2)
            with col3:
                e_office = st.selectbox(
                    "自社支社 *", options=property_store.OFFICE_OPTIONS,
                    index=_option_index(prop.get("office", ""), property_store.OFFICE_OPTIONS),
                )
            with col4:
                e_staff = st.selectbox(
                    "自社担当者 *", options=property_store.STAFF_OPTIONS,
                    index=_option_index(prop.get("staff", ""), property_store.STAFF_OPTIONS),
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
                        e_property_type, e_address_type, e_address.strip(), e_office, e_staff, e_memo.strip(),
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
