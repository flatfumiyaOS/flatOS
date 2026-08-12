"""案件管理データのローカルJSON保存用モジュール。

案件ごとの基本情報・資料・工程表・写真・見積書スプレッドシートIDを、
data/projects.json（一覧）とdata/project_files/<案件ID>/（アップロードファイル本体）に保存する。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
PROJECTS_FILE = DATA_DIR / "projects.json"
PROJECT_FILES_DIR = DATA_DIR / "project_files"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_all() -> list[dict]:
    if not PROJECTS_FILE.exists():
        return []
    return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))


def _save_all(projects: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(
        json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def _save_file(project_id: int, subdir: str, filename: str, file_bytes: bytes) -> str:
    folder = PROJECT_FILES_DIR / str(project_id) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    if target.exists():
        stem, suffix = target.stem, target.suffix
        target = folder / f"{stem}_{datetime.now().strftime('%H%M%S%f')}{suffix}"
    target.write_bytes(file_bytes)
    return str(target)


def add_document(project_id: int, filename: str, file_bytes: bytes) -> None:
    path = _save_file(project_id, "documents", filename, file_bytes)
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p["documents"].append(
                {"filename": Path(path).name, "path": path, "uploaded_at": _now()}
            )
            p["updated_at"] = _now()
            break
    _save_all(projects)


def add_photo(project_id: int, filename: str, file_bytes: bytes, phase: str) -> None:
    path = _save_file(project_id, "photos", filename, file_bytes)
    projects = _load_all()
    for p in projects:
        if p["id"] == project_id:
            p["photos"].append(
                {
                    "filename": Path(path).name,
                    "path": path,
                    "phase": phase,
                    "uploaded_at": _now(),
                }
            )
            p["updated_at"] = _now()
            break
    _save_all(projects)
