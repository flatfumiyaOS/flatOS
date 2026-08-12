"""協力会社請求書（原価）データのローカルJSON保存用モジュール。

data/costs.json（一覧）と data/cost_files/<原価ID>_<ファイル名>（アップロード本体）に保存する。
project_store.py と同じ構成（_load_all/_save_all、連番ID）を踏襲する。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
COSTS_FILE = DATA_DIR / "costs.json"
COST_FILES_DIR = DATA_DIR / "cost_files"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_all() -> list[dict]:
    if not COSTS_FILE.exists():
        return []
    return json.loads(COSTS_FILE.read_text(encoding="utf-8"))


def _save_all(costs: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COSTS_FILE.write_text(json.dumps(costs, ensure_ascii=False, indent=2), encoding="utf-8")


def get_all_costs() -> list[dict]:
    return _load_all()


def get_costs_for_project(project_id: int) -> list[dict]:
    return [c for c in _load_all() if c["project_id"] == project_id]


def get_cost(cost_id: int) -> dict | None:
    return next((c for c in _load_all() if c["id"] == cost_id), None)


def _save_file(cost_id: int, filename: str, file_bytes: bytes) -> str:
    COST_FILES_DIR.mkdir(parents=True, exist_ok=True)
    target = COST_FILES_DIR / f"{cost_id}_{filename}"
    target.write_bytes(file_bytes)
    return str(target)


def add_cost(
    project_id: int,
    project_name: str,
    vendor_name: str,
    amount_tax_included: int,
    amount_tax_excluded: int,
    work_type: str,
    invoice_date: str,
    payment_month: str,
    file_bytes: bytes | None = None,
    file_name: str | None = None,
) -> dict:
    costs = _load_all()
    new_id = max((c["id"] for c in costs), default=0) + 1
    now = _now()
    file_path = None
    if file_bytes is not None and file_name:
        file_path = _save_file(new_id, file_name, file_bytes)
    cost = {
        "id": new_id,
        "project_id": project_id,
        "project_name": project_name,
        "vendor_name": vendor_name,
        "amount_tax_included": amount_tax_included,
        "amount_tax_excluded": amount_tax_excluded,
        "work_type": work_type,
        "invoice_date": invoice_date,
        "payment_month": payment_month,
        "paid": False,
        "file_path": file_path,
        "created_at": now,
        "updated_at": now,
    }
    costs.append(cost)
    _save_all(costs)
    return cost


def set_paid(cost_id: int, paid: bool) -> None:
    costs = _load_all()
    for c in costs:
        if c["id"] == cost_id:
            c["paid"] = paid
            c["updated_at"] = _now()
            break
    _save_all(costs)
