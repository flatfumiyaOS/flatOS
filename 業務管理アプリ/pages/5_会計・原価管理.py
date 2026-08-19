"""会計・原価管理画面。協力会社請求書の読み込み、現場別粗利、顧客請求、支払管理、収支ダッシュボードをまとめる。"""

from __future__ import annotations

import datetime
from collections import defaultdict

import altair as alt
import pandas as pd
import streamlit as st

import auth_gate
import billing_generator
import billing_store
import cost_store
import db
import google_auth
import project_store
import sheets
from cost_extractor import extract_invoice_info
from layout import APP_ICON_PATH, show_header

NEW_VENDOR_OPTION = "＋ 新しい業者名を入力"
UNSELECTED_VENDOR_OPTION = "（未選択）"

st.set_page_config(page_title="会計・原価管理", page_icon=str(APP_ICON_PATH), layout="wide")
auth_gate.require_password()

google_auth.handle_login_redirect()

show_header()
st.title("会計・原価管理")

projects = project_store.get_all_projects()
project_options = {p["id"]: p["name"] for p in projects}


def _project_label(project_id: int) -> str:
    return project_options.get(project_id, "（不明な案件）")


def _yen(amount: int) -> str:
    """表（st.dataframe）に金額を表示するとき用に、桁区切りカンマを付けた文字列にする。"""
    return f"{amount:,}"


def _parse_date(value: str) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _read_estimate_total(spreadsheet_id: str) -> int | None:
    """見積書スプレッドシート（御見積書）の「合計」金額（税込）を読み取る。見つからなければNone。"""
    try:
        values = sheets.read_range(spreadsheet_id, "御見積書", "E1:F100")
    except Exception:
        return None
    for row in values:
        if row and row[0] == "合計" and len(row) > 1 and row[1]:
            try:
                return int(str(row[1]).replace(",", "").replace("円", "").strip())
            except ValueError:
                return None
    return None


def _project_revenue(project: dict) -> int:
    """案件の売上を返す。顧客請求の合計があればそれを使い、無ければ見積書の合計金額を使う。"""
    billings = billing_store.get_billings_for_project(project["id"])
    if billings:
        return sum(b["amount"] for b in billings)
    spreadsheet_id = project.get("spreadsheet_id")
    if spreadsheet_id:
        total = _read_estimate_total(spreadsheet_id)
        if total is not None:
            return total
    return 0


# 収支ダッシュボードのグラフで使う色（売上・原価・粗利の3系列を固定の順番・配色にする）
FINANCE_METRICS = ["売上", "原価", "粗利"]
FINANCE_COLORS = {"売上": "#2a78d6", "原価": "#eb6834", "粗利": "#1baf7a"}


def _finance_trend_chart(rows: list[dict], period_field: str) -> alt.Chart:
    """月次・年次サマリー用の、売上・原価・粗利を並べたグループ棒グラフを作る。

    rows は {period_field: "2026-08", "revenue": int, "cost": int} のリスト
    （期間の昇順に並んでいる前提）。
    """
    long_rows = []
    for r in rows:
        revenue, cost = r["revenue"], r["cost"]
        for metric, value in (("売上", revenue), ("原価", cost), ("粗利", revenue - cost)):
            long_rows.append({period_field: r[period_field], "指標": metric, "金額": value})
    df = pd.DataFrame(long_rows)
    periods = [r[period_field] for r in rows]

    chart = (
        alt.Chart(df)
        .mark_bar(size=16, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X(
                f"{period_field}:O",
                title=None,
                sort=periods,
                axis=alt.Axis(labelColor="#52514e", domainColor="#c3c2b7", tickColor="#c3c2b7", grid=False),
            ),
            y=alt.Y(
                "金額:Q",
                title=None,
                axis=alt.Axis(
                    format=",.0f",
                    labelColor="#898781",
                    tickColor="#e1e0d9",
                    gridColor="#e1e0d9",
                    domain=False,
                    tickCount=5,
                ),
            ),
            xOffset=alt.XOffset("指標:N", sort=FINANCE_METRICS),
            color=alt.Color(
                "指標:N",
                sort=FINANCE_METRICS,
                scale=alt.Scale(domain=FINANCE_METRICS, range=[FINANCE_COLORS[m] for m in FINANCE_METRICS]),
                legend=alt.Legend(title=None, orient="top", symbolType="circle"),
            ),
            tooltip=[
                alt.Tooltip(f"{period_field}:O", title=period_field),
                alt.Tooltip("指標:N", title="項目"),
                alt.Tooltip("金額:Q", title="金額", format=",.0f"),
            ],
        )
        .properties(height=280)
        .configure_view(strokeWidth=0)
        .configure_axis(labelFontSize=11)
    )
    return chart


tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "📄 原価入力（請求書・レシート AI-OCR）",
        "📊 現場別 粗利管理",
        "🧾 顧客請求・領収書発行",
        "💳 支払管理（協力会社別）",
        "📈 収支ダッシュボード",
    ]
)

with tab1:
    st.subheader("原価の読み込み（協力会社請求書・レシート）")

    if not projects:
        st.info("先に「案件管理」で案件を登録してください。")

    entry_kind_label = st.radio(
        "読み込む書類の種類",
        options=["協力会社からの請求書", "レシート（ホームセンター等での材料購入）"],
        key="cost_entry_kind",
        horizontal=True,
    )
    is_receipt = entry_kind_label.startswith("レシート")
    document_kind = "receipt" if is_receipt else "invoice"
    category = cost_store.CATEGORY_MATERIAL if is_receipt else cost_store.CATEGORY_SUBCONTRACT

    uploaded_invoice = st.file_uploader(
        "レシートの写真をアップロード（PDF・画像）" if is_receipt else "請求書をアップロード（PDF・画像）",
        type=["pdf", "png", "jpg", "jpeg"],
        key="cost_invoice_uploader",
    )

    if st.toggle("原価を手入力", key="show_manual_cost_entry"):
        if not projects:
            st.info("先に「案件管理」で案件を登録してください。")
        else:
            with st.form("manual_cost_form", clear_on_submit=True):
                manual_project_id = st.selectbox(
                    "案件",
                    options=list(project_options.keys()),
                    format_func=_project_label,
                    key="manual_cost_project_select",
                )
                manual_category = st.selectbox(
                    "区分",
                    options=[cost_store.CATEGORY_SUBCONTRACT, cost_store.CATEGORY_MATERIAL],
                    key="manual_cost_category_select",
                )
                manual_vendor_rows = db.get_all_vendors()
                manual_vendor_names = [v["name"] for v in manual_vendor_rows]
                manual_vendor_choice = st.selectbox(
                    "会社を選択",
                    options=[UNSELECTED_VENDOR_OPTION] + manual_vendor_names + [NEW_VENDOR_OPTION],
                    key="manual_cost_vendor_select",
                )
                manual_vendor_name_new = ""
                manual_save_new_vendor = False
                if manual_vendor_choice == NEW_VENDOR_OPTION:
                    manual_vendor_name_new = st.text_input("業者名・店舗名を入力", key="manual_cost_vendor_new")
                    manual_save_new_vendor = st.checkbox(
                        "この業者を協力会社データベースに登録する",
                        value=True,
                        key="manual_cost_vendor_save_new",
                    )
                manual_amount = st.number_input(
                    "金額（税込）", min_value=0, step=1000, key="manual_cost_amount"
                )
                manual_content = st.text_input("内容（工種・購入内容など）", key="manual_cost_content")
                col_manual_date, col_manual_due = st.columns(2)
                with col_manual_date:
                    manual_invoice_date = st.date_input(
                        "請求日・購入日", value=datetime.date.today(), key="manual_cost_invoice_date"
                    )
                with col_manual_due:
                    manual_due_date = st.date_input(
                        "支払期限", value=datetime.date.today(), key="manual_cost_due_date"
                    )

                if st.form_submit_button("原価を登録", type="primary"):
                    if manual_amount <= 0:
                        st.error("金額を入力してください。")
                    else:
                        if manual_vendor_choice == UNSELECTED_VENDOR_OPTION:
                            manual_vendor_name = ""
                        elif manual_vendor_choice == NEW_VENDOR_OPTION:
                            manual_vendor_name = manual_vendor_name_new.strip()
                        else:
                            manual_vendor_name = manual_vendor_choice

                        if (
                            manual_vendor_choice == NEW_VENDOR_OPTION
                            and manual_save_new_vendor
                            and manual_vendor_name
                            and manual_vendor_name not in manual_vendor_names
                        ):
                            db.add_vendor(manual_vendor_name, "", "", "", "", "")

                        cost_store.add_cost(
                            project_id=manual_project_id,
                            project_name=_project_label(manual_project_id),
                            vendor_name=manual_vendor_name,
                            amount_tax_included=int(manual_amount),
                            amount_tax_excluded=int(manual_amount),
                            work_type=manual_content.strip(),
                            invoice_date=manual_invoice_date.isoformat(),
                            payment_month=manual_due_date.strftime("%Y-%m"),
                            category=manual_category,
                        )
                        st.success("原価データとして登録しました。")
                        st.rerun()

    st.divider()

    if uploaded_invoice is not None and st.button("AIで読み取る", key="cost_ocr_button"):
        with st.spinner("読み取っています..."):
            try:
                media_type = uploaded_invoice.type or "application/octet-stream"
                result = extract_invoice_info(uploaded_invoice.getvalue(), media_type, document_kind)
                st.session_state["cost_ocr_result"] = result
                st.session_state["cost_ocr_file_bytes"] = uploaded_invoice.getvalue()
                st.session_state["cost_ocr_file_name"] = uploaded_invoice.name
                st.session_state["cost_ocr_category"] = category
            except Exception as exc:
                st.error(f"読み取りに失敗しました: {exc}")

    result = st.session_state.get("cost_ocr_result")
    if result and projects:
        # 読み取り結果は登録するまでセッションに保持されるため、読み取り時点の区分を使う
        # （読み取り後にラジオボタンを切り替えても、登録される区分がぶれないようにする）。
        category = st.session_state.get("cost_ocr_category", category)
        is_receipt = category == cost_store.CATEGORY_MATERIAL
        st.markdown("#### 読み取り結果（内容を確認・修正してください）")

        vendor_rows = db.get_all_vendors()
        vendor_names = [v["name"] for v in vendor_rows]
        ocr_vendor_name = result.get("vendor_name", "")
        vendor_options = vendor_names + [NEW_VENDOR_OPTION]
        default_vendor_index = (
            vendor_names.index(ocr_vendor_name) if ocr_vendor_name in vendor_names else len(vendor_names)
        )
        vendor_choice = st.selectbox(
            "購入した店舗名" if is_receipt else "請求元業者名",
            options=vendor_options,
            index=default_vendor_index,
            key="cost_vendor_select",
        )
        save_new_vendor = False
        if vendor_choice == NEW_VENDOR_OPTION:
            vendor_name = st.text_input(
                "店舗名を入力" if is_receipt else "業者名を入力", value=ocr_vendor_name, key="cost_vendor_name_new"
            )
            save_new_vendor = st.checkbox(
                "この業者を協力会社データベースに登録する（次回から選択できるようになります）",
                value=not is_receipt,
                key="cost_vendor_save_new",
            )
        else:
            vendor_name = vendor_choice
        col_incl, col_excl = st.columns(2)
        with col_incl:
            amount_incl = st.number_input(
                "金額（税込）" if is_receipt else "請求金額（税込）",
                min_value=0,
                value=int(result.get("amount_tax_included") or 0),
                step=1000,
                key="cost_amount_incl",
            )
        with col_excl:
            amount_excl = st.number_input(
                "金額（税抜）" if is_receipt else "請求金額（税抜）",
                min_value=0,
                value=int(result.get("amount_tax_excluded") or 0),
                step=1000,
                key="cost_amount_excl",
            )
        work_type = st.text_input(
            "購入内容" if is_receipt else "工種・内容", value=result.get("work_type", ""), key="cost_work_type"
        )

        hint = (result.get("suggested_project_hint") or "").strip()
        guessed_id = None
        if hint:
            for p in projects:
                if hint in p["name"] or (p.get("address") and hint in p["address"]):
                    guessed_id = p["id"]
                    break

        project_select_options = list(project_options.keys())
        default_index = (
            project_select_options.index(guessed_id) if guessed_id in project_select_options else 0
        )
        selected_project_id = st.selectbox(
            "該当案件",
            options=project_select_options,
            index=default_index,
            format_func=_project_label,
            key="cost_project_select",
        )
        if guessed_id:
            st.caption(
                f"請求書の内容から「{_project_label(guessed_id)}」の可能性があると判断しました（要確認）。"
            )

        col_date, col_month = st.columns(2)
        with col_date:
            invoice_date_value = st.date_input(
                "購入日" if is_receipt else "請求日",
                value=_parse_date(result.get("invoice_date", "")) or datetime.date.today(),
                key="cost_invoice_date",
            )
        with col_month:
            payment_month_value = st.text_input(
                "支払月（YYYY-MM）",
                value=invoice_date_value.strftime("%Y-%m"),
                key="cost_payment_month",
            )

        if st.button("登録", key="cost_register_button", type="primary"):
            if not vendor_name.strip():
                st.error("店舗名を入力してください。" if is_receipt else "業者名を入力してください。")
            else:
                if save_new_vendor and vendor_name.strip() not in vendor_names:
                    db.add_vendor(vendor_name.strip(), "", "", "", "", "")
                cost_store.add_cost(
                    project_id=selected_project_id,
                    project_name=_project_label(selected_project_id),
                    vendor_name=vendor_name.strip(),
                    amount_tax_included=int(amount_incl),
                    amount_tax_excluded=int(amount_excl),
                    work_type=work_type,
                    invoice_date=invoice_date_value.isoformat(),
                    payment_month=payment_month_value.strip(),
                    file_bytes=st.session_state.get("cost_ocr_file_bytes"),
                    file_name=st.session_state.get("cost_ocr_file_name"),
                    category=category,
                )
                st.success("原価データとして登録しました。")
                for key in ("cost_ocr_result", "cost_ocr_file_bytes", "cost_ocr_file_name", "cost_ocr_category"):
                    st.session_state.pop(key, None)
                st.rerun()

    st.divider()
    st.markdown("#### 登録済みの原価データ（請求書・レシート）")
    all_costs = cost_store.get_all_costs()
    if all_costs:
        st.dataframe(
            [
                {
                    "区分": c.get("category", cost_store.CATEGORY_SUBCONTRACT),
                    "案件": c["project_name"],
                    "業者名・店舗名": c["vendor_name"],
                    "内容": c["work_type"],
                    "税込金額": _yen(c["amount_tax_included"]),
                    "日付": c["invoice_date"],
                    "支払月": c["payment_month"],
                    "支払": "済" if c["paid"] else "未",
                }
                for c in reversed(all_costs)
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("まだ登録された原価データはありません。")

    st.divider()
    st.markdown("#### 登録済みの原価データを訂正する")
    current_month = datetime.date.today().strftime("%Y-%m")
    this_month_costs = [c for c in all_costs if (c["invoice_date"] or "")[:7] == current_month]
    if not this_month_costs:
        st.caption("今月分の原価データはありません。")
    else:
        cost_id_to_label = {
            c["id"]: (
                f'{c["invoice_date"]} / {c["project_name"]} / '
                f'{c["vendor_name"] or "（未選択）"} / ¥{c["amount_tax_included"]:,}'
            )
            for c in this_month_costs
        }
        edit_cost_id = st.selectbox(
            "訂正する原価データを選択（今月分のみ表示）",
            options=list(cost_id_to_label.keys()),
            format_func=lambda x: cost_id_to_label[x],
            key="edit_cost_select",
        )
        edit_cost = next(c for c in this_month_costs if c["id"] == edit_cost_id)
        project_ids = list(project_options.keys())

        with st.form("edit_cost_form"):
            edit_project_id = st.selectbox(
                "案件",
                options=project_ids,
                index=project_ids.index(edit_cost["project_id"]) if edit_cost["project_id"] in project_ids else 0,
                format_func=_project_label,
                key="edit_cost_project_select",
            )
            edit_category = st.selectbox(
                "区分",
                options=[cost_store.CATEGORY_SUBCONTRACT, cost_store.CATEGORY_MATERIAL],
                index=0 if edit_cost.get("category", cost_store.CATEGORY_SUBCONTRACT) == cost_store.CATEGORY_SUBCONTRACT else 1,
                key="edit_cost_category_select",
            )
            edit_vendor_name = st.text_input(
                "会社名・店舗名（空欄可）", value=edit_cost["vendor_name"], key="edit_cost_vendor"
            )
            edit_amount = st.number_input(
                "金額（税込）",
                min_value=0,
                step=1000,
                value=int(edit_cost["amount_tax_included"]),
                key="edit_cost_amount",
            )
            edit_content = st.text_input(
                "内容（工種・購入内容など）", value=edit_cost["work_type"], key="edit_cost_content"
            )
            col_edit_date, col_edit_due = st.columns(2)
            with col_edit_date:
                edit_invoice_date = st.date_input(
                    "請求日・購入日",
                    value=_parse_date(edit_cost["invoice_date"]) or datetime.date.today(),
                    key="edit_cost_invoice_date",
                )
            with col_edit_due:
                edit_due_date = st.date_input(
                    "支払期限",
                    value=_parse_date(f'{edit_cost["payment_month"]}-01') or datetime.date.today(),
                    key="edit_cost_due_date",
                )

            if st.form_submit_button("更新する", type="primary"):
                if edit_amount <= 0:
                    st.error("金額を入力してください。")
                else:
                    cost_store.update_cost(
                        cost_id=edit_cost_id,
                        project_id=edit_project_id,
                        project_name=_project_label(edit_project_id),
                        vendor_name=edit_vendor_name.strip(),
                        amount_tax_included=int(edit_amount),
                        amount_tax_excluded=int(edit_amount),
                        work_type=edit_content.strip(),
                        invoice_date=edit_invoice_date.isoformat(),
                        payment_month=edit_due_date.strftime("%Y-%m"),
                        category=edit_category,
                    )
                    st.success("原価データを更新しました。")
                    st.rerun()

with tab2:
    st.subheader("現場別 粗利管理")
    if not projects:
        st.info("先に「案件管理」で案件を登録してください。")
    else:
        selected_id = st.selectbox(
            "案件を選択",
            options=list(project_options.keys()),
            format_func=_project_label,
            key="profit_project_select",
        )
        project = project_store.get_project(selected_id)
        revenue = _project_revenue(project)
        project_costs = cost_store.get_costs_for_project(selected_id)
        cost_total = sum(c["amount_tax_included"] for c in project_costs)
        subcontract_total = sum(
            c["amount_tax_included"] for c in project_costs
            if c.get("category", cost_store.CATEGORY_SUBCONTRACT) == cost_store.CATEGORY_SUBCONTRACT
        )
        material_total = sum(
            c["amount_tax_included"] for c in project_costs
            if c.get("category", cost_store.CATEGORY_SUBCONTRACT) == cost_store.CATEGORY_MATERIAL
        )
        gross_profit = revenue - cost_total
        gross_margin = (gross_profit / revenue * 100) if revenue else 0.0

        col1, col2, col3 = st.columns(3)
        col1.metric("売上高", f"¥{revenue:,}")
        col2.metric("粗利益", f"¥{gross_profit:,}")
        col3.metric("粗利率", f"{gross_margin:.1f}%")

        col4, col5, col6 = st.columns(3)
        col4.metric("発生原価合計", f"¥{cost_total:,}")
        col5.metric("　内・外注費", f"¥{subcontract_total:,}")
        col6.metric("　内・材料費", f"¥{material_total:,}")

        st.markdown("#### 費用内訳（協力会社・購入店舗ごと）")
        if project_costs:
            st.dataframe(
                [
                    {
                        "区分": c.get("category", cost_store.CATEGORY_SUBCONTRACT),
                        "業者名・店舗名": c["vendor_name"],
                        "内容": c["work_type"],
                        "税込金額": _yen(c["amount_tax_included"]),
                        "日付": c["invoice_date"],
                        "支払": "済" if c["paid"] else "未",
                    }
                    for c in project_costs
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("この案件に登録された原価データはまだありません。")

with tab3:
    st.subheader("顧客請求・領収書発行")

    if not projects:
        st.info("先に「案件管理」で案件を登録してください。")
    elif not google_auth.is_logged_in():
        st.link_button("Googleでログイン", google_auth.get_login_url())
        st.caption("見積書から請求書を新規作成するには、Googleアカウントでログインしてください。")
    else:
        projects_with_estimate = [p for p in projects if p.get("spreadsheet_id")]
        if not projects_with_estimate:
            st.info("見積書スプレッドシートが作成されている案件がありません。先に「見積書」ページで見積書を作成してください。")
        else:
            billing_project_id = st.selectbox(
                "案件",
                options=[p["id"] for p in projects_with_estimate],
                format_func=_project_label,
                key="billing_project_select",
            )
            ratio_percent = st.selectbox(
                "請求割合", options=[100, 50], format_func=lambda v: f"{v}%", key="billing_ratio_select"
            )

            if st.button("請求データを作成", key="billing_create_button", type="primary"):
                billing_project = project_store.get_project(billing_project_id)
                estimate_total = _read_estimate_total(billing_project["spreadsheet_id"])
                if estimate_total is None:
                    st.error(
                        "見積書スプレッドシートから合計金額を読み取れませんでした。"
                        "見積書に「合計」欄が入力されているかご確認ください。"
                    )
                else:
                    with st.spinner("請求書を作成しています..."):
                        try:
                            amount = round(estimate_total * ratio_percent / 100)
                            billing_date_value = datetime.date.today()
                            new_spreadsheet_id = billing_generator.create_invoice_from_estimate(
                                billing_project["spreadsheet_id"],
                                _project_label(billing_project_id),
                                billing_date_value,
                                google_auth.get_credentials(),
                            )
                            due_date_value = billing_generator.next_month_last_day(billing_date_value)
                            billing_store.add_billing(
                                project_id=billing_project_id,
                                project_name=_project_label(billing_project_id),
                                customer_name=(billing_project or {}).get("customer_name", ""),
                                ratio_percent=ratio_percent,
                                estimate_total=estimate_total,
                                amount=amount,
                                billing_date=billing_date_value.isoformat(),
                                due_date=due_date_value.isoformat(),
                                spreadsheet_id=new_spreadsheet_id,
                            )
                            st.success(f"請求書を作成しました（請求金額: ¥{amount:,}）。")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"作成に失敗しました: {exc}")

    st.divider()
    st.markdown("#### 請求一覧")
    all_billings = billing_store.get_all_billings()
    if not all_billings:
        st.caption("まだ請求データがありません。")
    else:
        for b in reversed(all_billings):
            with st.container(border=True):
                col_info, col_status, col_open = st.columns([3, 1, 1])
                with col_info:
                    ratio_label = f"{b['ratio_percent']}%" if b.get("ratio_percent") else "-"
                    st.write(f"**{b['project_name']}** ／ 請求割合 {ratio_label} ／ ¥{b['amount']:,}")
                    st.caption(
                        f"宛先: {b['customer_name']} 様　"
                        f"請求日: {b['billing_date']}　支払期限: {b['due_date']}"
                    )
                with col_status:
                    new_status = st.selectbox(
                        "入金ステータス",
                        options=[billing_store.STATUS_UNPAID, billing_store.STATUS_PAID],
                        index=0 if b["status"] == billing_store.STATUS_UNPAID else 1,
                        key=f"billing_status_{b['id']}",
                        label_visibility="collapsed",
                    )
                    if new_status != b["status"]:
                        billing_store.set_status(b["id"], new_status)
                        st.rerun()
                with col_open:
                    if b.get("spreadsheet_id"):
                        st.link_button(
                            "開く", sheets.spreadsheet_url(b["spreadsheet_id"]), width="stretch"
                        )

with tab4:
    st.subheader("支払管理（業者・店舗別）")
    all_costs = cost_store.get_all_costs()
    if not all_costs:
        st.info("まだ原価データが登録されていません。")
    else:
        months = sorted({c["payment_month"] for c in all_costs if c["payment_month"]}, reverse=True)
        if not months:
            st.info("支払月が設定された原価データがありません。")
        else:
            selected_month = st.selectbox("支払月を選択", options=months, key="payment_month_select")
            month_costs = [c for c in all_costs if c["payment_month"] == selected_month]

            vendor_items: dict[str, list[dict]] = defaultdict(list)
            for c in month_costs:
                vendor_items[c["vendor_name"]].append(c)

            total_amount = sum(c["amount_tax_included"] for c in month_costs)
            st.metric(f"{selected_month} 振込予定額合計", f"¥{total_amount:,}")

            for vendor, items in vendor_items.items():
                vendor_total = sum(i["amount_tax_included"] for i in items)
                all_paid = all(i["paid"] for i in items)
                with st.container(border=True):
                    col_info, col_check = st.columns([3, 1])
                    with col_info:
                        st.write(f"**{vendor}**　¥{vendor_total:,}（{len(items)}件）")
                        for i in items:
                            i_category = i.get("category", cost_store.CATEGORY_SUBCONTRACT)
                            st.caption(
                                f"- [{i_category}] {i['project_name']} / {i['work_type']} / ¥{i['amount_tax_included']:,}"
                            )
                    with col_check:
                        checked = st.checkbox(
                            "支払完了", value=all_paid, key=f"vendor_paid_{selected_month}_{vendor}"
                        )
                        if checked != all_paid:
                            for i in items:
                                cost_store.set_paid(i["id"], checked)
                            st.rerun()

with tab5:
    st.subheader("収支ダッシュボード")

    all_costs = cost_store.get_all_costs()
    all_billings = billing_store.get_all_billings()

    total_revenue = 0
    project_summaries = []
    for p in projects:
        p_revenue = _project_revenue(p)
        p_cost_total = sum(c["amount_tax_included"] for c in cost_store.get_costs_for_project(p["id"]))
        p_profit = p_revenue - p_cost_total
        p_margin = (p_profit / p_revenue * 100) if p_revenue else 0.0
        total_revenue += p_revenue
        project_summaries.append(
            {
                "案件": p["name"],
                "売上": _yen(p_revenue),
                "原価": _yen(p_cost_total),
                "粗利": _yen(p_profit),
                "粗利率(%)": round(p_margin, 1),
            }
        )

    total_cost = sum(c["amount_tax_included"] for c in all_costs)
    total_profit = total_revenue - total_cost

    col1, col2, col3 = st.columns(3)
    col1.metric("全案件 売上合計", f"¥{total_revenue:,}")
    col2.metric("全案件 原価合計", f"¥{total_cost:,}")
    col3.metric("全案件 粗利合計", f"¥{total_profit:,}")

    st.markdown("#### 案件別 利益率ランキング")
    if project_summaries:
        ranking = sorted(project_summaries, key=lambda r: r["粗利率(%)"], reverse=True)
        st.dataframe(ranking, width="stretch", hide_index=True)
    else:
        st.caption("案件がまだ登録されていません。")

    st.markdown("#### 未回収売掛金一覧（未入金の顧客請求）")
    unpaid = [b for b in all_billings if b["status"] == billing_store.STATUS_UNPAID]
    if unpaid:
        st.dataframe(
            [
                {
                    "案件": b["project_name"],
                    "顧客": b["customer_name"],
                    "請求割合": f"{b['ratio_percent']}%" if b.get("ratio_percent") else "-",
                    "金額": _yen(b["amount"]),
                    "支払期限": b["due_date"],
                }
                for b in unpaid
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(f"未回収合計: ¥{sum(b['amount'] for b in unpaid):,}")
    else:
        st.caption("未入金の請求はありません。")

    monthly: dict[str, dict[str, int]] = defaultdict(lambda: {"revenue": 0, "cost": 0})
    for b in all_billings:
        month = b["billing_date"][:7] if b["billing_date"] else "不明"
        monthly[month]["revenue"] += b["amount"]
    for c in all_costs:
        month = c["invoice_date"][:7] if c["invoice_date"] else "不明"
        monthly[month]["cost"] += c["amount_tax_included"]

    st.markdown("#### 月次サマリー")
    if monthly:
        chart_months = sorted(m for m in monthly.keys() if m != "不明")[-12:]
        if chart_months:
            monthly_chart_rows = [
                {"月": m, "revenue": monthly[m]["revenue"], "cost": monthly[m]["cost"]}
                for m in chart_months
            ]
            st.altair_chart(_finance_trend_chart(monthly_chart_rows, "月"), use_container_width=True)
            st.caption("直近12ヶ月分を表示しています。")

        monthly_rows = []
        for month in sorted(monthly.keys(), reverse=True):
            rev = monthly[month]["revenue"]
            cost = monthly[month]["cost"]
            monthly_rows.append(
                {"月": month, "売上": _yen(rev), "原価": _yen(cost), "粗利": _yen(rev - cost)}
            )
        st.dataframe(monthly_rows, width="stretch", hide_index=True)
    else:
        st.caption("データがまだありません。")

    st.markdown("#### 年次サマリー")
    yearly: dict[str, dict[str, int]] = defaultdict(lambda: {"revenue": 0, "cost": 0})
    for month, vals in monthly.items():
        year = month[:4] if month != "不明" else "不明"
        yearly[year]["revenue"] += vals["revenue"]
        yearly[year]["cost"] += vals["cost"]
    if yearly:
        chart_years = sorted(y for y in yearly.keys() if y != "不明")
        if chart_years:
            yearly_chart_rows = [
                {"年": y, "revenue": yearly[y]["revenue"], "cost": yearly[y]["cost"]}
                for y in chart_years
            ]
            st.altair_chart(_finance_trend_chart(yearly_chart_rows, "年"), use_container_width=True)

        yearly_rows = []
        for year in sorted(yearly.keys(), reverse=True):
            rev = yearly[year]["revenue"]
            cost = yearly[year]["cost"]
            yearly_rows.append(
                {"年": year, "売上": _yen(rev), "原価": _yen(cost), "粗利": _yen(rev - cost)}
            )
        st.dataframe(yearly_rows, width="stretch", hide_index=True)
    else:
        st.caption("データがまだありません。")
