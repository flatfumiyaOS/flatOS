"""案件管理データのローカルJSON保存用モジュール。

案件ごとの基本情報・資料・工程表・写真・見積書スプレッドシートIDを、
data/projects.json（一覧）とdata/project_files/<案件ID>/（アップロードファイル本体）に保存する。
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

import drive_storage
import google_auth

DATA_DIR = Path(__file__).parent / "data"
PROJECTS_FILE = DATA_DIR / "projects.json"
PROJECT_FILES_DIR = DATA_DIR / "project_files"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_all() -> list[dict]:
    drive_storage.restore_if_missing(PROJECTS_FILE, "projects.json")
    if not PROJECTS_FILE.exists():
        return []
    return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))


def _save_all(projects: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(
        json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    drive_storage.backup_file(PROJECTS_FILE, "projects.json")


def get_all_projects() -> list[dict]:
    return _load_all()


def get_project(project_id: int) -> dict | None:
    return next((p for p in _load_all() if p["id"] == project_id), None)


def create_project(name: str) -> dict:
    projects = _load_all()
    new_id = max((p["id"] for p in projects), default=0) + 1
    now = _now()
    project = {
        "id": new_id,
        "name": name,
        "customer_name": "",
        "address": "",
        "start_date": "",
        "end_date": "",
        "overview": "",
        "documents": [],
        "photos": [],
        "cover_photo": None,
        "spreadsheet_id": None,
        "schedule_spreadsheet_id": None,
        "created_at": now,
        "updated_at": now,
    }
    projects.append(project)
    _save_all(projects)
    return project


def get_or_create_project(name: str) -> dict:
    """案件名が一致する既存の案件を返す。無ければ新規に作成して返す。

    見積書ページのように、案件管理を経由せずに見積書を作成できる画面から
    呼び出し、その場で案件管理に案件として登録・紐付けするために使う。
    """
    existing = next((p for p in _load_all() if p["name"] == name), None)
    if existing is not None:
        return existing
    return create_project(name)


def _update_project(project_id: int, **fields) -> None:
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p.update(fields)
            p["updated_at"] = _now()
            break
    _save_all(projects)


def update_basic_info(
    project_id: int,
    customer_name: str,
    address: str,
    start_date: str,
    end_date: str,
    overview: str,
) -> None:
    _update_project(
        project_id,
        customer_name=customer_name,
        address=address,
        start_date=start_date,
        end_date=end_date,
        overview=overview,
    )


def set_spreadsheet_id(project_id: int, spreadsheet_id: str) -> None:
    _update_project(project_id, spreadsheet_id=spreadsheet_id)


def set_schedule_spreadsheet_id(project_id: int, spreadsheet_id: str) -> None:
    _update_project(project_id, schedule_spreadsheet_id=spreadsheet_id)


def _save_file(
    project_id: int, subdir: str, filename: str, file_bytes: bytes
) -> tuple[str, str | None]:
    folder = PROJECT_FILES_DIR / str(project_id) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    if target.exists():
        stem, suffix = target.stem, target.suffix
        target = folder / f"{stem}_{datetime.now().strftime('%H%M%S%f')}{suffix}"
    target.write_bytes(file_bytes)

    drive_file_id = None
    if google_auth.is_logged_in():
        try:
            credentials = google_auth.get_credentials()
            drive_folder_id = drive_storage.get_folder_path(
                credentials, subdir, str(project_id)
            )
            drive_file_id = drive_storage.upload_bytes(
                credentials, drive_folder_id, target.name, file_bytes
            )
        except Exception:
            drive_file_id = None
    return str(target), drive_file_id


def get_file_bytes(record: dict) -> bytes | None:
    """写真・資料のバイト列を返す。ローカルにキャッシュがあればそこから、
    無ければ（サーバー再起動などで消えていれば）Googleドライブから復元して返す。
    ドライブにも無い、あるいは未ログインの場合はNoneを返す。
    """
    path = Path(record.get("path", ""))
    if path.exists():
        return path.read_bytes()

    drive_file_id = record.get("drive_file_id")
    if not drive_file_id or not google_auth.is_logged_in():
        return None
    try:
        data = drive_storage.download_bytes(google_auth.get_credentials(), drive_file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data
    except Exception:
        return None


def get_photo_display_bytes(record: dict) -> bytes | None:
    """写真を画面に表示するためのバイト列を返す。

    スマートフォンで撮影した写真は、画素データ自体は横向きのまま、EXIFの回転情報
    だけで正しい向きを表現していることが多い。st.image()はこの回転情報を見ないため、
    そのまま渡すと向きがおかしく表示される。ここで回転情報を画素データに焼き込み
    直してから返す（保存済みのファイル自体は元のまま変更しない）。
    """
    data = get_file_bytes(record)
    if data is None:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        original_format = image.format or "JPEG"
        corrected = ImageOps.exif_transpose(image)
        if original_format == "JPEG" and corrected.mode not in ("RGB", "L"):
            corrected = corrected.convert("RGB")
        buffer = io.BytesIO()
        corrected.save(buffer, format=original_format)
        return buffer.getvalue()
    except Exception:
        return data


def add_document(project_id: int, filename: str, file_bytes: bytes) -> None:
    path, drive_file_id = _save_file(project_id, "documents", filename, file_bytes)
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p["documents"].append(
                {
                    "filename": Path(path).name,
                    "path": path,
                    "drive_file_id": drive_file_id,
                    "uploaded_at": _now(),
                }
            )
            p["updated_at"] = _now()
            break
    _save_all(projects)


def add_photo(project_id: int, filename: str, file_bytes: bytes, phase: str) -> None:
    path, drive_file_id = _save_file(project_id, "photos", filename, file_bytes)
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p["photos"].append(
                {
                    "filename": Path(path).name,
                    "path": path,
                    "drive_file_id": drive_file_id,
                    "phase": phase,
                    "uploaded_at": _now(),
                }
            )
            p["updated_at"] = _now()
            break
    _save_all(projects)


def set_cover_photo(project_id: int, filename: str, file_bytes: bytes) -> None:
    """案件一覧カードの表紙に使う「現場建物写真」を保存する（1案件につき1枚、上書き）。"""
    path, drive_file_id = _save_file(project_id, "cover", filename, file_bytes)
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p["cover_photo"] = {
                "filename": Path(path).name,
                "path": path,
                "drive_file_id": drive_file_id,
                "uploaded_at": _now(),
            }
            p["updated_at"] = _now()
            break
    _save_all(projects)
