"""協力会社請求書（PDF・画像）から、AI-OCRで業者名・金額・工種などを抽出するロジック。"""

from __future__ import annotations

import base64
import json
import os

import streamlit as st

try:
    import anthropic
except ImportError:
    anthropic = None

MODEL_NAME = "claude-sonnet-5"

INVOICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": "string", "description": "請求元の業者名"},
        "amount_tax_included": {
            "type": "integer",
            "description": "請求金額（税込・円）。読み取れない場合は0",
        },
        "amount_tax_excluded": {
            "type": "integer",
            "description": "請求金額（税抜・円）。読み取れない場合は税込金額と同じ値",
        },
        "work_type": {"type": "string", "description": "工種・内容（例: 電気工事、給排水設備工事など）"},
        "invoice_date": {
            "type": "string",
            "description": "請求日（YYYY-MM-DD形式）。読み取れない場合は空文字",
        },
        "suggested_project_hint": {
            "type": "string",
            "description": "請求書内の現場住所・案件名など、該当案件を特定する手がかりになりそうな文字列。無ければ空文字",
        },
    },
    "required": [
        "vendor_name",
        "amount_tax_included",
        "amount_tax_excluded",
        "work_type",
        "invoice_date",
        "suggested_project_hint",
    ],
    "additionalProperties": False,
}


def _get_api_key() -> str | None:
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


def extract_invoice_info(file_bytes: bytes, media_type: str) -> dict:
    """請求書のPDF・画像から、業者名・金額・工種・請求日などを抽出して辞書で返す。"""
    api_key = _get_api_key()
    if anthropic is None or not api_key:
        raise RuntimeError("Anthropic APIキーが設定されていないため、自動読み取りができません。")

    data_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
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

    prompt = (
        "添付した協力会社からの請求書を読み取り、内容を抽出してください。"
        "金額は数字のみ（カンマ・円マークなし）で答えてください。"
    )

    client = anthropic.Anthropic(api_key=api_key)
    # max_tokensは十分な余裕を持たせ、ストリーミングで取得する
    # （出力が長さの上限で打ち切られると空の結果になってしまうため）。
    with client.messages.stream(
        model=MODEL_NAME,
        max_tokens=4096,
        output_config={"format": {"type": "json_schema", "schema": INVOICE_JSON_SCHEMA}},
        messages=[{"role": "user", "content": [file_block, {"type": "text", "text": prompt}]}],
    ) as stream:
        response = stream.get_final_message()

    text = "".join(block.text for block in response.content if block.type == "text")
    return json.loads(text)
