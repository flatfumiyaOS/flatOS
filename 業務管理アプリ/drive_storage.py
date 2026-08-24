"""ローカルディスクに保存しているデータ（案件データ・現場写真・原価証憑など）を
Googleドライブへバックアップ・復元するための共通部品。

Streamlit Community Cloudはアプリの再起動のたびにローカルディスクの内容が
消えてしまうため、ここでユーザー本人のGoogleアカウント（OAuthログイン）の
ドライブに保存しておき、次回起動時に無ければそこから復元する。
サービスアカウントには保存容量が無い（sheets.py参照）ため、ここでのアップロードも
すべてユーザー本人のOAuth権限（google_auth.get_credentials()）で行う。
"""

from __future__ import annotations

import io
from pathlib import Path

import streamlit as st
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

import google_auth

APP_ROOT_FOLDER_NAME = "flatOS_data"


def _drive_service(user_credentials: UserCredentials):
    return build("drive", "v3", credentials=user_credentials)


def _find_folder(drive_service, name: str, parent_id: str) -> str | None:
    query = (
        f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false and '{parent_id}' in parents"
    )
    result = drive_service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    files = result.get("files", [])
    return files[0]["id"] if files else None


def _create_folder(drive_service, name: str, parent_id: str) -> str:
    folder = (
        drive_service.files()
        .create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [parent_id],
            },
            fields="id",
        )
        .execute()
    )
    return folder["id"]


def _get_app_root_folder_id(user_credentials: UserCredentials) -> str:
    """バックアップの起点となるフォルダのIDを返す。

    st.secrets["DRIVE_APP_FOLDER_ID"] が設定されていれば、そのフォルダを使う。
    複数人（例: 社長と現場監督）がそれぞれ自分のGoogleアカウントでログインしても
    同じ場所にバックアップ・復元できるよう、あらかじめ全員に共有しておいた
    固定フォルダのIDをここに設定する運用を想定している。
    設定が無ければ、ログイン中のアカウントのマイドライブ直下に自動作成する
    （1人だけで使う場合の簡易動作）。
    """
    try:
        configured_id = st.secrets.get("DRIVE_APP_FOLDER_ID")
    except Exception:
        configured_id = None
    if configured_id:
        return configured_id

    drive_service = _drive_service(user_credentials)
    query = (
        f"name = '{APP_ROOT_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false and 'root' in parents"
    )
    result = drive_service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    files = result.get("files", [])
    return files[0]["id"] if files else _create_folder(drive_service, APP_ROOT_FOLDER_NAME, "root")


def get_folder_path(user_credentials: UserCredentials, *path_parts: str) -> str:
    """バックアップ用のルートフォルダ（_get_app_root_folder_id参照）を起点に、
    指定した名前のフォルダを（無ければ作成して）辿り、末端フォルダのIDを返す。
    同じ結果はセッション中キャッシュする。"""
    cache_key = "_drive_folder_cache::" + "/".join((APP_ROOT_FOLDER_NAME, *path_parts))
    cached = st.session_state.get(cache_key)
    if cached:
        return cached

    drive_service = _drive_service(user_credentials)
    folder_id = _get_app_root_folder_id(user_credentials)

    for part in path_parts:
        existing = _find_folder(drive_service, part, folder_id)
        folder_id = existing or _create_folder(drive_service, part, folder_id)

    st.session_state[cache_key] = folder_id
    return folder_id


def find_file_id(user_credentials: UserCredentials, folder_id: str, filename: str) -> str | None:
    drive_service = _drive_service(user_credentials)
    escaped = filename.replace("'", "\\'")
    query = f"name = '{escaped}' and '{folder_id}' in parents and trashed = false"
    result = drive_service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    files = result.get("files", [])
    return files[0]["id"] if files else None


def upload_bytes(
    user_credentials: UserCredentials,
    folder_id: str,
    filename: str,
    data: bytes,
    mime_type: str = "application/octet-stream",
) -> str:
    """指定フォルダに新規ファイルとしてアップロードし、file_idを返す。"""
    drive_service = _drive_service(user_credentials)
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
    file = (
        drive_service.files()
        .create(body={"name": filename, "parents": [folder_id]}, media_body=media, fields="id")
        .execute()
    )
    return file["id"]


def update_bytes(
    user_credentials: UserCredentials,
    file_id: str,
    data: bytes,
    mime_type: str = "application/octet-stream",
) -> None:
    """既存ファイルの中身を上書きする。"""
    drive_service = _drive_service(user_credentials)
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)
    drive_service.files().update(fileId=file_id, media_body=media).execute()


def save_or_update_file(
    user_credentials: UserCredentials,
    folder_id: str,
    filename: str,
    data: bytes,
    mime_type: str = "application/octet-stream",
) -> str:
    """同名ファイルが既にあれば上書き、無ければ新規作成する。file_idを返す。"""
    existing_id = find_file_id(user_credentials, folder_id, filename)
    if existing_id:
        update_bytes(user_credentials, existing_id, data, mime_type)
        return existing_id
    return upload_bytes(user_credentials, folder_id, filename, data, mime_type)


def make_public(user_credentials: UserCredentials, file_id: str) -> None:
    """ファイルを「リンクを知っている全員が閲覧可」に設定する。

    Googleスプレッドシートの=IMAGE()関数など、Sheets側から外部URLとして直接
    取得する必要がある場合に使う（サービスアカウント共有だけでは、Sheetsの
    IMAGE()関数からは読み込めないため）。
    """
    drive_service = _drive_service(user_credentials)
    drive_service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
    ).execute()


def trash_file(user_credentials: UserCredentials, file_id: str) -> None:
    """ファイルをゴミ箱に移動する（完全削除ではなく、Googleドライブ上で復元可能な状態にする）。

    案件データの削除など、ユーザー本人の明示的な許可を得たうえでファイルを削除する
    場合に使う。完全削除(files().delete)ではなくゴミ箱移動にしているのは、誤って
    削除してしまった場合にGoogleドライブ側の「ゴミ箱」から復元できるようにするため。
    """
    drive_service = _drive_service(user_credentials)
    drive_service.files().update(fileId=file_id, body={"trashed": True}).execute()


def download_bytes(user_credentials: UserCredentials, file_id: str) -> bytes:
    drive_service = _drive_service(user_credentials)
    request = drive_service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def restore_if_missing(local_path: Path, drive_filename: str, *subfolder_parts: str) -> bool:
    """local_pathが存在せず、Googleにログイン済みの場合、Googleドライブから復元を試みる。

    セッション中、同じファイルについての復元は一度だけ試す（ログイン前の呼び出しでは
    まだ試行済み扱いにしないので、後からログインした際は改めて試される）。
    復元できればTrueを返す。
    """
    if local_path.exists():
        return False
    if not google_auth.is_logged_in():
        return False

    subfolder = "/".join(subfolder_parts) if subfolder_parts else "app_data"
    cache_key = f"_drive_restore_tried::{subfolder}/{drive_filename}"
    if st.session_state.get(cache_key):
        return False
    st.session_state[cache_key] = True

    try:
        credentials = google_auth.get_credentials()
        folder_id = get_folder_path(credentials, *(subfolder_parts or ("app_data",)))
        file_id = find_file_id(credentials, folder_id, drive_filename)
        if file_id is None:
            return False
        data = download_bytes(credentials, file_id)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        return True
    except Exception:
        return False


def backup_file(
    local_path: Path,
    drive_filename: str,
    *subfolder_parts: str,
    mime_type: str = "application/octet-stream",
) -> None:
    """local_pathの中身をGoogleドライブへバックアップする（同名なら上書き）。

    ログインしていない場合や通信エラー時は、何もせず静かに諦める
    （ローカル保存自体は既に完了済みのはずなので、ここで失敗してもアプリ全体は壊さない）。
    """
    if not google_auth.is_logged_in():
        return
    try:
        credentials = google_auth.get_credentials()
        folder_id = get_folder_path(credentials, *(subfolder_parts or ("app_data",)))
        data = local_path.read_bytes()
        save_or_update_file(credentials, folder_id, drive_filename, data, mime_type)
    except Exception:
        pass
