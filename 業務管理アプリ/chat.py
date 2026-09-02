"""AIチャット機能の共通部品。st.chat_input / st.chat_message を使った会話の骨組み。"""

from __future__ import annotations

import base64
import json
import os

import streamlit as st
import streamlit.components.v1 as components

import project_store
import property_store
import schedule_store
from db import (
    add_customer,
    add_customer_contact,
    add_memory_note,
    add_vendor,
    get_all_customer_contacts,
    get_all_customers,
    get_all_documents,
    get_all_vendors,
    search_customer_contacts,
    search_customers,
    search_vendors,
    update_customer_contact_fields,
    update_customer_fields,
    update_vendor_fields,
    get_memory_notes,
)
from sheets import (
    TEMPLATE_SPREADSHEET_ID,
    delete_rows,
    list_sheet_names,
    read_cell,
    read_range,
    set_cell_color,
    set_column_width,
    set_date_format,
    set_row_height,
    write_cell,
    write_range,
)

try:
    import anthropic
except ImportError:
    anthropic = None

MODEL_NAME = "claude-sonnet-5"

# チャットのメモリー機能で使う分類。ページごとに対応するカテゴリを指定してもらう
# （見積書ページ以外はまだ存在しないが、今後の追加時に手直しが少なくて済むよう
# 先に確定しておく）。
MEMORY_CATEGORIES = ["見積書", "請求書", "契約書", "工程表", "案件管理"]

# 現在表示中のスプレッドシート（見積書・工程表など）を操作するためのツール定義。
SHEET_TOOLS = [
    {
        "name": "list_sheet_names",
        "description": "現在表示中のスプレッドシート内のシート（タブ）名の一覧を取得する。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_sheet_cell",
        "description": "現在表示中のスプレッドシートの指定したシート・セルの値を読み取る。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "cell": {"type": "string", "description": "セル位置（例: 'A1'）"},
            },
            "required": ["sheet_name", "cell"],
        },
    },
    {
        "name": "write_sheet_cell",
        "description": "現在表示中のスプレッドシートの指定したシート・セルに値を書き込む。「=SUM(...)」のような数式もそのまま入力できる。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "cell": {"type": "string", "description": "セル位置（例: 'A1'）"},
                "value": {"type": "string", "description": "書き込む値"},
            },
            "required": ["sheet_name", "cell", "value"],
        },
    },
    {
        "name": "read_sheet_range",
        "description": "現在表示中のスプレッドシートの指定した範囲の値をまとめて読み取る。既存の行のレイアウトや記載例を確認する際に使う。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "range_a1": {"type": "string", "description": "範囲（例: 'A30:F45'）"},
            },
            "required": ["sheet_name", "range_a1"],
        },
    },
    {
        "name": "write_sheet_range",
        "description": (
            "現在表示中のスプレッドシートの指定した範囲に、複数のセルの値をまとめて書き込む。"
            "行ごとの値を2次元配列で指定する。「=SUM(...)」のような数式もそのまま入力できる。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "range_a1": {"type": "string", "description": "書き込む範囲（例: 'A33:F35'）"},
                "values": {
                    "type": "array",
                    "description": "行ごとの値の配列（2次元配列）。各行の要素数は範囲の列数と一致させる。",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": ["sheet_name", "range_a1", "values"],
        },
    },
    {
        "name": "delete_sheet_rows",
        "description": "現在表示中のスプレッドシートの指定した行範囲を削除する。明細の行数に応じて不要なテンプレートの行を減らす際に使う。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "start_row": {"type": "integer", "description": "削除する開始行（1始まり）"},
                "end_row": {"type": "integer", "description": "削除する終了行（1始まり、この行を含む）"},
            },
            "required": ["sheet_name", "start_row", "end_row"],
        },
    },
    {
        "name": "set_sheet_column_width",
        "description": "スプレッドシートの指定した列範囲の幅をピクセル単位で設定する（セルの値ではなく列の書式を変更する）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "start_col": {
                    "type": "integer",
                    "description": "開始列（1始まり。A=1、B=2、C=3...）",
                },
                "end_col": {"type": "integer", "description": "終了列（1始まり、この列を含む）"},
                "width_px": {"type": "integer", "description": "列幅（ピクセル）"},
            },
            "required": ["sheet_name", "start_col", "end_col", "width_px"],
        },
    },
    {
        "name": "set_sheet_date_format",
        "description": (
            "スプレッドシートの指定したセル範囲に日付の表示形式を設定する"
            "（セルの値・数式自体は変更しない）。工程表の曜日行のように、"
            "日付セルを参照する数式（例: =B2）の表示形式を'ddd'（曜日表示）に"
            "揃えたい場合などに使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "start_row": {"type": "integer", "description": "開始行（1始まり）"},
                "end_row": {"type": "integer", "description": "終了行（1始まり、この行を含む）"},
                "start_col": {
                    "type": "integer",
                    "description": "開始列（1始まり。A=1、B=2、C=3...）",
                },
                "end_col": {"type": "integer", "description": "終了列（1始まり、この列を含む）"},
                "pattern": {
                    "type": "string",
                    "description": "表示形式（例: 'ddd'で曜日表示、'm/d'で月/日表示）",
                },
            },
            "required": ["sheet_name", "start_row", "end_row", "start_col", "end_col", "pattern"],
        },
    },
    {
        "name": "set_sheet_cell_color",
        "description": (
            "スプレッドシートの指定したセル範囲の背景色を設定する"
            "（セルの値・数式自体は変更しない）。土日祝の列を目立たせる、などに使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "start_row": {"type": "integer", "description": "開始行（1始まり）"},
                "end_row": {"type": "integer", "description": "終了行（1始まり、この行を含む）"},
                "start_col": {
                    "type": "integer",
                    "description": "開始列（1始まり。A=1、B=2、C=3...）",
                },
                "end_col": {"type": "integer", "description": "終了列（1始まり、この列を含む）"},
                "red": {"type": "number", "description": "赤成分（0〜1）"},
                "green": {"type": "number", "description": "緑成分（0〜1）"},
                "blue": {"type": "number", "description": "青成分（0〜1）"},
            },
            "required": ["sheet_name", "start_row", "end_row", "start_col", "end_col", "red", "green", "blue"],
        },
    },
    {
        "name": "set_sheet_row_height",
        "description": (
            "スプレッドシートの指定した行範囲の高さをピクセル単位で設定する"
            "（セルの値・数式自体は変更しない）。行の高さが周囲と揃っていない場合に使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sheet_name": {"type": "string", "description": "シート（タブ）名"},
                "start_row": {"type": "integer", "description": "開始行（1始まり）"},
                "end_row": {"type": "integer", "description": "終了行（1始まり、この行を含む）"},
                "height_px": {"type": "integer", "description": "行の高さ（ピクセル）"},
            },
            "required": ["sheet_name", "start_row", "end_row", "height_px"],
        },
    },
]

# アプリ本体のデータベース（顧客・顧客担当者・協力会社・案件・物件）を操作するための
# ツール定義。スプレッドシート操作とは別に、いつでも使えるようにする
# （見積書作成中に顧客の郵便番号を直すなど、ページをまたいだ依頼が多いため）。
# 「削除」はどの対象についても用意しない（CLAUDE.mdの方針上、データの削除は
# ユーザー本人の明示的な許可が必要なため。削除が必要な場合は該当ページの
# 削除確認つきUIを使うよう案内する）。

CUSTOMER_UPDATABLE_FIELDS = [
    "name", "kana", "entity_type", "honorific", "phone", "fax", "email",
    "postal_code", "address", "referrer", "memo",
]
CUSTOMER_FIELD_OPTIONS = {"entity_type": ["個人", "法人"], "honorific": ["様", "御中"]}

CONTACT_UPDATABLE_FIELDS = ["name", "kana", "honorific", "title", "email", "memo"]
CONTACT_FIELD_OPTIONS = {"honorific": ["様", "御中"]}

VENDOR_UPDATABLE_FIELDS = [
    "name", "kana", "phone", "email", "address", "memo", "honorific", "fax",
    "postal_code", "referrer", "quality_rating", "service_rating",
    "communication_rating", "it_literacy_rating",
]
VENDOR_FIELD_OPTIONS = {"honorific": ["様", "御中"]}

PROJECT_UPDATABLE_FIELDS = [
    "name", "customer_name", "address", "start_date", "end_date", "overview",
    "office", "staff", "payment_terms", "order_status", "billing_timing",
    "billing_due_date", "category1", "category2", "category3", "billing_status",
]
PROJECT_FIELD_OPTIONS = {
    "office": project_store.OFFICE_OPTIONS,
    "staff": project_store.STAFF_OPTIONS,
    "payment_terms": project_store.PAYMENT_TERMS_OPTIONS,
    "order_status": project_store.ORDER_STATUS_OPTIONS,
    "billing_timing": project_store.BILLING_TIMING_OPTIONS,
    "category1": project_store.CATEGORY1_OPTIONS,
    "category2": project_store.CATEGORY2_OPTIONS,
    "category3": project_store.CATEGORY3_OPTIONS,
    "billing_status": project_store.BILLING_STATUS_OPTIONS,
}

PROPERTY_UPDATABLE_FIELDS = ["name", "kana", "property_type", "address_type", "address", "office", "staff", "memo"]
PROPERTY_FIELD_OPTIONS = {
    "property_type": property_store.PROPERTY_TYPE_OPTIONS,
    "address_type": property_store.ADDRESS_TYPE_OPTIONS,
    "office": property_store.OFFICE_OPTIONS,
    "staff": property_store.STAFF_OPTIONS,
}

APP_DB_TOOLS = [
    {
        "name": "search_customers_db",
        "description": "顧客データベースを、顧客名・フリガナ・電話番号・メール・住所などのキーワードで検索する。キーワードを空文字にすると全件を返す。更新の前には必ずこれで対象のidを確認する。",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "検索キーワード（空文字なら全件）"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "update_customer_field",
        "description": "顧客データベースの、指定した1件の顧客の1項目だけを更新する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer", "description": "更新する顧客のid（search_customers_dbで確認する）"},
                "field": {"type": "string", "enum": CUSTOMER_UPDATABLE_FIELDS, "description": "更新する項目名"},
                "value": {"type": "string", "description": "新しい値"},
            },
            "required": ["customer_id", "field", "value"],
        },
    },
    {
        "name": "add_customer_db",
        "description": "顧客データベースに新しい顧客を登録する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "顧客名（必須）"},
                "kana": {"type": "string", "description": "フリガナ"},
                "entity_type": {"type": "string", "enum": ["個人", "法人"], "description": "個人か法人か（既定: 個人）"},
                "honorific": {"type": "string", "enum": ["様", "御中"], "description": "敬称（既定: 様）"},
                "phone": {"type": "string", "description": "TEL"},
                "fax": {"type": "string", "description": "FAX"},
                "email": {"type": "string", "description": "MAIL"},
                "postal_code": {"type": "string", "description": "郵便番号"},
                "address": {"type": "string", "description": "住所"},
                "referrer": {"type": "string", "description": "紹介者"},
                "memo": {"type": "string", "description": "備考"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_customer_contacts_db",
        "description": "顧客担当者データベースを、担当者名・フリガナ・役職・メール・顧客名などのキーワードで検索する。キーワードを空文字にすると全件を返す。",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "検索キーワード（空文字なら全件）"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "update_customer_contact_field",
        "description": "顧客担当者データベースの、指定した1件の担当者の1項目だけを更新する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "更新する担当者のid（search_customer_contacts_dbで確認する）"},
                "field": {"type": "string", "enum": CONTACT_UPDATABLE_FIELDS, "description": "更新する項目名"},
                "value": {"type": "string", "description": "新しい値"},
            },
            "required": ["contact_id", "field", "value"],
        },
    },
    {
        "name": "add_customer_contact_db",
        "description": "既存の顧客に、新しい顧客担当者を1名登録する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "紐付け先の顧客名（search_customers_dbで確認した正式名称）"},
                "name": {"type": "string", "description": "担当者名（必須）"},
                "kana": {"type": "string", "description": "フリガナ"},
                "honorific": {"type": "string", "enum": ["様", "御中"], "description": "敬称（既定: 様）"},
                "title": {"type": "string", "description": "役職"},
                "email": {"type": "string", "description": "MAIL"},
                "memo": {"type": "string", "description": "備考"},
            },
            "required": ["customer_name", "name"],
        },
    },
    {
        "name": "search_vendors_db",
        "description": "協力会社データベースを、会社名・フリガナ・電話番号・メール・住所などのキーワードで検索する。キーワードを空文字にすると全件を返す。",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "検索キーワード（空文字なら全件）"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "update_vendor_field",
        "description": "協力会社データベースの、指定した1件の協力会社の1項目だけを更新する（ご担当者欄は対象外。ご担当者の変更は協力会社ページから行うよう案内する）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "vendor_id": {"type": "integer", "description": "更新する協力会社のid（search_vendors_dbで確認する）"},
                "field": {"type": "string", "enum": VENDOR_UPDATABLE_FIELDS, "description": "更新する項目名"},
                "value": {"type": "string", "description": "新しい値"},
            },
            "required": ["vendor_id", "field", "value"],
        },
    },
    {
        "name": "add_vendor_db",
        "description": "協力会社データベースに新しい協力会社を登録する（ご担当者欄は登録できないので、必要なら協力会社ページから追加するよう案内する）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "会社名（必須）"},
                "kana": {"type": "string", "description": "フリガナ"},
                "honorific": {"type": "string", "enum": ["様", "御中"], "description": "敬称（既定: 様）"},
                "phone": {"type": "string", "description": "TEL"},
                "fax": {"type": "string", "description": "FAX"},
                "email": {"type": "string", "description": "MAIL"},
                "postal_code": {"type": "string", "description": "郵便番号"},
                "address": {"type": "string", "description": "住所"},
                "referrer": {"type": "string", "description": "紹介者"},
                "memo": {"type": "string", "description": "備考"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_projects_db",
        "description": "案件管理データベースを、案件名・顧客名に含まれるキーワードで検索する（非表示にした案件は対象外）。キーワードを空文字にすると全件を返す。",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "検索キーワード（空文字なら全件）"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "update_project_field",
        "description": "案件管理データベースの、指定した1件の案件の1項目だけを更新する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "更新する案件のid（search_projects_dbで確認する）"},
                "field": {"type": "string", "enum": PROJECT_UPDATABLE_FIELDS, "description": "更新する項目名"},
                "value": {"type": "string", "description": "新しい値"},
            },
            "required": ["project_id", "field", "value"],
        },
    },
    {
        "name": "add_project_db",
        "description": "案件管理データベースに、案件名だけを指定して新しい案件を登録する。他の項目（顧客名・自社支社・ステータスなど）は登録後にupdate_project_fieldで設定する。",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "案件名（必須）"}},
            "required": ["name"],
        },
    },
    {
        "name": "search_properties_db",
        "description": "物件管理データベースを、物件名・顧客名・住所に含まれるキーワードで検索する。キーワードを空文字にすると全件を返す。",
        "input_schema": {
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "検索キーワード（空文字なら全件）"}},
            "required": ["keyword"],
        },
    },
    {
        "name": "update_property_field",
        "description": "物件管理データベースの、指定した1件の物件の1項目だけを更新する（紐づく顧客・外観画像は対象外。変更が必要な場合は物件管理ページから行うよう案内する）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "property_id": {"type": "integer", "description": "更新する物件のid（search_properties_dbで確認する）"},
                "field": {"type": "string", "enum": PROPERTY_UPDATABLE_FIELDS, "description": "更新する項目名"},
                "value": {"type": "string", "description": "新しい値"},
            },
            "required": ["property_id", "field", "value"],
        },
    },
    {
        "name": "add_property_db",
        "description": "既存の顧客に紐づく新しい物件を登録する（外観画像は登録できないので、必要なら物件管理ページから追加するよう案内する）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "紐付け先の顧客名（search_customers_dbで確認した正式名称）"},
                "name": {"type": "string", "description": "物件名（必須）"},
                "kana": {"type": "string", "description": "フリガナ"},
                "property_type": {
                    "type": "string",
                    "enum": property_store.PROPERTY_TYPE_OPTIONS,
                    "description": f"物件種別（既定: {property_store.PROPERTY_TYPE_OPTIONS[0]}）",
                },
                "address_type": {
                    "type": "string",
                    "enum": property_store.ADDRESS_TYPE_OPTIONS,
                    "description": f"物件住所種別（既定: {property_store.ADDRESS_TYPE_SAME_AS_CUSTOMER}）",
                },
                "address": {"type": "string", "description": "物件住所（「新しい住所を入力」を選んだ場合のみ使用される）"},
                "office": {"type": "string", "enum": property_store.OFFICE_OPTIONS, "description": "自社支社"},
                "staff": {"type": "string", "enum": property_store.STAFF_OPTIONS, "description": "自社担当者"},
                "memo": {"type": "string", "description": "備考"},
            },
            "required": ["customer_name", "name"],
        },
    },
]

APP_DB_TOOL_NAMES = {tool["name"] for tool in APP_DB_TOOLS}


def _project_summary(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in ("photos", "documents", "cover_photo")}


def _property_summary(p: dict) -> dict:
    return {k: v for k, v in p.items() if k != "image"}


def _validate_field(allowed_fields: list[str], enum_options: dict, field: str, value: str) -> str | None:
    """更新項目名・値の妥当性を確認する。問題があればエラーメッセージ、無ければNoneを返す。"""
    if field not in allowed_fields:
        return f"「{field}」という項目は更新できません。指定できる項目: {', '.join(allowed_fields)}"
    options = enum_options.get(field)
    if options is not None and value not in options:
        return f"「{field}」に指定できる値は次のいずれかです: {', '.join(options)}"
    return None


def _find_customer_by_name(customer_name: str) -> dict | None:
    return next((dict(c) for c in get_all_customers() if c["name"] == customer_name), None)


def _run_db_tool(name: str, tool_input: dict) -> str:
    """顧客・顧客担当者・協力会社・案件・物件のデータベースを操作するツールを実行する。"""
    try:
        if name == "search_customers_db":
            rows = search_customers(tool_input.get("keyword", ""))
            return json.dumps([dict(r) for r in rows], ensure_ascii=False)
        if name == "update_customer_field":
            field, value = tool_input["field"], tool_input["value"]
            error = _validate_field(CUSTOMER_UPDATABLE_FIELDS, CUSTOMER_FIELD_OPTIONS, field, value)
            if error:
                return error
            update_customer_fields(tool_input["customer_id"], **{field: value})
            return "顧客情報を更新しました。"
        if name == "add_customer_db":
            add_customer(
                tool_input.get("name", "").strip(), tool_input.get("kana", ""),
                tool_input.get("honorific", "様"), tool_input.get("phone", ""),
                tool_input.get("fax", ""), tool_input.get("email", ""),
                tool_input.get("postal_code", ""), tool_input.get("address", ""),
                tool_input.get("referrer", ""), tool_input.get("memo", ""),
                entity_type=tool_input.get("entity_type", "個人"),
            )
            return "顧客を登録しました。"

        if name == "search_customer_contacts_db":
            rows = search_customer_contacts(tool_input.get("keyword", ""))
            return json.dumps([dict(r) for r in rows], ensure_ascii=False)
        if name == "update_customer_contact_field":
            field, value = tool_input["field"], tool_input["value"]
            error = _validate_field(CONTACT_UPDATABLE_FIELDS, CONTACT_FIELD_OPTIONS, field, value)
            if error:
                return error
            update_customer_contact_fields(tool_input["contact_id"], **{field: value})
            return "顧客担当者の情報を更新しました。"
        if name == "add_customer_contact_db":
            customer = _find_customer_by_name(tool_input["customer_name"])
            if customer is None:
                return f"「{tool_input['customer_name']}」という顧客が見つかりません。search_customers_dbで正しい顧客名を確認してください。"
            add_customer_contact(
                customer["id"], customer["name"],
                tool_input.get("name", "").strip(), tool_input.get("kana", ""),
                tool_input.get("honorific", "様"), tool_input.get("title", ""),
                tool_input.get("email", ""), tool_input.get("memo", ""),
            )
            return "顧客担当者を登録しました。"

        if name == "search_vendors_db":
            rows = search_vendors(tool_input.get("keyword", ""))
            return json.dumps([dict(r) for r in rows], ensure_ascii=False)
        if name == "update_vendor_field":
            field, value = tool_input["field"], tool_input["value"]
            error = _validate_field(VENDOR_UPDATABLE_FIELDS, VENDOR_FIELD_OPTIONS, field, value)
            if error:
                return error
            update_vendor_fields(tool_input["vendor_id"], **{field: value})
            return "協力会社の情報を更新しました。"
        if name == "add_vendor_db":
            add_vendor(
                tool_input.get("name", "").strip(), tool_input.get("kana", ""),
                tool_input.get("phone", ""), tool_input.get("email", ""),
                tool_input.get("address", ""), tool_input.get("memo", ""),
                honorific=tool_input.get("honorific", "様"), fax=tool_input.get("fax", ""),
                postal_code=tool_input.get("postal_code", ""), referrer=tool_input.get("referrer", ""),
            )
            return "協力会社を登録しました。"

        if name == "search_projects_db":
            keyword = tool_input.get("keyword", "").strip()
            projects = [p for p in project_store.get_all_projects() if not p.get("archived")]
            if keyword:
                projects = [
                    p for p in projects
                    if keyword in (p.get("name") or "") or keyword in (p.get("customer_name") or "")
                ]
            return json.dumps([_project_summary(p) for p in projects], ensure_ascii=False)
        if name == "update_project_field":
            field, value = tool_input["field"], tool_input["value"]
            error = _validate_field(PROJECT_UPDATABLE_FIELDS, PROJECT_FIELD_OPTIONS, field, value)
            if error:
                return error
            project_store.update_project_fields(tool_input["project_id"], **{field: value})
            return "案件情報を更新しました。"
        if name == "add_project_db":
            project = project_store.create_project(tool_input["name"].strip())
            return f"案件を登録しました（id: {project['id']}）。他の項目はupdate_project_fieldで設定できます。"

        if name == "search_properties_db":
            keyword = tool_input.get("keyword", "").strip()
            properties = property_store.get_all_properties()
            if keyword:
                properties = [
                    p for p in properties
                    if keyword in (p.get("name") or "") or keyword in (p.get("customer_name") or "")
                    or keyword in (p.get("address") or "")
                ]
            return json.dumps([_property_summary(p) for p in properties], ensure_ascii=False)
        if name == "update_property_field":
            field, value = tool_input["field"], tool_input["value"]
            error = _validate_field(PROPERTY_UPDATABLE_FIELDS, PROPERTY_FIELD_OPTIONS, field, value)
            if error:
                return error
            property_store.update_property_fields(tool_input["property_id"], **{field: value})
            return "物件情報を更新しました。"
        if name == "add_property_db":
            customer = _find_customer_by_name(tool_input["customer_name"])
            if customer is None:
                return f"「{tool_input['customer_name']}」という顧客が見つかりません。search_customers_dbで正しい顧客名を確認してください。"
            new_property = property_store.add_property(
                customer["id"], customer["name"],
                tool_input.get("name", "").strip(), tool_input.get("kana", ""),
                tool_input.get("property_type", property_store.PROPERTY_TYPE_OPTIONS[0]),
                tool_input.get("address_type", property_store.ADDRESS_TYPE_SAME_AS_CUSTOMER),
                tool_input.get("address", ""), tool_input.get("office", ""),
                tool_input.get("staff", ""), tool_input.get("memo", ""),
            )
            return f"物件を登録しました（id: {new_property['id']}）。外観画像は物件管理ページから登録してください。"

        return f"不明なツールです: {name}"
    except Exception as exc:  # noqa: BLE001 — ツール結果としてエラー内容をClaudeに返すため
        return f"エラーが発生しました: {exc}"


def _current_spreadsheet_id(category: str) -> str | None:
    """現在チャットや画面に表示しているスプレッドシートのIDを返す。カテゴリによって
    参照する場所が異なる（ページごとに別々のスプレッドシートを表示しているため）。

    - 見積書: 見積書ページで新しい案件のスプレッドシートを作成すると、そのIDが
      session_stateに保存され、以降はそちらが対象になる。未作成の場合はテンプレートを対象にする。
    - 工程表: 工程表ページで選択中の工程表のIDを、schedule_storeから引く。
      選択中のものが無ければNoneを返す（対象が無いことをそのまま伝える）。
    """
    if category == "工程表":
        schedule_id = st.session_state.get("selected_schedule_id")
        if schedule_id is None:
            return None
        schedule = schedule_store.get_schedule(schedule_id)
        return schedule["spreadsheet_id"] if schedule else None
    return st.session_state.get("current_spreadsheet_id", TEMPLATE_SPREADSHEET_ID)


def _run_sheet_tool(name: str, tool_input: dict, category: str) -> str:
    """スプレッドシート操作ツールを実行し、結果を文字列で返す。失敗時はエラー内容を返す。"""
    spreadsheet_id = _current_spreadsheet_id(category)
    if spreadsheet_id is None:
        return "現在表示中のスプレッドシートがありません。先に案件・工程表を選択または作成してください。"
    try:
        if name == "list_sheet_names":
            return "、".join(list_sheet_names(spreadsheet_id))
        if name == "read_sheet_cell":
            value = read_cell(spreadsheet_id, tool_input["sheet_name"], tool_input["cell"])
            return value if value else "(空欄)"
        if name == "write_sheet_cell":
            write_cell(
                spreadsheet_id, tool_input["sheet_name"], tool_input["cell"], tool_input["value"]
            )
            return "書き込みが完了しました。"
        if name == "read_sheet_range":
            values = read_range(spreadsheet_id, tool_input["sheet_name"], tool_input["range_a1"])
            return json.dumps(values, ensure_ascii=False)
        if name == "write_sheet_range":
            write_range(
                spreadsheet_id,
                tool_input["sheet_name"],
                tool_input["range_a1"],
                tool_input["values"],
            )
            return "書き込みが完了しました。"
        if name == "delete_sheet_rows":
            delete_rows(
                spreadsheet_id,
                tool_input["sheet_name"],
                tool_input["start_row"],
                tool_input["end_row"],
            )
            return "行を削除しました。"
        if name == "set_sheet_column_width":
            set_column_width(
                spreadsheet_id,
                tool_input["sheet_name"],
                tool_input["start_col"],
                tool_input["end_col"],
                tool_input["width_px"],
            )
            return "列幅を設定しました。"
        if name == "set_sheet_date_format":
            set_date_format(
                spreadsheet_id,
                tool_input["sheet_name"],
                tool_input["start_row"],
                tool_input["end_row"],
                tool_input["start_col"],
                tool_input["end_col"],
                tool_input["pattern"],
            )
            return "表示形式を設定しました。"
        if name == "set_sheet_cell_color":
            set_cell_color(
                spreadsheet_id,
                tool_input["sheet_name"],
                tool_input["start_row"],
                tool_input["end_row"],
                tool_input["start_col"],
                tool_input["end_col"],
                tool_input["red"],
                tool_input["green"],
                tool_input["blue"],
            )
            return "背景色を設定しました。"
        if name == "set_sheet_row_height":
            set_row_height(
                spreadsheet_id,
                tool_input["sheet_name"],
                tool_input["start_row"],
                tool_input["end_row"],
                tool_input["height_px"],
            )
            return "行の高さを設定しました。"
        return f"不明なツールです: {name}"
    except Exception as exc:  # noqa: BLE001 — ツール結果としてエラー内容をClaudeに返すため
        return f"エラーが発生しました: {exc}"


def _build_user_content(text: str, uploaded_file) -> str | list[dict]:
    """入力テキストと添付ファイル（PDF・画像）から、Claudeに送るメッセージ内容を作る。

    添付が無い場合はこれまで通り文字列のまま返す。
    """
    if uploaded_file is None:
        return text

    file_bytes = uploaded_file.getvalue()
    data_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    media_type = uploaded_file.type or "application/octet-stream"

    if media_type == "application/pdf":
        file_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64},
        }
    else:
        file_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data_b64},
        }

    content: list[dict] = [file_block]
    content.append({"type": "text", "text": text if text else "この図面を確認してください。"})
    return content


def _render_message_content(content: str | list[dict]) -> None:
    """会話履歴の1メッセージ分を表示する。添付ファイルがある場合はその旨も表示する。"""
    if isinstance(content, str):
        st.write(content)
        return

    for block in content:
        if block["type"] == "text":
            st.write(block["text"])
        elif block["type"] == "image":
            st.image(base64.b64decode(block["source"]["data"]))
        elif block["type"] == "document":
            st.caption("（PDFファイルが添付されています）")


def _get_api_key() -> str | None:
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


ESTIMATE_SKILL_PATH = os.path.join(
    os.path.dirname(__file__), "..", ".claude", "skills", "見積書作成", "SKILL.md"
)


def _load_estimate_skill_rules() -> str:
    """見積書作成スキル（`.claude/skills/見積書作成/SKILL.md`）の内容を読み込んで返す。

    見積書の明細入力ルールはこのスキルファイルで一元管理し、アプリ側にはハードコードしない。
    読み込めない場合は空文字を返す。
    """
    try:
        with open(ESTIMATE_SKILL_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


SCHEDULE_EDIT_RULES = """\
工程表スプレッドシートを編集するときは、次のルールを必ず守ってください。

- シートの構成: 1行目=タイトルと月ヘッダー（月をまたぐ場合は月ごとにセルを結合）、
  2行目=日付（m/d形式、B列以降）、3行目=曜日、4行目以降=工種ごとの行（各工種の
  担当作業を該当する日付の列に記載。2日以上かかる作業はセルが結合されている）。
- **3行目（曜日）のセルは、同じ列の2行目のセルを参照する数式（例: `=B2`）にしてください。
  「月」「火」のような曜日の文字列を直接書き込まないでください。** 数式にしておかないと、
  後で2行目の日付を変更したときに3行目の曜日がずれてしまいます。
- 日付・曜日の列を追加/移動/変更した場合は、3行目の該当範囲に対して
  `set_sheet_date_format`（pattern: "ddd"）を実行し、曜日として表示されるようにして
  ください。数式を書き込んだだけでは表示形式が引き継がれないことがあります。
- 4行目以降の作業内容のセルは、複数日にまたがる作業でセル結合されていることがあります。
  日付の列を追加・削除・入れ替えるような大きな変更をする場合は、既存のセル結合が
  崩れる可能性があるため、事前に`read_sheet_range`で現在の内容を確認してから、
  慎重に対応してください。
- セルの背景色を変える場合は`set_sheet_cell_color`を使う（RGBは0〜1の割合）。
  「薄い赤」は F4CCCC（red=0.957, green=0.8, blue=0.8）を使う。土日・祝日の列を
  目立たせる依頼では、2行目（日付）から、その時点の工種の最終行までの範囲を
  対象にする（見出し行だけ、あるいは1行だけに色を塗らない）。既存の他の行の
  色（工種ごとのパステルカラー）を上書きしないよう、対象の列だけに絞ること。

【土日・祝日を休みにする依頼の扱い方（重要）】
- 工期は「実働日数」で考える。土日・祝日は稼働日に数えない。
- 「土日（祝日）を休みにして」と依頼された場合は、次の手順で対応する。
  1. 土日・祝日の列を特定する（3行目の曜日、および一般的な祝日を踏まえて判断する。
     不明な祝日があれば確認する）。
  2. 各工種（行）ごとに、土日・祝日の列に入っている作業内容を、その工種の
     直後の稼働日（次の土日・祝日でない列。多くの場合は翌週の月曜）に移動する。
     すでにその稼働日に別の作業が入っている場合は、その工種の後続の作業も
     まとめて1日ずつ後ろにずらす（同じ行の中で予定が重ならないようにする）。
  3. 行ごとに必要な分だけ、2行目・3行目の日付列を右側に延長する（土日・祝日も
     含めて連続した日付で埋め、空白の日を作らない）。延長する日数は、その工期で
     実際に必要な分だけにとどめ、余分に延長しない。
  4. 土日・祝日の列自体は残したまま、その列の作業内容セルは空にする。
- **移動するのは、既存のセルに実際に入っている作業内容だけ。** 依頼されていない
  新しい工種・作業内容を作り出さないこと（存在しない作業を推測で追加するのは禁止）。
- 変更する行・列は、依頼の内容から必要最小限の範囲に絞ってください。関係のない
  行・列（見出しの月表示や、依頼と無関係な工種の行など）には触れないでください。
- 大きな変更（日付範囲の変更、複数行にまたがる変更など）を行う前に、変更後の
  内容を要約してユーザーに確認を求めることも検討してください。特に、依頼内容が
  曖昧で複数の解釈がありうる場合は、作業前に質問してください。
"""


def _build_context_summary(category: str) -> str:
    """個人情報は含めず、件数などの集計情報と、該当カテゴリのメモのみをもとに作成する。"""
    customers = get_all_customers()
    documents = get_all_documents()
    total_amount = sum(d["total"] for d in documents)

    notes = get_memory_notes(category)
    notes_text = (
        "\n".join(f"- {note['content']}" for note in notes)
        if notes
        else "（まだ登録されているメモはありません）"
    )

    parts = [
        "現在のアプリのデータ状況（集計情報のみで、氏名や連絡先などの個人情報は含みません）\n"
        f"- 登録されている顧客数: {len(customers)}件\n"
        f"- 作成された書類の件数: {len(documents)}件\n"
        f"- 書類の合計金額の総計: {total_amount:,}円\n",
        "現在表示中のスプレッドシートのセルや列幅を読み書きするツールが使えます。"
        "ユーザーからスプレッドシートの内容確認や編集を頼まれたら、必要に応じてツールを使ってください。\n",
        "それとは別に、アプリ本体のデータベース（顧客・顧客担当者・協力会社・案件・物件）を"
        "検索・登録・修正するツールも使えます。ユーザーから「〇〇さんの郵便番号を登録して」"
        "「△△様の電話番号を直して」のような、アプリのデータそのものへの操作を頼まれたときは、"
        "スプレッドシートの話だと決めつけず、まずsearch_customers_dbなどの検索ツールで対象を"
        "確認してから、update_customer_fieldなどの更新ツールを使ってください。"
        "登録データの削除だけはこれらのツールでは行えません（削除が必要な場合は、該当ページの"
        "削除確認つきの画面から手動で行うようご案内してください）。\n",
    ]
    if category == "見積書":
        rules = _load_estimate_skill_rules()
        if rules:
            parts.append(
                "見積書の明細を編集する際は、次のスキル定義（見積書作成ルール）に必ず従ってください。\n\n"
                + rules
            )
    if category == "工程表":
        parts.append(SCHEDULE_EDIT_RULES)
    parts.append(
        f"\n【「{category}」について、これまでにメモリーへ保存された過去の指摘・ルール】\n"
        f"{notes_text}\n"
        "これらのメモに書かれている内容は、今後の作業でも必ず守ってください。"
    )
    return "\n".join(parts)


def _summarize_for_memory(messages: list[dict], category: str) -> str:
    """会話の中でユーザーが指摘・指示した内容から、今後も守るべきルールだけを簡潔にまとめる。

    世間話やその場限りの確認は含めない。該当する内容が無ければ「なし」を返す。
    """
    api_key = _get_api_key()
    if anthropic is None or not api_key:
        return ""

    client = anthropic.Anthropic(api_key=api_key)
    instruction = (
        f"これまでの「{category}」に関する会話を振り返り、ユーザーが指摘・指示した内容のうち、"
        "今後の同様の作業でも継続して守るべきルールや注意点だけを箇条書きで簡潔にまとめてください。"
        "世間話や、その場限りの確認・雑談は含めないでください。"
        "該当する内容が無い場合は「なし」とだけ答えてください。"
    )
    conversation = list(messages) + [{"role": "user", "content": instruction}]
    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=1024,
        messages=conversation,
    )
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _call_claude(messages: list[dict], category: str) -> str:
    """これまでの会話履歴を渡してClaudeからの返答を取得する。APIキー未設定時は案内メッセージを返す。"""
    api_key = _get_api_key()
    if anthropic is None:
        return "anthropicパッケージが未インストールのため応答できません。"
    if not api_key:
        return (
            "まだAnthropicのAPIキーが設定されていないため、実際の応答はできません。\n"
            "APIキーを設定すると、ここでClaudeが返答するようになります。"
        )

    client = anthropic.Anthropic(api_key=api_key)
    conversation = list(messages)

    for _ in range(30):  # ツール呼び出しの無限ループを防ぐための上限（見積書の明細入力は工程が多いため多めに確保）
        # max_tokensは十分大きくし、タイムアウト防止のためストリーミングで取得する。
        # 明細の多い案件では1回のやりとりの出力量が多く、既定の小さいmax_tokensだと
        # 文章の途中でstop_reason="max_tokens"となって打ち切られ、ツール呼び出しに
        # 辿り着けないまま無言で終わってしまうことがあったため（工程表の自動生成で
        # 遭遇したのと同じ問題）。
        with client.messages.stream(
            model=MODEL_NAME,
            max_tokens=32000,
            system=_build_context_summary(category),
            tools=SHEET_TOOLS + APP_DB_TOOLS,
            messages=conversation,
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "max_tokens":
            return (
                "回答が長くなりすぎたため、途中で終了しました。"
                "工種を分けるなど、一度に頼む範囲を絞ってもう一度お試しください。"
            )
        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        conversation.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                if block.name in APP_DB_TOOL_NAMES:
                    result = _run_db_tool(block.name, block.input)
                else:
                    result = _run_sheet_tool(block.name, block.input, category)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
        conversation.append({"role": "user", "content": tool_results})

    return "処理が複雑になりすぎたため、完了できませんでした。もう一度お試しください。"


def show_chat_toggle() -> None:
    """チャットが閉じているとき、画面左下に固定表示の「チャット」ボタンを表示する。

    ページ内のレイアウト（見積書ページのGoogleスプレッドシート埋め込みなど）に
    隠されないよう、通常のドキュメントの流れではなく画面に固定表示する。
    """
    if "chat_open" not in st.session_state:
        st.session_state["chat_open"] = False

    if st.session_state["chat_open"]:
        return

    st.markdown(
        """
        <style>
            .st-key-chat_toggle_container {
                position: fixed;
                bottom: 1rem;
                left: 1rem;
                z-index: 999999;
                /* 幅を指定しないとStreamlitの既定スタイル(横幅100%)が残り、
                   ボタンの見た目より広い透明な帯が画面下部に固定表示され、
                   その帯に重なった他の要素のクリックを奪ってしまうため、
                   ボタンの実サイズに合わせて幅を縮める。 */
                width: fit-content;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="chat_toggle_container"):
        if st.button("チャット", key="chat_toggle_button"):
            st.session_state["chat_open"] = True
            st.rerun()


def show_chat_panel(category: str) -> None:
    """チャット開閉状態がONのとき、画面右側に固定表示のチャットパネルを描画する。

    categoryは、このチャットが「見積書」「請求書」など、どの業務分類に属するかを表す
    （MEMORY_CATEGORIESのいずれか）。メモリー機能の保存・参照先の分類として使う。
    """
    if category not in MEMORY_CATEGORIES:
        raise ValueError(f"未対応のカテゴリです: {category}")
    if not st.session_state.get("chat_open"):
        return

    st.markdown(
        """
        <style>
            div[data-testid="stAppViewContainer"] .block-container {
                max-width: calc(100% - 380px) !important;
                padding-right: 1.5rem !important;
            }
            .st-key-chat_panel {
                position: fixed;
                top: 4.5rem;
                right: 1rem;
                width: 340px;
                height: calc(100vh - 6rem);
                display: flex;
                flex-direction: column;
                gap: 0.4rem;
                overflow: hidden;
                background-color: #f0f2f6;
                border: 1px solid rgba(128, 128, 128, 0.3);
                border-radius: 8px;
                padding: 1rem;
                z-index: 999;
            }
            @media (prefers-color-scheme: dark) {
                .st-key-chat_panel {
                    background-color: #262730;
                }
            }
            /* StreamlitはコンテナをさらにstLayoutWrapperという内部divで包んでおり、
               実際にflexアイテムとして働くのはそちらのため、直接子要素として指定する。 */
            .st-key-chat_panel > [data-testid="stLayoutWrapper"] {
                flex: 0 0 auto;
                min-height: 0;
            }
            .st-key-chat_panel > [data-testid="stLayoutWrapper"]:has(.st-key-chat_messages_area) {
                flex: 1 1 auto;
                min-height: 0;
                overflow: hidden;
            }
            .st-key-chat_messages_area {
                height: 100%;
                overflow-y: auto;
                margin-bottom: 0.5rem;
                font-size: 0.85rem;
            }
            .st-key-chat_panel_header h3 {
                font-size: 1.1rem;
                margin: 0;
            }
            .st-key-chat_panel_header [data-testid="stHorizontalBlock"] {
                align-items: center;
            }
            .st-key-chat_input_area [data-testid="stVerticalBlock"] {
                gap: 0.3rem;
            }
            .st-key-chat_input_area [data-testid="stCheckbox"] {
                margin: 0;
            }
            .st-key-chat_input_text textarea {
                background-color: black !important;
                color: white !important;
                font-size: 0.85rem;
                padding: 0.4rem 0.6rem;
            }
            .st-key-chat_send_button button,
            .st-key-chat_close_button button {
                background-color: black !important;
                color: white !important;
                border-color: black !important;
            }
            .st-key-chat_send_button button,
            .st-key-chat_close_button button,
            .st-key-chat_toggle_button button {
                padding: 0.2rem 0.6rem;
                font-size: 0.8rem;
                min-height: 0;
            }
            /* ファイル添付欄（アップローダー）をコンパクトにする */
            .st-key-chat_input_area [data-testid="stFileUploaderDropzone"] {
                padding: 0.4rem 0.6rem;
                min-height: 0;
            }
            .st-key-chat_input_area [data-testid="stFileUploaderDropzoneInstructions"] {
                font-size: 0.7rem;
            }
            .st-key-chat_input_area [data-testid="stFileUploaderDropzone"] button {
                padding: 0.15rem 0.5rem;
                font-size: 0.7rem;
                min-height: 0;
            }
            /* メモリー保存チェックボックスをコンパクトにする */
            .st-key-chat_input_area [data-testid="stCheckbox"] label p {
                font-size: 0.75rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container(key="chat_panel"):
        with st.container(key="chat_panel_header"):
            col_title, col_close = st.columns([2, 1])
            with col_title:
                st.subheader("チャット")
            with col_close:
                if st.button("閉じる", key="chat_close_button", width="stretch"):
                    st.session_state["chat_open"] = False
                    st.rerun()
        _render_conversation(category)


def _scroll_chat_to_bottom() -> None:
    """会話履歴の表示エリアを一番下まで自動でスクロールする。

    st.markdownで<script>を挿入しても実行されないため、スクリプトが実行される
    components.html（サンドボックス化されたiframe）を使い、親ページのDOMを操作する。
    """
    # メッセージ数を埋め込んで内容を毎回変えることで、コンポーネントを毎回再読み込みさせ、
    # スクリプトが確実に再実行されるようにする（内容が同じだとStreamlitが再実行しないため）。
    message_count = len(st.session_state.get("chat_messages", []))
    components.html(
        f"""
        <!-- marker:{message_count} -->
        <script>
            const area = window.parent.document.querySelector('.st-key-chat_messages_area');
            if (area) {{
                area.scrollTop = area.scrollHeight;
            }}
        </script>
        """,
        height=0,
    )


def _render_conversation(category: str) -> None:
    """会話履歴の表示と、新しいメッセージの入力・送信を行う基本的なチャットUI。

    st.chat_inputはEnterキーで即送信されるため、日本語入力の変換中にEnterを押すと
    誤って送信されてしまう(Streamlit側の既知の不具合)。そのため、Enterでは送信されない
    st.text_area + 送信ボタンの組み合わせを使う。
    """
    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []
    if st.session_state.get("_clear_chat_input"):
        st.session_state["chat_input_text"] = ""
        st.session_state["_clear_chat_input"] = False
    if st.session_state.get("_clear_chat_save_memory"):
        st.session_state["chat_save_memory"] = False
        st.session_state["_clear_chat_save_memory"] = False
    if "chat_uploader_counter" not in st.session_state:
        st.session_state["chat_uploader_counter"] = 0

    with st.container(key="chat_messages_area"):
        for message in st.session_state["chat_messages"]:
            with st.chat_message(message["role"]):
                _render_message_content(message["content"])
    _scroll_chat_to_bottom()

    with st.container(key="chat_input_area"):
        # file_uploaderはtext_areaと違いsession_stateの値を直接クリアできないため、
        # keyを毎回変えることで送信後に新しい（空の）ウィジェットとして描画させる。
        uploader_key = f"chat_file_uploader_{st.session_state['chat_uploader_counter']}"
        uploaded_file = st.file_uploader(
            "図面を添付（PDF・画像）",
            type=["pdf", "png", "jpg", "jpeg"],
            key=uploader_key,
            label_visibility="collapsed",
        )
        st.text_area(
            "メッセージを入力してください",
            key="chat_input_text",
            height=70,
            label_visibility="collapsed",
        )
        st.checkbox(
            f"この内容を「{category}」のメモリーに保存する",
            key="chat_save_memory",
        )
        send_clicked = st.button("送信", key="chat_send_button", type="primary")

    if send_clicked:
        user_input = st.session_state["chat_input_text"].strip()
        if user_input or uploaded_file is not None:
            content = _build_user_content(user_input, uploaded_file)
            st.session_state["chat_messages"].append({"role": "user", "content": content})
            reply = _call_claude(st.session_state["chat_messages"], category)
            st.session_state["chat_messages"].append({"role": "assistant", "content": reply})

            if st.session_state.get("chat_save_memory"):
                summary = _summarize_for_memory(st.session_state["chat_messages"], category)
                if summary and summary != "なし":
                    add_memory_note(category, summary)
                    st.session_state["chat_messages"].append(
                        {
                            "role": "assistant",
                            "content": f"（「{category}」のメモリーに保存しました）\n{summary}",
                        }
                    )

            st.session_state["_clear_chat_input"] = True
            st.session_state["_clear_chat_save_memory"] = True
            st.session_state["chat_uploader_counter"] += 1
            st.rerun()
