"""顧客請求（見積書から作成する請求書）データのローカルJSON保存用モジュール。

data/billings.json に一覧を保存する。project_store.py と同じ構成
（_load_all/_save_all、連番ID）を踏襲する。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import drive_storage

DATA_DIR = Path(__file__).parent / "data"
BILLINGS_FILE = DATA_DIR / "billings.json"

STATUS_UNPAID = "未入金"
STATUS_PAID = "入金済"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_all() -> list[dict]:
    drive_storage.restore_if_missing(BILLINGS_FILE, "billings.json")
    if not BILLINGS_FILE.exists():
        return []
    return json.loads(BILLINGS_FILE.read_text(encoding="utf-8"))


def _save_all(billings: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BILLINGS_FILE.write_text(json.dumps(billings, ensure_ascii=False, indent=2), encoding="utf-8")
    drive_storage.backup_file(BILLINGS_FILE, "billings.json")


def get_all_billings() -> list[dict]:
    return _load_all()


def get_billings_for_project(project_id: int) -> list[dict]:
    return [b for b in _load_all() if b["project_id"] == project_id]


def get_billing(billing_id: int) -> dict | None:
    return next((b for b in _load_all() if b["id"] == billing_id), None)


def add_billing(
    project_id: int,
    project_name: str,
    customer_name: str,
    ratio_percent: int,
    estimate_total: int,
    amount: int,
    billing_date: str,
    due_date: str,
    spreadsheet_id: str | None,
) -> dict:
    billings = _load_all()
    new_id = max((b["id"] for b in billings), default=0) + 1
    now = _now()
    billing = {
        "id": new_id,
        "project_id": project_id,
        "project_name": project_name,
        "customer_name": customer_name,
        "ratio_percent": ratio_percent,
        "estimate_total": estimate_total,
        "amount": amount,
        "billing_date": billing_date,
        "due_date": due_date,
        "spreadsheet_id": spreadsheet_id,
        "status": STATUS_UNPAID,
        "created_at": now,
        "updated_at": now,
    }
    billings.append(billing)
    _save_all(billings)
    return billing


def set_status(billing_id: int, status: str) -> None:
    billings = _load_all()
    for b in billings:
        if b["id"] == billing_id:
            b["status"] = status
            b["updated_at"] = _now()
            break
    _save_all(billings)
