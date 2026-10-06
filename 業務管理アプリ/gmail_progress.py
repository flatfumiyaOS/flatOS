"""案件の「現在の進捗確認」用に、Gmailから案件に関連しそうなメールを検索し、
AIで本当に関係があるかを判定したうえで一行要約するロジック。

検索は顧客名・案件名をキーワードにGmail自体の検索機能（qパラメータ）で候補を絞り込み、
件名・送信者・本文冒頭（スニペット）だけをAIに渡して関係の有無と要約を判定させる
（本文全体は取得しない）。判定・要約にはAIの誤り（別案件の混入・見落とし）が
あり得るため、あくまで参考情報として表示する前提で使う。
"""

from __future__ import annotations

import datetime
import json
import os
import re

import streamlit as st
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build

try:
    import anthropic
except ImportError:
    anthropic = None

MODEL_NAME = "claude-sonnet-5"

# 検索対象は直近何日分のメールにするか。
SEARCH_LOOKBACK_DAYS = 90
# 1回の更新でAIに判定させる候補メールの上限件数（コスト・応答時間を抑えるため）。
MAX_CANDIDATE_MESSAGES = 40

# TO DO判定で「こちら側」として扱うメールアドレス。主にやり取りしている平居靖弘を
# 本人（ログイン中のGoogleアカウント）と同一視してよいとユーザー本人から確認済み
# （スレッドの最後の送信者がこの中に含まれていれば、既にこちらが対応済みとみなす）。
#
# 注意: この機能はログイン中のGoogleアカウント自身のメールボックス（userId="me"）
# しか検索できない（Gmail APIの仕様上、他人のメールボックスには本人のOAuthトークン
# では一切アクセスできない）。そのため、平居靖弘が送受信したメールであっても、
# ログイン中の本人がCC等で実際に受け取っている（＝本人のメールボックスに存在する）
# ものしか、そもそも検索結果に出てこない。STAFF_EMAILは、そうして見えている
# スレッドの「最後の送信者が平居靖弘自身だった場合」を対応済みとして除外するためだけに
# 使っており、平居靖弘のメールボックスへ別途アクセスするものではない。
STAFF_EMAIL = "y-hirai@flat.co.jp"

PROGRESS_LOG_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "判定対象のmessage_id"},
                    "relevant": {
                        "type": "boolean",
                        "description": "このメールが、指定した案件に実際に関係する内容かどうか",
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "relevantがtrueの場合のみ、内容を一行で簡潔に要約した日本語の文章"
                            "（例:「YAMA設備へスケジュール変更の連絡。10/14予定の作業を10/16に"
                            "変更できないか打診。回答待ち」）。relevantがfalseの場合は空文字。"
                        ),
                    },
                },
                "required": ["message_id", "relevant", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entries"],
    "additionalProperties": False,
}


def _get_api_key() -> str | None:
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


def _gmail_service(user_credentials: UserCredentials):
    return build("gmail", "v1", credentials=user_credentials)


def _build_search_query(customer_name: str, project_name: str) -> str:
    keywords = [k.strip() for k in (customer_name, project_name) if k and k.strip()]
    if not keywords:
        return ""
    cutoff = (
        datetime.date.today() - datetime.timedelta(days=SEARCH_LOOKBACK_DAYS)
    ).strftime("%Y/%m/%d")
    keyword_query = " OR ".join(f'"{k}"' for k in keywords)
    return f"({keyword_query}) after:{cutoff}"


def _fetch_candidate_messages(
    user_credentials: UserCredentials, query: str, exclude_message_ids: set[str]
) -> list[dict]:
    """検索条件に一致するメールのうち、まだ進捗ログに取り込んでいないものの
    件名・送信者・日付・本文冒頭（スニペット）を取得して返す。"""
    service = _gmail_service(user_credentials)

    message_refs: list[dict] = []
    page_token = None
    while len(message_refs) < MAX_CANDIDATE_MESSAGES:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=min(50, MAX_CANDIDATE_MESSAGES - len(message_refs)),
                pageToken=page_token,
            )
            .execute()
        )
        page_refs = response.get("messages", [])
        for ref in page_refs:
            if ref["id"] not in exclude_message_ids:
                message_refs.append(ref)
        page_token = response.get("nextPageToken")
        if not page_token or not page_refs:
            break

    detailed = []
    for ref in message_refs:
        message = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=ref["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in message.get("payload", {}).get("headers", [])}
        detailed.append(
            {
                "id": message["id"],
                "subject": headers.get("Subject", "（件名なし）"),
                "from": headers.get("From", ""),
                "date_header": headers.get("Date", ""),
                "internal_date_ms": int(message.get("internalDate", "0")),
                "snippet": message.get("snippet", ""),
            }
        )
    return detailed


def fetch_and_summarize_progress(
    customer_name: str,
    project_name: str,
    address: str,
    user_credentials: UserCredentials,
    exclude_message_ids: set[str],
) -> list[dict]:
    """案件に関連しそうなメールをGmailで検索し、AIに関係の有無・要約を判定させて返す。

    exclude_message_idsに含まれるメール（既に進捗ログに取り込み済み）は検索対象から除く。
    戻り値は、関係ありと判定された分のみ {"gmail_message_id", "date", "summary"} の
    辞書のリスト（呼び出し側でproject_store.add_progress_log_entriesに渡す想定）。
    """
    query = _build_search_query(customer_name, project_name)
    if not query:
        return []

    candidates = _fetch_candidate_messages(user_credentials, query, exclude_message_ids)
    if not candidates:
        return []

    api_key = _get_api_key()
    if anthropic is None or not api_key:
        raise RuntimeError("Anthropic APIキーが設定されていないため、要約できません。")

    email_list_text = "\n\n".join(
        f"[message_id: {c['id']}]\n日付: {c['date_header']}\n送信者: {c['from']}\n"
        f"件名: {c['subject']}\n本文冒頭: {c['snippet']}"
        for c in candidates
    )
    prompt = (
        f"以下は、Gmailで「{customer_name}」「{project_name}」に関連しそうなキーワードで検索して"
        "見つかったメール一覧です（件名・送信者・本文冒頭のみ）。\n"
        f"実際にこの案件（顧客名: {customer_name}、案件名: {project_name}、"
        f"現場住所: {address or '不明'}）に関係する内容かどうかを、メールごとに判定してください。\n"
        "関係すると判定したメールについてのみ、内容を一行で簡潔に日本語で要約してください。\n"
        "広告・通知メールや、キーワードがたまたま一致しただけで明らかに別の案件・別の顧客に"
        "関する内容は、関係ありと判定しないでください。\n\n"
        f"メール一覧:\n{email_list_text}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    with client.messages.stream(
        model=MODEL_NAME,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": PROGRESS_LOG_JSON_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    text = "".join(block.text for block in response.content if block.type == "text")
    result = json.loads(text) if text.strip() else {"entries": []}

    candidates_by_id = {c["id"]: c for c in candidates}
    entries = []
    for item in result.get("entries", []):
        if not item.get("relevant") or not (item.get("summary") or "").strip():
            continue
        candidate = candidates_by_id.get(item.get("message_id"))
        if candidate is None:
            continue
        email_date = (
            datetime.datetime.fromtimestamp(
                candidate["internal_date_ms"] / 1000, tz=datetime.timezone.utc
            )
            .astimezone()
            .date()
        )
        entries.append(
            {
                "gmail_message_id": candidate["id"],
                "date": email_date.isoformat(),
                "summary": item["summary"].strip(),
            }
        )
    return entries


TODO_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "description": "判定対象のthread_id"},
                    "relevant": {
                        "type": "boolean",
                        "description": (
                            "この案件に実際に関係し、こちらからの返信・対応が本当に必要な"
                            "内容かどうか"
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "relevantがtrueの場合のみ、何への対応が必要かを一行で簡潔に"
                            "要約した日本語の文章。relevantがfalseの場合は空文字。"
                        ),
                    },
                },
                "required": ["thread_id", "relevant", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entries"],
    "additionalProperties": False,
}


def _extract_email_address(from_header: str) -> str:
    """Fromヘッダー（例:「山田太郎 <yamada@example.com>」）からメールアドレス部分だけ
    取り出す（小文字化して返す）。抽出できなければ空文字。"""
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", from_header or "")
    return match.group(0).lower() if match else ""


def _get_my_email(service) -> str:
    profile = service.users().getProfile(userId="me").execute()
    return (profile.get("emailAddress") or "").lower()


def _fetch_pending_threads(
    user_credentials: UserCredentials, query: str, our_emails: set[str]
) -> list[dict]:
    """検索条件に一致するメールのスレッドのうち、直近のやり取りで最後に送信したのが
    こちら側（our_emails）ではないスレッド（＝返信・対応待ちの可能性がある）の
    件名・直近の送信者・本文冒頭を返す。"""
    service = _gmail_service(user_credentials)

    message_refs: list[dict] = []
    page_token = None
    while len(message_refs) < MAX_CANDIDATE_MESSAGES:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=min(50, MAX_CANDIDATE_MESSAGES - len(message_refs)),
                pageToken=page_token,
            )
            .execute()
        )
        page_refs = response.get("messages", [])
        message_refs.extend(page_refs)
        page_token = response.get("nextPageToken")
        if not page_token or not page_refs:
            break

    thread_ids: list[str] = []
    seen_thread_ids: set[str] = set()
    for ref in message_refs:
        thread_id = ref.get("threadId")
        if thread_id and thread_id not in seen_thread_ids:
            seen_thread_ids.add(thread_id)
            thread_ids.append(thread_id)

    pending = []
    for thread_id in thread_ids:
        thread = (
            service.users()
            .threads()
            .get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            )
            .execute()
        )
        thread_messages = thread.get("messages", [])
        if not thread_messages:
            continue
        # Gmail APIのスレッド内メッセージは古い順に並ぶため、最後の要素が最新。
        latest = thread_messages[-1]
        headers = {
            h["name"]: h["value"] for h in latest.get("payload", {}).get("headers", [])
        }
        sender_email = _extract_email_address(headers.get("From", ""))
        if sender_email in our_emails:
            continue  # 最後に送ったのがこちら側 -> 対応待ちではない
        pending.append(
            {
                "thread_id": thread_id,
                "subject": headers.get("Subject", "（件名なし）"),
                "from": headers.get("From", ""),
                "snippet": latest.get("snippet", ""),
            }
        )
    return pending


def fetch_todo_items(
    customer_name: str,
    project_name: str,
    address: str,
    user_credentials: UserCredentials,
) -> list[dict]:
    """案件に関連するメールスレッドのうち、こちらからの返信・対応が必要そうなものを
    AIに判定・要約させて返す。

    進捗ログと違い蓄積せず、呼び出すたびに現在の状態を作り直す想定
    （返信済みになれば、次回の呼び出しでは自然に対象から外れる）。
    「こちら側」は、ログイン中のGoogleアカウント本人と、本人と一心同体として扱ってよいと
    確認済みの平居靖弘（STAFF_EMAIL）の両方を指す。
    戻り値: 対応が必要と判定された分のみ {"summary"} の辞書のリスト。
    """
    service = _gmail_service(user_credentials)
    my_email = _get_my_email(service)
    our_emails = {e for e in (my_email, STAFF_EMAIL.lower()) if e}

    query = _build_search_query(customer_name, project_name)
    if not query:
        return []

    pending = _fetch_pending_threads(user_credentials, query, our_emails)
    if not pending:
        return []

    api_key = _get_api_key()
    if anthropic is None or not api_key:
        raise RuntimeError("Anthropic APIキーが設定されていないため、判定できません。")

    thread_list_text = "\n\n".join(
        f"[thread_id: {t['thread_id']}]\n直近の送信者: {t['from']}\n"
        f"件名: {t['subject']}\n本文冒頭: {t['snippet']}"
        for t in pending
    )
    prompt = (
        f"以下は、Gmailで「{customer_name}」「{project_name}」に関連しそうなキーワードで"
        "検索して見つかったメールスレッドのうち、直近のやり取りで最後に送信したのが"
        "こちら（自社）ではないもの一覧です（件名・直近の送信者・本文冒頭のみ）。\n"
        f"実際にこの案件（顧客名: {customer_name}、案件名: {project_name}、"
        f"現場住所: {address or '不明'}）に関係し、こちらからの返信・対応が必要な"
        "内容かどうかを、スレッドごとに判定してください。\n"
        "対応が必要と判定したものについてのみ、何への対応が必要かを一行で簡潔に"
        "日本語で要約してください。\n"
        "広告・通知メールや、キーワードがたまたま一致しただけで明らかに別の案件・"
        "別の顧客に関する内容、返信を必要としない単なる御礼・完了報告のメールなどは、"
        "対応が必要と判定しないでください。\n\n"
        f"スレッド一覧:\n{thread_list_text}"
    )

    client = anthropic.Anthropic(api_key=api_key)
    with client.messages.stream(
        model=MODEL_NAME,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": TODO_JSON_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    text = "".join(block.text for block in response.content if block.type == "text")
    result = json.loads(text) if text.strip() else {"entries": []}

    entries = []
    for item in result.get("entries", []):
        if not item.get("relevant") or not (item.get("summary") or "").strip():
            continue
        entries.append({"summary": item["summary"].strip()})
    return entries
