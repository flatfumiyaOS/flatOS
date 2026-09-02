"""作成済みの工程表スプレッドシートの一覧をローカルJSONで管理するモジュール。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import drive_storage

DATA_DIR = Path(__file__).parent / "data"
SCHEDULES_FILE = DATA_DIR / "schedules.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_all() -> list[dict]:
    drive_storage.restore_if_missing(SCHEDULES_FILE, "schedules.json")
    if not SCHEDULES_FILE.exists():
        return []
    return json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))


def _save_all(schedules: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULES_FILE.write_text(
        json.dumps(schedules, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    drive_storage.backup_file(SCHEDULES_FILE, "schedules.json")


def get_all_schedules() -> list[dict]:
    return _load_all()


def get_schedule(schedule_id: int) -> dict | None:
    return next((s for s in _load_all() if s["id"] == schedule_id), None)


def add_schedule(
    customer_name: str, project_name: str, spreadsheet_id: str, file_name: str
) -> dict:
    schedules = _load_all()
    new_id = max((s["id"] for s in schedules), default=0) + 1
    record = {
        "id": new_id,
        "customer_name": customer_name,
        "project_name": project_name,
        "spreadsheet_id": spreadsheet_id,
        "file_name": file_name,
        "created_at": _now(),
    }
    schedules.append(record)
    _save_all(schedules)
    return record


def remove_schedule(schedule_id: int, user_credentials=None) -> None:
    """一覧からこの工程表の記録を外す。

    user_credentials（ログイン中のGoogleアカウント）が渡されていれば、Googleドライブ上の
    スプレッドシート本体もゴミ箱に移動する（完全削除ではなく復元可能な状態にする）。
    渡されていない場合、Googleドライブ上のファイルはそのまま残る（一覧からの記録だけを外す）。
    """
    schedules = _load_all()
    schedule = next((s for s in schedules if s["id"] == schedule_id), None)
    if schedule is None:
        return
    if user_credentials is not None:
        try:
            drive_storage.trash_file(user_credentials, schedule["spreadsheet_id"])
        except Exception:
            pass
    remaining = [s for s in schedules if s["id"] != schedule_id]
    _save_all(remaining)
