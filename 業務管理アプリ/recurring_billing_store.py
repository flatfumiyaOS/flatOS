"""定期請求（毎月/毎年決まった日に、見積書をもとに請求書を自動作成するルール）の
ローカルJSON保存用モジュール。project_store.py と同じ構成（_load_all/_save_all、
連番ID）を踏襲する。

Streamlitアプリには裏側で定期実行する仕組み（cron等）が無いため、実際の請求書生成は
「定期請求」ページや「会計・原価管理」ページを開いたタイミングで行う
（billing_generator.check_and_generate_due_recurring_billings）。このモジュールは
その判定に使う「まだ生成されていない直近の請求期はどれか」を返す is_due() を持つ。
"""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from pathlib import Path

import drive_storage

DATA_DIR = Path(__file__).parent / "data"
RECURRING_BILLINGS_FILE = DATA_DIR / "recurring_billings.json"

RECURRENCE_MONTHLY = "月一"
RECURRENCE_YEARLY = "年一"
RECURRENCE_OPTIONS = [RECURRENCE_MONTHLY, RECURRENCE_YEARLY]

STATUS_ACTIVE = "有効"
STATUS_PAUSED = "停止中"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_all() -> list[dict]:
    drive_storage.restore_if_missing(RECURRING_BILLINGS_FILE, "recurring_billings.json")
    if not RECURRING_BILLINGS_FILE.exists():
        return []
    return json.loads(RECURRING_BILLINGS_FILE.read_text(encoding="utf-8"))


def _save_all(records: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RECURRING_BILLINGS_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    drive_storage.backup_file(RECURRING_BILLINGS_FILE, "recurring_billings.json")


def get_all_recurring_billings() -> list[dict]:
    return _load_all()


def get_recurring_billing(recurring_billing_id: int) -> dict | None:
    return next((r for r in _load_all() if r["id"] == recurring_billing_id), None)


def add_recurring_billing(
    customer_id: int,
    customer_name: str,
    base_spreadsheet_id: str,
    recurrence_type: str,
    billing_day: int,
    billing_month: int | None,
) -> dict:
    records = _load_all()
    new_id = max((r["id"] for r in records), default=0) + 1
    now = _now()
    record = {
        "id": new_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "base_spreadsheet_id": base_spreadsheet_id,
        "recurrence_type": recurrence_type,
        "billing_day": billing_day,
        "billing_month": billing_month,
        "status": STATUS_ACTIVE,
        "last_generated_period": None,
        "created_at": now,
        "updated_at": now,
    }
    records.append(record)
    _save_all(records)
    return record


def set_status(recurring_billing_id: int, status: str) -> None:
    records = _load_all()
    for r in records:
        if r["id"] == recurring_billing_id:
            r["status"] = status
            r["updated_at"] = _now()
            break
    _save_all(records)


def set_last_generated_period(recurring_billing_id: int, period_key: str) -> None:
    records = _load_all()
    for r in records:
        if r["id"] == recurring_billing_id:
            r["last_generated_period"] = period_key
            r["updated_at"] = _now()
            break
    _save_all(records)


def _effective_day(year: int, month: int, day: int) -> int:
    """指定した年月において、dayがその月の末日を超えていれば末日に丸める
    （例: 31日指定で2月なら28日/29日になる）。"""
    return min(day, calendar.monthrange(year, month)[1])


def is_due(record: dict, today: date) -> tuple[str, date] | None:
    """本日時点で、この定期請求がまだ生成されていない直近の請求期に達していれば
    (period_key, その期の請求日) を返す。達していなければNoneを返す。

    period_keyは、月一なら"YYYY-MM"、年一なら"YYYY"。しばらくアプリを開かず複数期が
    未生成のまま溜まっていても、さかのぼって全部生成することはせず、直近1期分だけを
    対象にする（意図しない大量生成を避けるため）。
    """
    if record["recurrence_type"] == RECURRENCE_MONTHLY:
        day = _effective_day(today.year, today.month, record["billing_day"])
        candidate = date(today.year, today.month, day)
        if candidate > today:
            # 今月分はまだ請求日前 -> 前月分を対象にする
            year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            day = _effective_day(year, month, record["billing_day"])
            candidate = date(year, month, day)
        period_key = f"{candidate.year:04d}-{candidate.month:02d}"
    else:
        month = record["billing_month"]
        day = _effective_day(today.year, month, record["billing_day"])
        candidate = date(today.year, month, day)
        if candidate > today:
            # 今年分はまだ請求日前 -> 昨年分を対象にする
            year = today.year - 1
            day = _effective_day(year, month, record["billing_day"])
            candidate = date(year, month, day)
        period_key = f"{candidate.year:04d}"

    if record.get("last_generated_period") == period_key:
        return None
    return period_key, candidate
